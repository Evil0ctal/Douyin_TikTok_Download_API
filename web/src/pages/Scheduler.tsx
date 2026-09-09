import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Banner,
  Button,
  Card,
  Disclosure,
  ErrorState,
  MetricTile,
  PageHeader,
  RefreshIcon,
  ServerIcon,
  SettingEditor,
  Skeleton,
  StatusBadge,
  sourceOf,
  type SettingRow,
} from '@/components'
import { useApiQuery, useInvalidate, useSession } from '@/hooks'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { IDENTITY_STATES, type IdentityState } from '@/lib/types'

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
}

type PoolCounts = Record<IdentityState, number>

const EMPTY_POOL: PoolCounts = {
  minting: 0,
  active: 0,
  cooling: 0,
  degraded: 0,
  retired: 0,
}

/** Sums identity counts out of a flat census or one census per platform. */
function poolCounts(pool: unknown, depth = 0): PoolCounts {
  const totals: PoolCounts = { ...EMPTY_POOL }
  if (depth > 3 || typeof pool !== 'object' || pool === null) return totals
  for (const [key, value] of Object.entries(pool as Record<string, unknown>)) {
    if (typeof value === 'number') {
      if ((IDENTITY_STATES as readonly string[]).includes(key)) {
        totals[key as IdentityState] += value
      }
      continue
    }
    const nested = poolCounts(value, depth + 1)
    for (const state of IDENTITY_STATES) totals[state] += nested[state]
  }
  return totals
}

/** The two prefixes this page owns. Settings shows everything else. */
const SECTIONS = ['sched', 'pool'] as const
type Section = (typeof SECTIONS)[number]

/** The stages a request passes through, in order, each with what it enforces. */
const STAGES = ['circuit', 'rank', 'inflight', 'quota', 'outcome'] as const

export default function Scheduler() {
  const { t } = useTranslation(['console', 'common'])
  const invalidate = useInvalidate()
  const session = useSession()

  const query = useApiQuery<SettingsResponse>({
    key: SETTINGS_KEY,
    path: paths.settings.list,
    poll: POLL.slow,
  })

  const status = useApiQuery<SystemStatusLike>({
    key: STATUS_KEY,
    path: paths.system.status,
    poll: POLL.fast,
  })

  const role = session.data?.role ?? null
  // With no session payload the server is still the authority; the console
  // shows the controls and surfaces its refusal rather than locking a page
  // nobody can then explain.
  const canWrite = role === null || role !== 'viewer'
  const canWriteSensitive = role === null || role === 'admin'

  const sections = useMemo(() => {
    const rows = query.data?.settings ?? []
    return SECTIONS.map((section) => ({
      section,
      rows: rows.filter((row) => row.key.startsWith(`${section}.`)),
    })).filter((entry) => entry.rows.length > 0)
  }, [query.data])

  const overrides = useMemo(
    () =>
      sections
        .flatMap((entry) => entry.rows)
        .filter((row) => sourceOf(row) === 'database').length,
    [sections],
  )

  const pool = poolCounts(status.data?.pool)
  const refresh = (): void => {
    void invalidate(SETTINGS_KEY)
    void invalidate(STATUS_KEY)
  }

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
        {(['active', 'cooling', 'degraded'] as const).map((state) => (
          <MetricTile
            key={state}
            label={
              <span className="u-row">
                <StatusBadge kind="identity" value={state} size="sm" flash={false} />
              </span>
            }
            value={status.isLoading ? '—' : String(pool[state])}
            loading={status.isLoading}
            footer={<span className="u-xs u-muted">{t(`scheduler.pool.${state}`)}</span>}
          />
        ))}
      </div>

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

      {query.isLoading ? (
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
        sections.map(({ section, rows }) => (
          <Card
            key={section}
            title={t(`settings.group.${section}`)}
            description={t(`scheduler.section.${section}`)}
            flush
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
        ))
      )}

      <Card flush>
        <Disclosure title={t('scheduler.overridesTitle')}>
          <p className="u-secondary" style={{ margin: 0 }}>
            {t('scheduler.overrides', { count: overrides })}
          </p>
        </Disclosure>
      </Card>
    </div>
  )
}

/** Section labels, kept beside the sections they name. */
export type SchedulerSection = Section
