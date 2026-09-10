import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { useLocalStorageState } from '@/hooks/useLocalStorageState'

import { Checkbox } from './Checkbox'
import { EmptyState } from './EmptyState'
import { ErrorState } from './ErrorState'
import { ArrowDownIcon, ArrowUpIcon, ColumnsIcon, DensityIcon, SortIcon } from './Icons'
import { Menu, MenuSection } from './Menu'
import { Skeleton } from './Skeleton'
import styles from './data.module.css'

export type Density = 'compact' | 'comfortable'

export interface Column<T> {
  id: string
  header: ReactNode
  cell: (row: T) => ReactNode
  /** Provide to make the column sortable. Null and undefined sort last. */
  sortValue?: (row: T) => string | number | null | undefined
  align?: 'left' | 'right'
  width?: string
  /**
   * A floor the table may not shrink below, unlike `width`, which auto layout
   * treats as a suggestion and overrides the moment the columns want more room
   * than the container has.
   *
   * For a column whose value is worthless partly shown - a uuid, a digest -
   * where the honest outcome of not enough room is a horizontal scrollbar
   * rather than four missing characters.
   */
  minWidth?: string
  /** Renders the cell in the mono face; use for every id, hash and timestamp. */
  mono?: boolean
  hideable?: boolean
  defaultHidden?: boolean
  /** Label used in card mode; defaults to the header. */
  cardLabel?: ReactNode
  /** Drop this column from the phone card view to keep it scannable. */
  hideOnMobile?: boolean
}

export interface SortState {
  columnId: string
  direction: 'asc' | 'desc'
}

export interface DataTableProps<T> {
  columns: Array<Column<T>>
  rows: T[] | undefined
  getRowId: (row: T) => string
  loading?: boolean
  error?: unknown
  onRetry?: () => void
  emptyTitle?: ReactNode
  emptyDescription?: ReactNode
  emptyAction?: ReactNode
  /** Persists density and hidden columns per table. */
  storageKey?: string
  defaultSort?: SortState
  onRowClick?: (row: T) => void
  selectedIds?: ReadonlySet<string>
  onSelectionChange?: (ids: Set<string>) => void
  /**
   * Flash the row once when this value changes - a status transition, not a
   * refreshed number. Polling must never animate the whole table.
   */
  flashValue?: (row: T) => string | number | null | undefined
  /** Extra controls on the left of the toolbar (filters, bulk actions). */
  toolbar?: ReactNode
  skeletonRows?: number
  caption?: string
  maxHeight?: string
  className?: string
}

interface PreferenceState {
  density: Density
  hidden: string[]
}

function compare(a: string | number | null | undefined, b: string | number | null | undefined): number {
  if (a === b) return 0
  if (a === null || a === undefined) return 1
  if (b === null || b === undefined) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b), undefined, { numeric: true })
}

/** Matches --duration-flash in tokens.css. */
const FLASH_MS = 900

/** Row ids whose flash value changed since the previous poll. */
function useRowFlashes<T>(
  rows: T[],
  getRowId: (row: T) => string,
  flashValue?: (row: T) => string | number | null | undefined,
): ReadonlySet<string> {
  const previous = useRef(new Map<string, unknown>())
  const [flashed, setFlashed] = useState<ReadonlySet<string>>(() => new Set<string>())
  // Callers pass inline arrows for getRowId/flashValue, so the effect below runs
  // on every render. The clear timer therefore lives in a ref rather than in the
  // effect cleanup: cleaning it up per run cancelled it before it ever fired and
  // the flash class stayed on the row for good.
  const timer = useRef(0)

  useEffect(
    () => () => {
      window.clearTimeout(timer.current)
    },
    [],
  )

  useEffect(() => {
    if (!flashValue) return

    const next = new Map<string, unknown>()
    const changed = new Set<string>()
    for (const row of rows) {
      const id = getRowId(row)
      const value = flashValue(row)
      next.set(id, value)
      if (previous.current.has(id) && !Object.is(previous.current.get(id), value)) {
        changed.add(id)
      }
    }

    const firstLoad = previous.current.size === 0
    previous.current = next
    if (firstLoad || changed.size === 0) return

    setFlashed(changed)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      setFlashed(new Set<string>())
    }, FLASH_MS)
  }, [rows, getRowId, flashValue])

  return flashed
}

