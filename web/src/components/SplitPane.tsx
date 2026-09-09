import {
  Children,
  Fragment,
  useCallback,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from 'react'

import { cn } from '@/lib/cn'
import { readStoredJson, writeStoredJson } from '@/lib/storage'
import { useElementSize } from '@/hooks/useElementSize'
import { useIsMobile } from '@/hooks/useMediaQuery'

import styles from './layout.module.css'

/**
 * Gutter track width, matching --space-2 in tokens.css. The grid template is
 * written from JS, so the number has to exist on this side too; changing one
 * without the other puts the drag math out by the difference.
 */
const GUTTER_PX = 8

/**
 * Default floor for a pane, matching --sidebar-width: the narrowest column this
 * console already ships and still expects to be readable.
 */
const DEFAULT_MIN_PX = 240

/** One arrow press moves the gutter by --space-4. */
const KEY_STEP_PX = 16

interface DragState {
  index: number
  pointerId: number
  originX: number
  /** Width available to the panes, measured once at pointerdown. */
  usable: number
  start: number[]
  pending: number[]
}

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(value, high))
}

/**
 * Percentages summing to 100, or null when the input is not a layout for
 * `count` panes. Stored layouts are read back after the page that wrote them has
 * changed shape, so a two-pane array arriving at a three-pane split has to fall
 * back rather than render a column of width undefined.
 */
function toPercentages(value: unknown, count: number): number[] | null {
  if (!Array.isArray(value) || value.length !== count) return null
  const entries: number[] = []
  let total = 0
  for (const entry of value) {
    if (typeof entry !== 'number' || !Number.isFinite(entry) || entry <= 0) return null
    entries.push(entry)
    total += entry
  }
  if (total <= 0) return null
  return entries.map((entry) => (entry / total) * 100)
}

function evenSplit(count: number): number[] {
  return Array.from({ length: count }, () => 100 / count)
}

export interface SplitPaneProps {
  /** Two or three panes, in visual order. Each one scrolls independently. */
  children: ReactNode
  /** Per-instance key; the layout is persisted under `dtk.split.<storageKey>`. */
  storageKey: string
  /** Accessible name per gutter; index 0 is the gutter between panes 0 and 1. */
  gutterLabels: string[]
  /** Starting widths, any units - they are normalized to percentages. Defaults to an even split. */
  defaultSizes?: number[]
  /** Minimum width per pane in px. Defaults to DEFAULT_MIN_PX. */
  minSizes?: number[]
  /** Fires on every drag frame and key press with the live percentages. */
  onSizesChange?: (sizes: number[]) => void
  className?: string
  paneClassName?: string
}

/**
 * Horizontal split with draggable gutters.
 *
 * Pointer events rather than mouse events, and pointer capture rather than
 * document listeners: capture keeps the drag alive when the cursor outruns the
 * 8px gutter or leaves the window, and the same handler then serves a trackpad,
 * a pen and a touchscreen without a second code path.
 *
 * Below MOBILE_QUERY - the same 768px boundary at which the sidebar becomes a
 * drawer and DataTable becomes cards - the panes stack and the gutters are gone
 * entirely. An 8px drag target is not a target on a phone, and three columns on
 * a 375px screen is not a layout.
 */
