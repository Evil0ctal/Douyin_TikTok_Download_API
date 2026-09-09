import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation } from 'wouter'

import {
  AlertIcon,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  GaugeIcon,
  MetricTile,
  PageHeader,
  Select,
  StatusBadge,
  TimeSeriesChart,
  type ChartSeries,
  type Column,
} from '@/components'
import { paths } from '@/lib/endpoints'
import { MISSING } from '@/lib/format'
import { POLL } from '@/lib/query'
import { IDENTITY_STATES, type IdentityState, type Outcome } from '@/lib/types'
import { useApiQuery, useFormatters } from '@/hooks'

/* -------------------------------------------------------------------------- */
/* API shapes                                                                  */
/* -------------------------------------------------------------------------- */

/** GET /api/v1/system/status - pool is a census per platform plus a total. */
interface SystemStatusResponse {
  version: string
  commit: string | null
  uptime_seconds: number
  pool: Record<string, Record<string, number> | number>
}

/** GET /api/v1/admin/endpoints/health - every declared endpoint, traffic or not. */
interface EndpointHealthRow {
  endpoint: string
  platform: string
  circuit_open: boolean
  retry_after: number | null
  reason: string | null
  window_seconds: number
  samples: number
  success_rate: number | null
  risk_rate: number | null
  distinct_risk_identities: number
}

/** GET /api/v1/admin/metrics/timeseries - raw buckets; the shaping happens here. */
interface TimeseriesResponse {
  window_hours: number
  step_seconds: number
  points: Array<{
    bucket: string
    endpoint: string
    outcome: Outcome
    requests: number
    avg_duration_ms: number | null
  }>
}

interface RangeOption {
  id: string
  hours: number
  step: number
}

/** Windows the console offers. 24h at five minutes is 288 points, which draws instantly. */
const RANGES: readonly RangeOption[] = [
  { id: '1h', hours: 1, step: 60 },
  { id: '6h', hours: 6, step: 300 },
  { id: '24h', hours: 24, step: 300 },
  { id: '7d', hours: 168, step: 1800 },
]

/** How many endpoint series the per-endpoint chart will draw. */
const MAX_ENDPOINT_SERIES = 6

/**
 * Below this, a success rate stops being reassuring. One constant so the tile
 * and the per-endpoint table cannot disagree about what "fine" means.
 */
const SUCCESS_RATE_FLOOR = 0.9

interface Bucket {
  ts: number
  total: number
  ok: number
  risk: number
  business: number
  network: number
  durationSum: number
  durationWeight: number
}

/* -------------------------------------------------------------------------- */
/* Page                                                                        */
/* -------------------------------------------------------------------------- */

/**
 * The "is this thing healthy right now" page.
 *
 * Everything refreshes by polling every five to eight seconds and nothing
 * animates on refresh: a table that fades on every poll cannot be read, and the
 * only change worth a highlight is a status that actually changed
 * (docs/design/12-design-system.md).
 */
interface ArchiveTotals {
  contents: number
  authors: number
  stored: number
}

interface StorageTotals {
  bytes_total: number
  max_bytes: number
  in_flight: number
  by_state?: Record<string, number>
  downloader?: { volume_bytes?: number } | null
}

interface WatchlistTotals {
  total: number
  stats?: { failing?: number } | null
}

/** Quiet until the volume is nearly full, loud once it is. */
function diskTone(storage: StorageTotals | undefined): 'warning' | 'danger' | undefined {
  const ceiling = storage?.max_bytes ?? 0
  if (!storage || ceiling <= 0) return undefined
  const used = storage.downloader?.volume_bytes ?? storage.bytes_total ?? 0
  const share = used / ceiling
  if (share >= 0.9) return 'danger'
  if (share >= 0.75) return 'warning'
  return undefined
}