/**
 * The console's workhorse table.
 *
 * Dense by default because an identity pool has dozens of rows and the request
 * log has thousands; comfortable is one click away. Under 768px it stops being a
 * table and becomes a list of cards, because a twelve-column grid on a phone is
 * unreadable and the phone is where the 3am alert lands.
 */
export function DataTable<T>({
  columns,
  rows,
  getRowId,
  loading = false,
  error,
  onRetry,
  emptyTitle,
  emptyDescription,
  emptyAction,
  storageKey,
  defaultSort,
  onRowClick,
  selectedIds,
  onSelectionChange,
  flashValue,
  toolbar,
  skeletonRows = 6,
  caption,
  maxHeight,
  className,
}: DataTableProps<T>) {
  const { t } = useTranslation()
  const isMobile = useIsMobile()

  const [preferences, setPreferences] = useLocalStorageState<PreferenceState>(
    `table.${storageKey ?? 'default'}`,
    {
      density: 'compact',
      hidden: columns.filter((column) => column.defaultHidden).map((column) => column.id),
    },
  )
  const [sort, setSort] = useState<SortState | null>(defaultSort ?? null)

  const hidden = useMemo(() => new Set(preferences.hidden), [preferences.hidden])
  const visibleColumns = useMemo(
    () => columns.filter((column) => !hidden.has(column.id)),
    [columns, hidden],
  )

  const data = useMemo(() => rows ?? [], [rows])
  const flashes = useRowFlashes(data, getRowId, flashValue)

  const sorted = useMemo(() => {
    if (!sort) return data
    const column = columns.find((entry) => entry.id === sort.columnId)
    if (!column?.sortValue) return data
    const factor = sort.direction === 'asc' ? 1 : -1
    return [...data].sort((a, b) => factor * compare(column.sortValue?.(a), column.sortValue?.(b)))
  }, [data, sort, columns])

  const selectable = Boolean(onSelectionChange)
  const selected = selectedIds ?? new Set<string>()
  const allSelected = sorted.length > 0 && sorted.every((row) => selected.has(getRowId(row)))
  const someSelected = !allSelected && sorted.some((row) => selected.has(getRowId(row)))

  const toggleAll = (): void => {
    if (!onSelectionChange) return
    onSelectionChange(allSelected ? new Set() : new Set(sorted.map(getRowId)))
  }

  const toggleRow = (id: string): void => {
    if (!onSelectionChange) return
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onSelectionChange(next)
  }

  /**
   * Props that make a clickable row reachable without a mouse.
   *
   * A row wired only to onClick is invisible to the keyboard, and on the log
   * tables the detail drawer has no other entry point at all - which fails the
   * "table row actions are keyboard operable" bar in docs/design/12. The target
   * check keeps Enter on a button or checkbox inside the row from also opening
   * the row. No role is set on <tr>: overriding it to button would drop the row
   * out of the table for a screen reader, and the row's own cells are its name.
   */
  const rowActivation = (
    row: T,
  ): {
    tabIndex: number
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => void
  } | null => {
    if (!onRowClick) return null
    return {
      tabIndex: 0,
      onKeyDown: (event) => {
        if (event.target !== event.currentTarget) return
        if (event.key !== 'Enter' && event.key !== ' ') return
        event.preventDefault()
        onRowClick(row)
      },
    }
  }

  const toggleSort = (column: Column<T>): void => {
    if (!column.sortValue) return
    setSort((current) => {
      if (current?.columnId !== column.id) return { columnId: column.id, direction: 'asc' }
      if (current.direction === 'asc') return { columnId: column.id, direction: 'desc' }
      return null
    })
  }

  const setDensity = (density: Density): void => {
    setPreferences((current) => ({ ...current, density }))
  }

  const toggleColumn = (id: string): void => {
    setPreferences((current) => {
      const next = new Set(current.hidden)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { ...current, hidden: [...next] }
    })
  }

  const controls = (
    <div className={styles.toolbar}>
      <div className={styles.toolbarGroup}>
        {toolbar}
        {selectable && selected.size > 0 ? (
          <span className={styles.toolbarCount}>{t('table.selected', { count: selected.size })}</span>
        ) : null}
      </div>
      <div className={styles.toolbarGroup}>
        <span className={styles.toolbarCount}>{t('table.rowCount', { count: sorted.length })}</span>
        <Menu
          label={t('table.density')}
          trigger={
            <>
              <DensityIcon />
              <span className="u-sr-only">{t('table.density')}</span>
            </>
          }
          role="group"
        >
          <MenuSection title={t('table.density')}>
            <Checkbox
              checked={preferences.density === 'compact'}
              onChange={() => {
                setDensity('compact')
              }}
              label={t('table.densityCompact')}
            />
            <Checkbox
              checked={preferences.density === 'comfortable'}
              onChange={() => {
                setDensity('comfortable')
              }}
              label={t('table.densityComfortable')}
            />
          </MenuSection>
        </Menu>
        <Menu
          label={t('table.columns')}
          trigger={
            <>
              <ColumnsIcon />
              <span className="u-sr-only">{t('table.columns')}</span>
            </>
          }
          role="group"
        >
          <MenuSection title={t('table.columnsHint')}>
            {columns
              .filter((column) => column.hideable !== false)
              .map((column) => (
                <Checkbox
                  key={column.id}
                  checked={!hidden.has(column.id)}
                  onChange={() => {
                    toggleColumn(column.id)
                  }}
                  label={column.cardLabel ?? column.header}
                />
              ))}
          </MenuSection>
        </Menu>
      </div>
    </div>
  )

  let body: ReactNode = null

  if (error) {
    body = <ErrorState error={error} onRetry={onRetry} />
  } else if (loading && sorted.length === 0) {
    body = isMobile ? (
      <div className={styles.cards}>
        {Array.from({ length: skeletonRows }, (_, index) => (
          <div className={styles.cardRow} key={index}>
            <Skeleton height={16} />
            <Skeleton width="60%" />
          </div>
        ))}
      </div>
    ) : (
      <div className={styles.scroller} style={maxHeight ? { maxHeight } : undefined}>
        <table className={cn(styles.table, preferences.density === 'comfortable' && styles.comfortable)}>
          {caption ? <caption className="u-sr-only">{caption}</caption> : null}
          <thead>
            <tr>
              {selectable ? <th className={cn(styles.headCell, styles.selectCell)} /> : null}
              {visibleColumns.map((column) => (
                <th
                  key={column.id}
                  className={styles.headCell}
                  style={{ width: column.width, minWidth: column.minWidth }}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody aria-busy="true">
            {Array.from({ length: skeletonRows }, (_, rowIndex) => (
              <tr key={rowIndex} className={styles.row}>
                {selectable ? <td className={cn(styles.cell, styles.selectCell)} /> : null}
                {visibleColumns.map((column) => (
                  <td key={column.id} className={styles.cell}>
                    <Skeleton width={column.align === 'right' ? '40%' : '70%'} height={10} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  } else if (sorted.length === 0) {
    body = (
      <EmptyState
        title={emptyTitle ?? t('table.emptyTitle')}
        description={emptyDescription ?? t('table.emptyDescription')}
        action={emptyAction}
      />
    )
  } else if (isMobile) {
    body = (
      <div className={styles.cards}>
        {sorted.map((row) => {
          const id = getRowId(row)
          return (
            <div
              key={id}
              className={cn(
                styles.cardRow,
                onRowClick && styles.cardRowClickable,
                flashes.has(id) && styles.flashRow,
                selected.has(id) && styles.rowSelected,
              )}
              onClick={onRowClick ? () => { onRowClick(row) } : undefined}
              role={onRowClick ? 'button' : undefined}
              {...(rowActivation(row) ?? {})}
            >
              {visibleColumns
                .filter((column) => !column.hideOnMobile)
                .map((column) => (
                  <div key={column.id} className={styles.cardField}>
                    <span className={styles.cardFieldLabel}>{column.cardLabel ?? column.header}</span>
                    <span className={cn(styles.cardFieldValue, column.mono && styles.cellMono)}>
                      {column.cell(row)}
                    </span>
                  </div>
                ))}
            </div>
          )
        })}
      </div>
    )
  } else {
    body = (
      <div className={styles.scroller} style={maxHeight ? { maxHeight } : undefined}>
        <table className={cn(styles.table, preferences.density === 'comfortable' && styles.comfortable)}>
          {caption ? <caption className="u-sr-only">{caption}</caption> : null}
          <thead>
            <tr>
              {selectable ? (
                <th className={cn(styles.headCell, styles.selectCell)}>
                  <Checkbox
                    checked={allSelected}
                    indeterminate={someSelected}
                    onChange={toggleAll}
                    aria-label={t('action.selectAll')}
                  />
                </th>
              ) : null}
              {visibleColumns.map((column) => {
                const active = sort?.columnId === column.id
                const ariaSort = active
                  ? sort?.direction === 'asc'
                    ? 'ascending'
                    : 'descending'
                  : 'none'
                return (
                  <th
                    key={column.id}
                    className={cn(styles.headCell, column.align === 'right' && styles.headCellRight)}
                    style={{ width: column.width, minWidth: column.minWidth }}
                    aria-sort={column.sortValue ? ariaSort : undefined}
                    scope="col"
                  >
                    {column.sortValue ? (
                      <button
                        type="button"
                        className={cn(
                          styles.headButton,
                          column.align === 'right' && styles.headButtonRight,
                        )}
                        onClick={() => {
                          toggleSort(column)
                        }}
                      >
                        <span>{column.header}</span>
                        <span className={cn(styles.sortIcon, active && styles.sortActive)}>
                          {active ? (
                            sort?.direction === 'asc' ? (
                              <ArrowUpIcon size={11} />
                            ) : (
                              <ArrowDownIcon size={11} />
                            )
                          ) : (
                            <SortIcon size={11} />
                          )}
                        </span>
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const id = getRowId(row)
              return (
                <tr
                  key={id}
                  className={cn(
                    styles.row,
                    onRowClick && styles.rowClickable,
                    selected.has(id) && styles.rowSelected,
                    flashes.has(id) && styles.flashRow,
                  )}
                  onClick={onRowClick ? () => { onRowClick(row) } : undefined}
                  {...(rowActivation(row) ?? {})}
                >
                  {selectable ? (
                    // The guard belongs on the cell, not on the input.
                    // `Checkbox` renders a label wrapping a visually hidden
                    // input and a styled span, and the span is what anyone
                    // actually clicks - so a click on it bubbled label -> td ->
                    // tr and opened the row, while the input's own
                    // stopPropagation only ever saw the synthetic click the
                    // label forwards. Ticking a box is not asking to open
                    // anything.
                    <td
                      className={cn(styles.cell, styles.selectCell)}
                      onClick={(event) => {
                        event.stopPropagation()
                      }}
                    >
                      <Checkbox
                        checked={selected.has(id)}
                        onChange={() => {
                          toggleRow(id)
                        }}
                        aria-label={id}
                      />
                    </td>
                  ) : null}
                  {visibleColumns.map((column) => (
                    <td
                      key={column.id}
                      className={cn(
                        styles.cell,
                        column.align === 'right' && styles.cellRight,
                        column.mono && styles.cellMono,
                      )}
                    >
                      {column.cell(row)}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className={cn(styles.tableWrap, className)}>
      {controls}
      {body}
    </div>
  )
}