export function SplitPane({
  children,
  storageKey,
  gutterLabels,
  defaultSizes,
  minSizes,
  onSizesChange,
  className,
  paneClassName,
}: SplitPaneProps) {
  const isMobile = useIsMobile()
  const panes = Children.toArray(children)
  const count = panes.length
  const baseId = useId()
  const key = `split.${storageKey}`

  const defaults = toPercentages(defaultSizes, count) ?? evenSplit(count)
  const [sizes, setSizes] = useState<number[]>(
    // localStorage throws in a private window and wherever site data is
    // blocked; lib/storage swallows that and hands back the fallback.
    () => toPercentages(readStoredJson<unknown>(key, null), count) ?? defaults,
  )
  const [activeGutter, setActiveGutter] = useState<number | null>(null)
  const drag = useRef<DragState | null>(null)

  const [sizeRef, size] = useElementSize<HTMLDivElement>()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const setContainer = useCallback(
    (node: HTMLDivElement | null) => {
      containerRef.current = node
      sizeRef(node)
    },
    [sizeRef],
  )

  const usableWidth = Math.max(size.width - GUTTER_PX * Math.max(count - 1, 0), 0)

  const apply = (next: number[]): void => {
    setSizes(next)
    onSizesChange?.(next)
  }

  const persist = (next: number[]): void => {
    writeStoredJson(key, next)
  }

  const minPercentAt = (index: number, usable: number): number => {
    if (usable <= 0) return 0
    const px = minSizes?.[index] ?? DEFAULT_MIN_PX
    // A container narrower than the sum of the minimums has no solution. Capping
    // each floor at an equal share keeps the clamp below solvable instead of
    // pinning the gutter where it stands.
    return Math.min((px / usable) * 100, 100 / count)
  }

  /** Moves gutter `index` by a percentage, taking the width out of its right-hand neighbour only. */
  const withDelta = (
    base: number[],
    index: number,
    deltaPercent: number,
    usable: number,
  ): number[] | null => {
    const before = base[index]
    const after = base[index + 1]
    if (before === undefined || after === undefined) return null
    const pair = before + after
    const next = clamp(
      before + deltaPercent,
      minPercentAt(index, usable),
      pair - minPercentAt(index + 1, usable),
    )
    const result = [...base]
    result[index] = next
    result[index + 1] = pair - next
    return result
  }

  const onGutterPointerDown = (index: number, event: PointerEvent<HTMLDivElement>): void => {
    const container = containerRef.current
    if (!container || event.button !== 0) return
    const usable = container.getBoundingClientRect().width - GUTTER_PX * (count - 1)
    if (usable <= 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = {
      index,
      pointerId: event.pointerId,
      originX: event.clientX,
      usable,
      start: sizes,
      pending: sizes,
    }
    setActiveGutter(index)
  }

  const onGutterPointerMove = (event: PointerEvent<HTMLDivElement>): void => {
    const state = drag.current
    if (!state || state.pointerId !== event.pointerId) return
    const delta = ((event.clientX - state.originX) / state.usable) * 100
    const next = withDelta(state.start, state.index, delta, state.usable)
    if (!next) return
    state.pending = next
    apply(next)
  }

  const endDrag = (event: PointerEvent<HTMLDivElement>): void => {
    const state = drag.current
    if (!state || state.pointerId !== event.pointerId) return
    drag.current = null
    setActiveGutter(null)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    // A pointermove fires up to ~120 times a second; the layout is serialized
    // once, when the drag ends.
    persist(state.pending)
  }

  const reset = (): void => {
    apply(defaults)
    persist(defaults)
  }

  const onGutterKeyDown = (index: number, event: KeyboardEvent<HTMLDivElement>): void => {
    const step = usableWidth > 0 ? (KEY_STEP_PX / usableWidth) * 100 : 0
    let next: number[] | null = null
    switch (event.key) {
      case 'ArrowLeft':
        next = withDelta(sizes, index, -step, usableWidth)
        break
      case 'ArrowRight':
        next = withDelta(sizes, index, step, usableWidth)
        break
      case 'Home':
        next = withDelta(sizes, index, -100, usableWidth)
        break
      case 'End':
        next = withDelta(sizes, index, 100, usableWidth)
        break
      // The double-click reset is mouse-only; Enter is its keyboard half.
      case 'Enter':
        next = defaults
        break
      default:
        return
    }
    if (!next) return
    event.preventDefault()
    apply(next)
    persist(next)
  }

  if (isMobile) {
    return (
      <div className={cn(styles.splitStack, className)}>
        {panes.map((pane, index) => (
          <div key={index} className={cn(styles.splitStackPane, paneClassName)}>
            {pane}
          </div>
        ))}
      </div>
    )
  }

  const tracks: string[] = []
  for (let index = 0; index < count; index += 1) {
    if (index > 0) tracks.push(`${GUTTER_PX}px`)
    tracks.push(`minmax(0, ${(sizes[index] ?? 0).toFixed(4)}fr)`)
  }

  return (
    <div
      ref={setContainer}
      className={cn(styles.split, activeGutter !== null && styles.splitDragging, className)}
      style={{ gridTemplateColumns: tracks.join(' ') }}
    >
      {panes.map((pane, index) => {
        const gutter = index - 1
        const before = sizes[gutter] ?? 0
        const after = sizes[index] ?? 0
        return (
          <Fragment key={index}>
            {index > 0 ? (
              <div
                role="separator"
                tabIndex={0}
                aria-orientation="vertical"
                aria-label={gutterLabels[gutter]}
                aria-controls={`${baseId}-pane-${gutter}`}
                aria-valuenow={Math.round(before)}
                aria-valuemin={Math.round(minPercentAt(gutter, usableWidth))}
                aria-valuemax={Math.round(before + after - minPercentAt(index, usableWidth))}
                className={cn(styles.gutter, activeGutter === gutter && styles.gutterActive)}
                onPointerDown={(event) => {
                  onGutterPointerDown(gutter, event)
                }}
                onPointerMove={onGutterPointerMove}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
                onDoubleClick={reset}
                onKeyDown={(event) => {
                  onGutterKeyDown(gutter, event)
                }}
              />
            ) : null}
            <div id={`${baseId}-pane-${index}`} className={cn(styles.pane, paneClassName)}>
              {pane}
            </div>
          </Fragment>
        )
      })}
    </div>
  )
}