export default function Overview() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const [, navigate] = useLocation()

  const [rangeId, setRangeId] = useState('24h')
  const [endpointFilter, setEndpointFilter] = useState('')

  const range = RANGES.find((option) => option.id === rangeId) ?? RANGES[2]

  const system = useApiQuery<SystemStatusResponse>({
    key: ['system', 'status'],
    path: paths.system.status,
    poll: POLL.fast,
  })

  /**
   * What this instance has kept, as against what it has asked for.
   *
   * Every tile above is about requests - how many, how well, how fast - and
   * none of them says what the thing is actually accumulating. "66 archived,
   * 8 downloaded, 309 MB" is the answer to a different and more common
   * question, and all three numbers were already served by endpoints this page
   * did not call.
   */
  const archive = useApiQuery<ArchiveTotals>({
    key: ['archive', 'stats'],
    path: paths.archive.stats,
    poll: POLL.slow,
  })

  const storage = useApiQuery<StorageTotals>({
    key: ['downloads', 'storage'],
    path: paths.downloads.storage,
    poll: POLL.slow,
  })

  const watchlist = useApiQuery<WatchlistTotals>({
    key: ['admin', 'watchlist', 'overview'],
    path: paths.watchlist.list,
    poll: POLL.slow,
  })

  const health = useApiQuery<EndpointHealthRow[]>({
    key: ['admin', 'endpoints', 'health'],
    path: paths.endpointsHealth,
    poll: POLL.fast,
  })

  const metrics = useApiQuery<TimeseriesResponse>({
    key: ['admin', 'metrics', 'timeseries', rangeId, endpointFilter],
    path: paths.metrics.timeseries,
    params: {
      hours: range?.hours ?? 24,
      step: range?.step ?? 300,
      endpoint: endpointFilter || undefined,
    },
    poll: POLL.normal,
  })

  const pool = useMemo(() => poolCensus(system.data), [system.data])
  const buckets = useMemo(() => toBuckets(metrics.data), [metrics.data])
  const totals = useMemo(() => sumBuckets(buckets), [buckets])

  const openCircuits = (health.data ?? []).filter((row) => row.circuit_open)
  const endpointOptions = [...(health.data ?? [])].sort((a, b) => a.endpoint.localeCompare(b.endpoint))

  const series = useMemo<{ rates: ChartSeries[]; volume: ChartSeries[] }>(
    () => ({
      rates: [
        {
          id: 'success_rate',
          label: t('console:metric.successRate'),
          color: 'var(--success)',
          points: buckets.map((bucket) => ({
            ts: bucket.ts,
            value: bucket.total > 0 ? bucket.ok / bucket.total : null,
          })),
        },
        {
          id: 'risk_rate',
          label: t('console:metric.riskRate'),
          color: 'var(--danger)',
          points: buckets.map((bucket) => ({
            ts: bucket.ts,
            value: bucket.total > 0 ? bucket.risk / bucket.total : null,
          })),
        },
      ],
      volume: [
        {
          id: 'requests',
          label: t('console:metric.requests'),
          color: 'var(--accent)',
          points: buckets.map((bucket) => ({ ts: bucket.ts, value: bucket.total })),
        },
      ],
    }),
    [buckets, t],
  )

  // Per-endpoint curves. Capped at six series because the categorical palette
  // has six entries that clear 3:1 in both themes; beyond that a line chart
  // stops being readable anyway (docs/design/12-design-system.md).
  const perEndpoint = useMemo<ChartSeries[]>(() => {
    if (endpointFilter) return []
    return topEndpoints(metrics.data, MAX_ENDPOINT_SERIES).map((entry, index) => ({
      id: entry.endpoint,
      label: entry.endpoint,
      color: `var(--series-${String((index % 6) + 1)})`,
      points: entry.buckets.map((bucket) => ({
        ts: bucket.ts,
        value: bucket.total > 0 ? bucket.ok / bucket.total : null,
      })),
    }))
  }, [metrics.data, endpointFilter])

  const fresh =
    !system.isLoading &&
    !metrics.isLoading &&
    buckets.length === 0 &&
    pool.total === 0

  const columns: Array<Column<EndpointHealthRow>> = [
    {
      id: 'circuit',
      header: t('console:overview.circuit'),
      width: '130px',
      sortValue: (row) => (row.circuit_open ? 0 : 1),
      cell: (row) => <StatusBadge kind="circuit" value={row.circuit_open ? 'open' : 'closed'} />,
    },
    {
      id: 'endpoint',
      header: t('console:field.endpoint'),
      mono: true,
      sortValue: (row) => row.endpoint,
      cell: (row) => row.endpoint,
    },
    {
      id: 'successRate',
      header: t('console:metric.successRate'),
      align: 'right',
      mono: true,
      sortValue: (row) => row.success_rate,
      cell: (row) => rateCell(row.success_rate, format.percent(row.success_rate)),
    },
    {
      id: 'riskRate',
      header: t('console:metric.riskRate'),
      align: 'right',
      mono: true,
      sortValue: (row) => row.risk_rate,
      cell: (row) =>
        row.risk_rate === null ? (
          <span className="u-muted">{MISSING}</span>
        ) : (
          <span style={row.risk_rate > 0 ? { color: 'var(--danger)' } : undefined}>
            {format.percent(row.risk_rate)}
          </span>
        ),
    },
    {
      id: 'samples',
      header: t('console:overview.samples'),
      align: 'right',
      mono: true,
      sortValue: (row) => row.samples,
      cell: (row) => format.number(row.samples),
    },
    {
      id: 'riskIdentities',
      header: t('console:overview.riskIdentities'),
      align: 'right',
      mono: true,
      defaultHidden: true,
      sortValue: (row) => row.distinct_risk_identities,
      cell: (row) => format.number(row.distinct_risk_identities),
    },
    {
      id: 'reopen',
      header: t('console:overview.reopen'),
      align: 'right',
      mono: true,
      sortValue: (row) => row.retry_after,
      cell: (row) =>
        row.retry_after === null || row.retry_after === undefined ? (
          <span className="u-muted">{MISSING}</span>
        ) : (
          format.duration(row.retry_after * 1000)
        ),
    },
  ]

  return (
    <div className="u-page">
      <PageHeader
        title={t('console:page.overview.title')}
        description={t('console:page.overview.description')}
        badge={
          system.data ? (
            <span className="u-mono u-xs u-muted">
              {system.data.version}
              {system.data.commit ? ` · ${system.data.commit.slice(0, 7)}` : ''}
            </span>
          ) : null
        }
        actions={
          <>
            <div style={{ width: '220px' }}>
              <Select
                value={endpointFilter}
                aria-label={t('console:field.endpoint')}
                onChange={(event) => {
                  setEndpointFilter(event.target.value)
                }}
                options={[
                  { value: '', label: t('console:overview.allEndpoints') },
                  ...endpointOptions.map((row) => ({ value: row.endpoint, label: row.endpoint })),
                ]}
              />
            </div>
            <div style={{ width: '170px' }}>
              <Select
                value={rangeId}
                aria-label={t('console:overview.range.label')}
                onChange={(event) => {
                  setRangeId(event.target.value)
                }}
                options={RANGES.map((option) => ({
                  value: option.id,
                  label: t(`console:overview.range.${option.id}`),
                }))}
              />
            </div>
          </>
        }
      />

      {fresh ? (
        <Card>
          <EmptyState
            icon={<GaugeIcon size={16} />}
            title={t('console:overview.fresh.title')}
            description={t('console:overview.fresh.description')}
            action={
              <Button
                variant="primary"
                onClick={() => {
                  navigate('/identities')
                }}
              >
                {t('console:overview.fresh.action')}
              </Button>
            }
          />
        </Card>
      ) : null}

      <Alerts
        // Until the census actually lands, the pool is unknown, not empty. Without
        // this the page flashes a red "no usable identity" alarm on every load and
        // parks one permanently whenever the status call fails.
        poolKnown={system.data !== undefined && !system.isError}
        openCircuits={openCircuits}
        poolActive={pool.byState.active ?? 0}
        poolDegraded={pool.byState.degraded ?? 0}
        onGoToIdentities={() => {
          navigate('/identities')
        }}
      />

      <div className="u-grid-metrics">
        <MetricTile
          label={t('console:metric.activeIdentities')}
          value={format.number(pool.byState.active ?? 0)}
          loading={system.isLoading}
          tone={(pool.byState.active ?? 0) > 0 ? 'success' : 'danger'}
          footer={t('console:pool.summary', { count: pool.byState.active ?? 0 })}
        />
        <MetricTile
          label={t('console:metric.requests')}
          value={format.compact(totals.total)}
          loading={metrics.isLoading}
          spark={buckets.map((bucket) => bucket.total)}
        />
        <MetricTile
          label={t('console:metric.successRate')}
          value={totals.total > 0 ? format.percent(totals.ok / totals.total) : MISSING}
          loading={metrics.isLoading}
          // Only green when it has earned it. A hardcoded success tone paints a
          // 12% success rate the same shade as a 99% one.
          tone={successTone(totals.total > 0 ? totals.ok / totals.total : null)}
          spark={buckets.map((bucket) => (bucket.total > 0 ? bucket.ok / bucket.total : null))}
        />
        <MetricTile
          label={t('console:metric.riskRate')}
          value={totals.total > 0 ? format.percent(totals.risk / totals.total) : MISSING}
          loading={metrics.isLoading}
          tone={totals.risk > 0 ? 'danger' : undefined}
          spark={buckets.map((bucket) => (bucket.total > 0 ? bucket.risk / bucket.total : null))}
        />
        <MetricTile
          label={t('console:metric.avgDuration')}
          value={format.latency(totals.durationWeight > 0 ? totals.durationSum / totals.durationWeight : null)}
          loading={metrics.isLoading}
        />
        <MetricTile
          label={t('console:metric.openCircuits')}
          value={format.number(openCircuits.length)}
          loading={health.isLoading}
          tone={openCircuits.length > 0 ? 'danger' : undefined}
          footer={t('console:overview.endpointCount', { count: health.data?.length ?? 0 })}
        />
      </div>

      {/* The second question this page should answer: not how the requests
          went, but what they left behind. */}
      <div className="u-grid-metrics">
        <MetricTile
          label={t('console:metric.archived')}
          value={format.number(archive.data?.contents ?? 0)}
          loading={archive.isLoading}
          footer={
            <span className="u-xs u-muted">
              {t('console:metric.archivedHint', { count: archive.data?.authors ?? 0 })}
            </span>
          }
        />
        <MetricTile
          label={t('console:metric.downloaded')}
          value={format.number(archive.data?.stored ?? 0)}
          loading={archive.isLoading}
          footer={
            <span className="u-xs u-muted">
              {t('console:metric.downloadedHint', {
                total: format.number(archive.data?.contents ?? 0),
              })}
            </span>
          }
        />
        <MetricTile
          label={t('console:metric.diskUsed')}
          value={format.bytes(
            storage.data?.downloader?.volume_bytes ?? storage.data?.bytes_total ?? 0,
          )}
          loading={storage.isLoading}
          // Loud only near the ceiling, where new downloads start being refused.
          tone={diskTone(storage.data)}
          footer={
            <span className="u-xs u-muted">
              {t('console:metric.diskUsedHint', {
                ceiling: format.bytes(storage.data?.max_bytes ?? 0),
              })}
            </span>
          }
        />
        <MetricTile
          label={t('console:metric.inFlight')}
          value={format.number(storage.data?.in_flight ?? 0)}
          loading={storage.isLoading}
          footer={
            <span className="u-xs u-muted">
              {t('console:metric.inFlightHint', {
                failed: (storage.data?.by_state?.failed ?? 0) + (storage.data?.by_state?.partial ?? 0),
              })}
            </span>
          }
        />
        <MetricTile
          label={t('console:metric.watching')}
          value={format.number(watchlist.data?.total ?? 0)}
          loading={watchlist.isLoading}
          tone={(watchlist.data?.stats?.failing ?? 0) > 0 ? 'warning' : undefined}
          footer={
            <span className="u-xs u-muted">
              {(watchlist.data?.stats?.failing ?? 0) > 0
                ? t('console:metric.watchingFailing', { count: watchlist.data?.stats?.failing ?? 0 })
                : t('console:metric.watchingHint')}
            </span>
          }
        />
      </div>

      <Card title={t('console:overview.poolDistribution')} description={t('console:overview.poolHint')}>
        {system.error ? (
          <ErrorState
            error={system.error}
            compact
            onRetry={() => {
              void system.refetch()
            }}
          />
        ) : (
          <PoolDistribution byState={pool.byState} total={pool.total} />
        )}
      </Card>

      <div className="u-stack">
        <Card title={t('console:overview.rates')} description={t('console:overview.ratesHint')}>
          <TimeSeriesChart
            series={series.rates}
            loading={metrics.isLoading}
            error={metrics.error}
            onRetry={() => {
              void metrics.refetch()
            }}
            formatValue={(value) => format.percent(value, 0)}
            yMin={0}
            yMax={1}
            height={200}
          />
        </Card>

        <Card title={t('console:overview.volume')} description={t('console:overview.volumeHint')}>
          <TimeSeriesChart
            series={series.volume}
            loading={metrics.isLoading}
            error={metrics.error}
            onRetry={() => {
              void metrics.refetch()
            }}
            formatValue={(value) => format.compact(value)}
            yMin={0}
            height={180}
            showLegend={false}
          />
        </Card>

        {perEndpoint.length > 1 ? (
          <Card
            title={t('console:overview.byEndpoint')}
            description={t('console:overview.byEndpointHint')}
          >
            <TimeSeriesChart
              series={perEndpoint}
              loading={metrics.isLoading}
              error={metrics.error}
              onRetry={() => {
                void metrics.refetch()
              }}
              formatValue={(value) => format.percent(value, 0)}
              yMin={0}
              yMax={1}
              height={200}
            />
          </Card>
        ) : null}
      </div>

      <Card title={t('console:overview.endpoints')} flush>
        <DataTable
          columns={columns}
          rows={health.data}
          getRowId={(row) => row.endpoint}
          loading={health.isLoading}
          error={health.error}
          onRetry={() => {
            void health.refetch()
          }}
          storageKey="endpoint-health"
          defaultSort={{ columnId: 'circuit', direction: 'asc' }}
          flashValue={(row) => (row.circuit_open ? 'open' : 'closed')}
          caption={t('console:overview.endpoints')}
          emptyTitle={t('console:overview.noEndpoints')}
          emptyDescription={t('console:overview.noEndpointsHint')}
        />
      </Card>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Alerts                                                                      */
/* -------------------------------------------------------------------------- */

interface AlertsProps {
  /** False while the pool census is loading or failed; suppresses pool alarms. */
  poolKnown: boolean
  openCircuits: EndpointHealthRow[]
  poolActive: number
  poolDegraded: number
  onGoToIdentities: () => void
}

/**
 * Only conditions an operator must act on. Doc 15 sets the same bar for
 * notifications: an alert that fires on everything is an alert nobody reads.
 */
function Alerts({
  poolKnown,
  openCircuits,
  poolActive,
  poolDegraded,
  onGoToIdentities,
}: AlertsProps) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  const poolEmpty = poolKnown && poolActive === 0
  const degraded = poolKnown ? poolDegraded : 0

  if (openCircuits.length === 0 && !poolEmpty && degraded === 0) return null

  return (
    <div className="u-stack-sm">
      {poolEmpty ? (
        <AlertBanner
          tone="danger"
          title={t('console:overview.alert.poolEmpty')}
          detail={t('console:overview.alert.poolEmptyHint')}
          action={
            <Button size="sm" variant="secondary" onClick={onGoToIdentities}>
              {t('console:identity.mint.action')}
            </Button>
          }
        />
      ) : null}

      {degraded > 0 ? (
        <AlertBanner
          tone="caution"
          title={t('console:overview.alert.degraded', { count: degraded })}
          detail={t('console:overview.alert.degradedHint')}
        />
      ) : null}

      {openCircuits.map((row) => (
        <AlertBanner
          key={row.endpoint}
          tone="danger"
          title={
            <span className="u-row">
              <StatusBadge kind="circuit" value="open" size="sm" flash={false} />
              <span className="u-mono">{row.endpoint}</span>
            </span>
          }
          detail={
            <span className="u-mono u-xs">
              {row.reason ?? MISSING}
              {row.retry_after ? ` · ${format.duration(row.retry_after * 1000)}` : ''}
            </span>
          }
        />
      ))}
    </div>
  )
}

