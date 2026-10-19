import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Banner,
  Button,
  Card,
  DataTable,
  Disclosure,
  ErrorState,
  Input,
  MetricTile,
  PageHeader,
  RefreshIcon,
  ServerIcon,
  SettingEditor,
  Skeleton,
  StatusBadge,
  sourceOf,
  useToast,
  type Column,
  type SettingRow,
} from '@/components'
import {
  useApiMutation,
  useApiQuery,
  useFormatters,
  useInvalidate,
  usePlatformName,
  useSession,
} from '@/hooks'
import { apiPut } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { atLeast } from '@/lib/roles'
import { IDENTITY_STATES, PLATFORMS, type IdentityState, type Platform } from '@/lib/types'

import { CoverageCard } from './schedulerCoverage'

/**
 * The scheduler, as a page rather than as two headings in a settings list.
 *
 * Every number here was already editable under Settings, which is exactly the
 * problem: `sched.max_wait_seconds` next to `retention.task_days` is a list of
 * unrelated integers, and nothing on that page said what any of them do to the
 * one question an operator actually has - which identity serves the next
 * request, and what stops one of them serving all of them. So the numbers moved
 * to where the answer is, beside the pool they act on and beside a description
 * of the rotation they tune.
 *
 * The rotation description is deliberately honest about its own limits. The
 * ordering was measured against a uniform random baseline and is no better than
 * chance; what the scheduler actually guarantees is exclusivity and quota, and
 * saying "every cookie takes turns" without that qualification would be
 * claiming a property this code does not have (see
 * tests/integration/test_scheduler_fairness.py).
 */

const SETTINGS_KEY = ['admin', 'settings'] as const
const STATUS_KEY = ['system', 'status'] as const

interface SettingsResponse {
  version: number
  settings: SettingRow[]
}

interface SystemStatusLike {
  pool?: unknown
  /** Active identities kept out of rotation (issue 789), per platform. */
  reserved_active?: Record<string, number> | null
}

type PoolCounts = Record<IdentityState, number>

const EMPTY_POOL: PoolCounts = {
  minting: 0,
  active: 0,
  cooling: 0,
  degraded: 0,
  retired: 0,
}

/** One platform's census, as the status route reports it under `pool.<platform>`. */
function platformCounts(pool: unknown, platform: Platform): PoolCounts {
  const counts: PoolCounts = { ...EMPTY_POOL }
  if (typeof pool !== 'object' || pool === null) return counts
  const entry = (pool as Record<string, unknown>)[platform]
  if (typeof entry !== 'object' || entry === null) return counts
  for (const state of IDENTITY_STATES) {
    const value = (entry as Record<string, unknown>)[state]
    if (typeof value === 'number') counts[state] = value
  }
  return counts
}

interface PlatformCensus {
  platform: Platform
  /** Active and in rotation: what the next unpinned request can be given. */
  schedulable: number
  reserved: number
  cooling: number
  degraded: number
}

/**
 * Per platform, with reserved identities taken out of "schedulable".
 *
 * It used to add `active` up across all three platforms and call the sum
 * "schedulable right now". Neither half held: a Douyin request cannot be
 * served by a Bilibili identity, and an identity kept for the requests that
 * name it (issue 789) is active without being anything rotation will hand out.
 */
function census(status: SystemStatusLike | undefined): PlatformCensus[] {
  return PLATFORMS.map((platform) => {
    const counts = platformCounts(status?.pool, platform)
    const reserved = Math.min(counts.active, status?.reserved_active?.[platform] ?? 0)
    return {
      platform,
      schedulable: counts.active - reserved,
      reserved,
      cooling: counts.cooling,
      degraded: counts.degraded,
    }
  })
}

/** The two prefixes this page owns. Settings shows everything else. */
const SECTIONS = ['sched', 'pool'] as const
type Section = (typeof SECTIONS)[number]

/**
 * `pool.<platform>.min_size` and `.target_size`: shown as one grid below
 * rather than as six rows of -1 among the global marks, where "-1" read as a
 * value someone had chosen rather than "use the global one".
 */
const PER_PLATFORM_MARK = /^pool\.[a-z]+\.(min_size|target_size)$/
/** `pool.<platform>.guests_first`: a switch on the coverage card, not a row here. */
const PER_PLATFORM_LOGINS = /^pool\.[a-z]+\.guests_first$/
const MARK_FIELDS = ['min_size', 'target_size'] as const
type MarkField = (typeof MARK_FIELDS)[number]
/** The value that means "inherit the global mark" (src/dtk/core/config.py). */
const INHERIT = -1

/** The stages a request passes through, in order, each with what it enforces. */
const STAGES = ['circuit', 'rank', 'inflight', 'quota', 'outcome'] as const

export default function Scheduler() {
  const { t } = useTranslation(['console', 'common'])
  const platformName = usePlatformName()
  const format = useFormatters()
  const invalidate = useInvalidate()
  const session = useSession()

  const role = session.data?.role ?? null
  // The settings route is read_admin and this page is on the demo account's
  // menu. Asking anyway put an error where the settings cards belong; the
  // census and the rotation are what the demo can read, so that is the page.
  const canReadSettings = atLeast(role, 'viewer')
  const canWrite = atLeast(role, 'operator')
  const canWriteSensitive = atLeast(role, 'admin')

  const query = useApiQuery<SettingsResponse>({
    key: SETTINGS_KEY,
    path: paths.settings.list,
    poll: POLL.slow,
    enabled: canReadSettings,
  })

  const status = useApiQuery<SystemStatusLike>({
    key: STATUS_KEY,
    path: paths.system.status,
    poll: POLL.fast,
  })

  const sections = useMemo(() => {
    const rows = query.data?.settings ?? []
    return SECTIONS.map((section) => ({
      section,
      rows: rows.filter(
        (row) =>
          row.key.startsWith(`${section}.`) &&
          !PER_PLATFORM_MARK.test(row.key) &&
          !PER_PLATFORM_LOGINS.test(row.key),
      ),
    })).filter((entry) => entry.rows.length > 0)
  }, [query.data])

  const overrides = useMemo(
    () =>
      (query.data?.settings ?? []).filter(
        (row) =>
          SECTIONS.some((section) => row.key.startsWith(`${section}.`)) &&
          sourceOf(row) === 'database',
      ).length,
    [query.data],
  )

  const platforms = useMemo(() => census(status.data), [status.data])
  const totals = platforms.reduce(
    (sum, row) => ({
      schedulable: sum.schedulable + row.schedulable,
      reserved: sum.reserved + row.reserved,
      cooling: sum.cooling + row.cooling,
      degraded: sum.degraded + row.degraded,
    }),
    { schedulable: 0, reserved: 0, cooling: 0, degraded: 0 },
  )

  const refresh = (): void => {
    void invalidate(SETTINGS_KEY)
    void invalidate(STATUS_KEY)
  }

  const censusColumns: Array<Column<PlatformCensus>> = [
    {
      id: 'platform',
      header: t('console:field.platform'),
      hideable: false,
      cell: (row) => platformName(row.platform),
    },
    {
      id: 'schedulable',
      header: t('scheduler.census.schedulable'),
      align: 'right',
      mono: true,
      hideable: false,
      cell: (row) => format.number(row.schedulable),
      sortValue: (row) => row.schedulable,
    },
    {
      id: 'reserved',
      header: t('scheduler.census.reserved'),
      align: 'right',
      mono: true,
      cell: (row) =>
        row.reserved > 0 ? format.number(row.reserved) : <span className="u-muted">0</span>,
      sortValue: (row) => row.reserved,
    },
    {
      id: 'cooling',
      header: <StatusBadge kind="identity" value="cooling" size="sm" flash={false} />,
      align: 'right',
      mono: true,
      cell: (row) => format.number(row.cooling),
      sortValue: (row) => row.cooling,
    },
    {
      id: 'degraded',
      header: <StatusBadge kind="identity" value="degraded" size="sm" flash={false} />,
      align: 'right',
      mono: true,
      cell: (row) => format.number(row.degraded),
      sortValue: (row) => row.degraded,
    },
  ]

  const tiles = [
    {
      state: 'active' as const,
      value: totals.schedulable,
      footer:
        totals.reserved > 0
          ? t('scheduler.pool.activeReserved', { count: totals.reserved })
          : t('scheduler.pool.active'),
    },
    { state: 'cooling' as const, value: totals.cooling, footer: t('scheduler.pool.cooling') },
    { state: 'degraded' as const, value: totals.degraded, footer: t('scheduler.pool.degraded') },
  ]

  return (
    <div className="u-page">
      <PageHeader
        title={t('page.scheduler.title')}
        description={t('page.scheduler.description')}
        actions={
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshIcon />}
            onClick={refresh}
            loading={query.isFetching && !query.isLoading}
          >
            {t('common:action.refresh')}
          </Button>
        }
      />

      {/* What the pool looks like right now, because every number below is a
          statement about this census and reading them apart from it is how a
          low-water mark gets set to a value the pool has never been near. */}
      <div className="u-grid-metrics">
        {tiles.map((tile) => (
          <MetricTile
            key={tile.state}
            label={
              <span className="u-row">
                <StatusBadge kind="identity" value={tile.state} size="sm" flash={false} />
              </span>
            }
            value={status.isLoading ? '—' : String(tile.value)}
            loading={status.isLoading}
            footer={<span className="u-xs u-muted">{tile.footer}</span>}
          />
        ))}
      </div>

      <Card
        title={t('scheduler.census.title')}
        description={t('scheduler.census.description')}
        flush
      >
        <DataTable
          columns={censusColumns}
          rows={platforms}
          getRowId={(row) => row.platform}
          loading={status.isLoading}
          error={status.isError ? status.error : undefined}
          onRetry={() => {
            void status.refetch()
          }}
          storageKey="scheduler-census"
          caption={t('scheduler.census.title')}
        />
      </Card>

      {/* Viewer and up, like the settings below: the route is read_admin and
          this page is on the demo account's menu. */}
      {canReadSettings ? <CoverageCard canWrite={canWrite} /> : null}

      <Card
        title={t('scheduler.rotationTitle')}
        description={t('scheduler.rotationDescription')}
      >
        <ol
          className="u-stack-sm"
          style={{ margin: 0, paddingInlineStart: 'var(--space-5)', listStyle: 'decimal' }}
        >
          {STAGES.map((stage) => (
            <li key={stage}>
              <span>{t(`scheduler.stage.${stage}`)}</span>
            </li>
          ))}
        </ol>
      </Card>

      <Banner tone="accent" icon={<ServerIcon size={14} />}>
        <div className="u-stack-sm">
          <span>{t('scheduler.fairness')}</span>
          <span className="u-xs u-muted">{t('scheduler.fairnessMeasured')}</span>
        </div>
      </Banner>

      {!canReadSettings ? null : query.isLoading ? (
        <Card>
          <div className="u-stack-sm" aria-busy="true">
            <Skeleton height={18} width="30%" />
            <Skeleton height={44} />
            <Skeleton height={44} />
          </div>
        </Card>
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={refresh} />
      ) : (
        <>
          {sections.map(({ section, rows }) => (
            <Card
              key={section}
              title={t(`settings.group.${section}`)}
              description={t(`scheduler.section.${section}`)}
            >
              <div>
                {rows.map((row) => (
                  <SettingEditor
                    key={row.key}
                    row={row}
                    canWrite={canWrite}
                    canWriteSensitive={canWriteSensitive}
                    onSaved={refresh}
                  />
                ))}
              </div>
            </Card>
          ))}
          <PoolMarksGrid
            settings={query.data?.settings ?? []}
            canWrite={canWrite}
            onSaved={refresh}
          />
        </>
      )}

      {canReadSettings ? (
        <Card flush>
          <Disclosure title={t('scheduler.overridesTitle')}>
            <p className="u-secondary" style={{ margin: 0 }}>
              {t('scheduler.overrides', { count: overrides })}
            </p>
          </Disclosure>
        </Card>
      ) : null}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Per-platform marks                                                          */
/* -------------------------------------------------------------------------- */

interface PoolMarksGridProps {
  settings: SettingRow[]
  canWrite: boolean
  onSaved: () => void
}

/** An empty cell inherits; anything else must be a whole number of 0 or more. */
function parseMark(draft: string): number | null {
  const text = draft.trim()
  if (text === '') return INHERIT
  if (!/^\d+$/.test(text)) return null
  return Number(text)
}

function markKey(platform: string, field: MarkField): string {
  return `pool.${platform}.${field}`
}

/**
 * `pool.<platform>.min_size` and `target_size` as a grid, with "inherit the
 * global mark" spelled out as an empty cell showing the value it inherits.
 *
 * One Save for the grid: the filler reads a low-water mark and a target as a
 * pair, and editing them one row at a time is how a target ends up below its
 * own floor between two clicks.
 */
function PoolMarksGrid({ settings, canWrite, onSaved }: PoolMarksGridProps) {
  const { t } = useTranslation(['console', 'common'])
  const platformName = usePlatformName()
  const toast = useToast()
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  const byKey = useMemo(() => new Map(settings.map((row) => [row.key, row])), [settings])
  const platforms = PLATFORMS.filter((platform) =>
    MARK_FIELDS.every((field) => byKey.has(markKey(platform, field))),
  )
  const globals: Record<MarkField, unknown> = {
    min_size: byKey.get('pool.min_size')?.value,
    target_size: byKey.get('pool.target_size')?.value,
  }

  const shown = (key: string): string => {
    if (key in drafts) return drafts[key] ?? ''
    const value = byKey.get(key)?.value
    return typeof value === 'number' && value !== INHERIT ? String(value) : ''
  }

  const changes = Object.entries(drafts).flatMap(([key, draft]) => {
    const row = byKey.get(key)
    const value = parseMark(draft)
    return row && value !== null && value !== row.value ? [{ row, value }] : []
  })
  const invalid = Object.values(drafts).some((draft) => parseMark(draft) === null)

  const save = useApiMutation<unknown, void>(
    async () => {
      for (const { row, value } of changes) {
        // "Inherit" is stored as -1 rather than reached by deleting the
        // override: a delete falls back to the environment seed, and this page
        // cannot see whether one is set - a cleared cell would then quietly
        // mean the seed's number instead of the global mark it shows.
        await apiPut(paths.settings.byKey(row.key), { value, confirm: false })
      }
      return null
    },
    {
      onSuccess: () => {
        setDrafts({})
        toast.success(t('scheduler.marks.saved'))
        onSaved()
      },
      onError: (error) => {
        // Some may have landed before the one that failed; the refresh shows
        // which, and the drafts stay so the rest can be retried.
        toast.apiError(error, t('scheduler.marks.saveFailed'))
        onSaved()
      },
    },
  )

  if (platforms.length === 0) return null

  return (
    <Card
      title={t('scheduler.marks.title')}
      description={t('scheduler.marks.description')}
      footer={
        canWrite ? (
          <div className="u-row u-wrap">
            <Button
              size="sm"
              variant="primary"
              disabled={changes.length === 0 || invalid}
              loading={save.isPending}
              onClick={() => {
                save.mutate()
              }}
            >
              {t('scheduler.marks.save', { count: changes.length })}
            </Button>
            {Object.keys(drafts).length > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={save.isPending}
                onClick={() => {
                  setDrafts({})
                }}
              >
                {t('common:action.cancel')}
              </Button>
            ) : null}
          </div>
        ) : (
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('settings.readOnly')}
          </p>
        )
      }
    >
      <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
        <caption className="u-sr-only">{t('scheduler.marks.title')}</caption>
        <thead>
          <tr>
            <th scope="col" className="u-xs u-muted" style={{ textAlign: 'start', width: '28%' }}>
              {t('console:field.platform')}
            </th>
            {MARK_FIELDS.map((field) => (
              <th key={field} scope="col" className="u-xs u-muted" style={{ textAlign: 'start' }}>
                {t(`scheduler.marks.${field}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {platforms.map((platform) => (
            <tr key={platform}>
              <th
                scope="row"
                style={{ textAlign: 'start', fontWeight: 400, padding: 'var(--space-2) 0' }}
              >
                {platformName(platform)}
              </th>
              {MARK_FIELDS.map((field) => {
                const key = markKey(platform, field)
                const value = shown(key)
                const broken = key in drafts && parseMark(drafts[key] ?? '') === null
                return (
                  <td key={field} style={{ padding: 'var(--space-2) var(--space-2) var(--space-2) 0' }}>
                    <Input
                      mono
                      inputMode="numeric"
                      value={value}
                      disabled={!canWrite || save.isPending}
                      placeholder={t('scheduler.marks.inherit', {
                        value: String(globals[field] ?? '-'),
                      })}
                      aria-label={t('scheduler.marks.cellLabel', {
                        platform,
                        field: t(`scheduler.marks.${field}`),
                      })}
                      error={broken ? t('scheduler.marks.invalid') : undefined}
                      onChange={(event) => {
                        const next = event.target.value
                        setDrafts((current) => ({ ...current, [key]: next }))
                      }}
                    />
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

/** Section labels, kept beside the sections they name. */
export type SchedulerSection = Section