interface AlertBannerProps {
  tone: 'danger' | 'caution'
  title: ReactNode
  detail?: ReactNode
  action?: ReactNode
}

function AlertBanner({ tone, title, detail, action }: AlertBannerProps) {
  const color = tone === 'danger' ? 'var(--danger)' : 'var(--caution)'

  return (
    <div
      role={tone === 'danger' ? 'alert' : 'status'}
      className="u-row-between"
      style={{
        padding: 'var(--space-3)',
        background: 'var(--bg-raised)',
        border: '1px solid var(--border)',
        borderLeft: `3px solid ${color}`,
        borderRadius: 'var(--radius)',
      }}
    >
      <span className="u-row" style={{ alignItems: 'flex-start' }}>
        <span style={{ color }}>
          <AlertIcon size={14} />
        </span>
        <span className="u-stack-sm">
          <span>{title}</span>
          {detail ? <span className="u-xs u-muted">{detail}</span> : null}
        </span>
      </span>
      {action}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Pool distribution                                                           */
/* -------------------------------------------------------------------------- */

const STATE_COLOR: Record<IdentityState, string> = {
  minting: 'var(--accent)',
  active: 'var(--success)',
  cooling: 'var(--warning)',
  degraded: 'var(--caution)',
  retired: 'var(--neutral)',
}

function PoolDistribution({ byState, total }: { byState: Record<string, number>; total: number }) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  if (total === 0) {
    return <EmptyState title={t('console:overview.noIdentities')} description={t('console:overview.noIdentitiesHint')} />
  }

  return (
    <div className="u-stack">
      <div
        className="u-row"
        style={{ gap: '2px', height: '10px' }}
        role="img"
        aria-label={t('console:overview.poolDistribution')}
      >
        {IDENTITY_STATES.map((state) => {
          const count = byState[state] ?? 0
          if (count === 0) return null
          return (
            <span
              key={state}
              title={`${state}: ${String(count)}`}
              style={{
                flex: `${String(count)} 1 0`,
                background: STATE_COLOR[state],
                borderRadius: 'var(--radius-sm)',
              }}
            />
          )
        })}
      </div>
      <div className="u-row u-wrap" style={{ gap: 'var(--space-4)' }}>
        {IDENTITY_STATES.map((state) => (
          <span key={state} className="u-row">
            <StatusBadge kind="identity" value={state} size="sm" flash={false} />
            <span className="u-mono">{format.number(byState[state] ?? 0)}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Shaping                                                                     */
/* -------------------------------------------------------------------------- */

/** Success rates read green above the floor, caution below it, muted when unknown. */
function successTone(value: number | null): 'success' | 'caution' | undefined {
  if (value === null) return undefined
  return value < SUCCESS_RATE_FLOOR ? 'caution' : 'success'
}

function rateCell(value: number | null, text: string) {
  if (value === null) return <span className="u-muted">{MISSING}</span>
  return (
    <span style={value < SUCCESS_RATE_FLOOR ? { color: 'var(--caution)' } : undefined}>{text}</span>
  )
}

/** Flattens the per-platform census into one map plus a total. */
function poolCensus(status: SystemStatusResponse | undefined): {
  byState: Record<string, number>
  total: number
} {
  const byState: Record<string, number> = {}
  let total = 0

  for (const value of Object.values(status?.pool ?? {})) {
    if (typeof value !== 'object') continue
    for (const [state, count] of Object.entries(value)) {
      byState[state] = (byState[state] ?? 0) + count
      total += count
    }
  }
  return { byState, total }
}

/** One row per bucket, outcomes folded in. The API returns them unaggregated. */
function toBuckets(response: TimeseriesResponse | undefined): Bucket[] {
  const byTs = new Map<number, Bucket>()

  for (const point of response?.points ?? []) {
    const ts = new Date(point.bucket).getTime()
    if (!Number.isFinite(ts)) continue
    byTs.set(ts, foldPoint(byTs.get(ts) ?? emptyBucket(ts), point))
  }

  return [...byTs.values()].sort((a, b) => a.ts - b.ts)
}

function emptyBucket(ts: number): Bucket {
  return { ts, total: 0, ok: 0, risk: 0, business: 0, network: 0, durationSum: 0, durationWeight: 0 }
}

type TimeseriesPoint = TimeseriesResponse['points'][number]

/** Adds one outcome row to a bucket, returning a new bucket. */
function foldPoint(bucket: Bucket, point: TimeseriesPoint): Bucket {
  const of = (outcome: Outcome): number => (point.outcome === outcome ? point.requests : 0)
  const timed = point.avg_duration_ms !== null

  return {
    ...bucket,
    total: bucket.total + point.requests,
    ok: bucket.ok + of('ok'),
    risk: bucket.risk + of('risk_control'),
    business: bucket.business + of('business_error'),
    network: bucket.network + of('network_error'),
    durationSum: bucket.durationSum + (timed ? (point.avg_duration_ms ?? 0) * point.requests : 0),
    durationWeight: bucket.durationWeight + (timed ? point.requests : 0),
  }
}

/** The busiest endpoints in the window, each with its own bucket series. */
function topEndpoints(
  response: TimeseriesResponse | undefined,
  limit: number,
): Array<{ endpoint: string; buckets: Bucket[] }> {
  const byEndpoint = new Map<string, Map<number, Bucket>>()

  for (const point of response?.points ?? []) {
    const ts = new Date(point.bucket).getTime()
    if (!Number.isFinite(ts)) continue
    const buckets = byEndpoint.get(point.endpoint) ?? new Map<number, Bucket>()
    buckets.set(ts, foldPoint(buckets.get(ts) ?? emptyBucket(ts), point))
    byEndpoint.set(point.endpoint, buckets)
  }

  return [...byEndpoint.entries()]
    .map(([endpoint, buckets]) => ({
      endpoint,
      buckets: [...buckets.values()].sort((a, b) => a.ts - b.ts),
    }))
    .sort((a, b) => total(b.buckets) - total(a.buckets))
    .slice(0, limit)
}

function total(buckets: Bucket[]): number {
  return buckets.reduce((sum, bucket) => sum + bucket.total, 0)
}

function sumBuckets(buckets: Bucket[]): Bucket {
  return buckets.reduce<Bucket>(
    (accumulator, bucket) => ({
      ts: 0,
      total: accumulator.total + bucket.total,
      ok: accumulator.ok + bucket.ok,
      risk: accumulator.risk + bucket.risk,
      business: accumulator.business + bucket.business,
      network: accumulator.network + bucket.network,
      durationSum: accumulator.durationSum + bucket.durationSum,
      durationWeight: accumulator.durationWeight + bucket.durationWeight,
    }),
    emptyBucket(0),
  )
}
