import { useMemo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'

import {
  Button,
  CodeBlock,
  CopyableId,
  Drawer,
  ErrorState,
  Skeleton,
  StatusBadge,
} from '@/components'
import { useApiQuery, useFormatters } from '@/hooks'
import type { ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { OUTCOMES, type IdentityState, type Outcome, type RequestLogRow } from '@/lib/types'

/**
 * One proxy, opened from its row.
 *
 * A row used to open nothing unless this browser had probed it in the current
 * session, so the questions somebody clicks a proxy to ask went unanswered:
 * why did it fail - the column clamps the reason to two lines - what is bound
 * to it, and is a platform refusing it. A 412 from Bilibili is a verdict on an
 * address, and every identity behind one proxy shares that address, so the
 * request log filtered to this egress is the evidence.
 */

/** What the list returns for one proxy; shared with the page. */
export interface ProxyRowLike {
  id: string
  label?: string | null
  url_masked: string | null
  healthy: boolean
  last_check_at?: string | null
  last_error?: string | null
  latency_ms?: number | null
  identity_count?: number | null
}

/** What a proxy probe reports (mirrors ProxyProbe in src/dtk/ops/probes.py). */
export interface ProxyProbeResult {
  ok?: boolean
  latency_ms?: number | null
  exit_ip?: string | null
  country?: string | null
  timezone?: string | null
  detail?: string | null
}

export interface ProbeState {
  running: boolean
  result?: ProxyProbeResult
  error?: ApiError
}

/** GET /admin/proxies/{id}: the list's row plus what is bound to it. */
interface BoundIdentity {
  id: string
  platform: string
  state: IdentityState
  authenticated: boolean
  consecutive_fails: number
  last_used_at: string | null
  rotation_endpoints: string[] | null
}

interface ProxyDetail {
  identities: BoundIdentity[]
}

/** The request log, over the window the drawer reads. */
const RECENT_MINUTES = 1440
const RECENT_LIMIT = 50
/** Rows listed under the counts; the counts cover all of RECENT_LIMIT. */
const RECENT_SHOWN = 8

export interface ProxyDrawerProps {
  proxy: ProxyRowLike | null
  probe: ProbeState | undefined
  canManage: boolean
  /** A bulk probe is running; a second probe of the same proxy would race it. */
  busy?: boolean
  onClose: () => void
  onTest: (proxy: ProxyRowLike) => void
  onEdit: (proxy: ProxyRowLike) => void
  onDelete: (proxy: ProxyRowLike) => void
}

function Section({ title, aside, children }: { title: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="u-stack-sm">
      <div className="u-row-between u-wrap">
        <h3 className="u-xs u-muted" style={{ margin: 0, fontWeight: 500 }}>
          {title}
        </h3>
        {aside}
      </div>
      {children}
    </section>
  )
}

const DETAIL_GRID = {
  margin: 0,
  display: 'grid',
  gridTemplateColumns: 'auto 1fr',
  gap: 'var(--space-2) var(--space-3)',
  alignItems: 'center',
} as const

export function ProxyDrawer({
  proxy,
  probe,
  canManage,
  busy = false,
  onClose,
  onTest,
  onEdit,
  onDelete,
}: ProxyDrawerProps) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const id = proxy?.id ?? null

  // Under the list's key, so a refresh after a probe or an edit reaches it.
  const detail = useApiQuery<ProxyDetail>({
    key: ['admin', 'proxies', 'detail', id],
    path: id ? paths.proxies.byId(id) : paths.proxies.list,
    enabled: id !== null,
  })

  const recentParams = { proxy_id: id ?? undefined, minutes: RECENT_MINUTES, limit: RECENT_LIMIT }
  const recent = useApiQuery<RequestLogRow[]>({
    key: ['admin', 'logs', 'requests', recentParams],
    path: paths.logs.requests,
    params: recentParams,
    enabled: id !== null,
  })

  const recentRows = useMemo(() => (Array.isArray(recent.data) ? recent.data : []), [recent.data])
  const counts = useMemo(() => {
    const tally = new Map<Outcome, number>()
    for (const row of recentRows) tally.set(row.outcome, (tally.get(row.outcome) ?? 0) + 1)
    return OUTCOMES.filter((outcome) => tally.has(outcome)).map(
      (outcome): [Outcome, number] => [outcome, tally.get(outcome) ?? 0],
    )
  }, [recentRows])

  const result = probe?.result
  const logsHref = id ? `/logs?tab=requests&proxy_id=${encodeURIComponent(id)}` : '/logs'

  return (
    <Drawer
      open={proxy !== null}
      onClose={onClose}
      title={proxy?.label || t('console:proxy.drawer.title')}
      description={proxy ? (proxy.url_masked ?? proxy.id) : undefined}
      footer={
        proxy && canManage ? (
          <div className="u-row u-wrap">
            <Button
              size="sm"
              variant="secondary"
              loading={probe?.running ?? false}
              disabled={busy}
              onClick={() => {
                onTest(proxy)
              }}
            >
              {t('common:action.test')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                onEdit(proxy)
              }}
            >
              {t('common:action.edit')}
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                onDelete(proxy)
              }}
            >
              {t('common:action.delete')}
            </Button>
          </div>
        ) : undefined
      }
    >
      {proxy ? (
        <div className="u-stack">
          <div className="u-row u-wrap">
            <StatusBadge kind="health" value={proxy.healthy ? 'healthy' : 'unhealthy'} />
            <span className="u-xs u-muted">
              {proxy.last_check_at
                ? t('console:proxy.drawer.checked', { when: format.relative(proxy.last_check_at) })
                : t('console:proxy.drawer.neverChecked')}
            </span>
            <CopyableId value={proxy.id} label={t('console:proxy.column.id')} />
          </div>

          {proxy.last_error ? (
            <Section title={t('console:proxy.drawer.lastError')}>
              {/* Whole, unlike the clamped line in the table: this is the one
                  sentence that says whether to fix a subscription URL or
                  replace a dead server (issue 781). */}
              <p
                className="u-mono u-xs"
                style={{ margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
              >
                {proxy.last_error}
              </p>
            </Section>
          ) : null}

          {probe?.error || result ? (
            <Section title={t('console:proxy.test.title')}>
              {probe?.error ? <ErrorState compact error={probe.error} /> : null}
              {result ? (
                <div className="u-stack-sm">
                  <StatusBadge kind="health" value={result.ok ? 'healthy' : 'unhealthy'} />
                  <dl style={DETAIL_GRID}>
                    <dt className="u-xs u-muted">{t('console:field.latency')}</dt>
                    <dd className="u-mono" style={{ margin: 0 }}>
                      {format.latency(result.latency_ms)}
                    </dd>
                    <dt className="u-xs u-muted">{t('console:proxy.exitIp')}</dt>
                    <dd className="u-mono" style={{ margin: 0 }}>
                      {result.exit_ip ?? format.number(null)}
                    </dd>
                    <dt className="u-xs u-muted">{t('console:field.country')}</dt>
                    <dd className="u-mono" style={{ margin: 0 }}>
                      {result.country ?? format.number(null)}
                    </dd>
                    <dt className="u-xs u-muted">{t('console:proxy.column.timezone')}</dt>
                    <dd className="u-mono" style={{ margin: 0 }}>
                      {result.timezone ?? format.number(null)}
                    </dd>
                  </dl>
                  {result.detail ? <CodeBlock code={result.detail} language="text" /> : null}
                </div>
              ) : null}
            </Section>
          ) : null}

          <Section
            title={t('console:proxy.drawer.identities', {
              count: detail.data?.identities.length ?? proxy.identity_count ?? 0,
            })}
          >
            {detail.isLoading ? (
              <Skeleton height={44} />
            ) : detail.isError ? (
              <ErrorState
                compact
                error={detail.error}
                onRetry={() => {
                  void detail.refetch()
                }}
              />
            ) : (detail.data?.identities.length ?? 0) === 0 ? (
              <p className="u-xs u-muted" style={{ margin: 0 }}>
                {t('console:proxy.drawer.noIdentities')}
              </p>
            ) : (
              <ul className="u-stack-sm" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {detail.data?.identities.map((identity) => (
                  <li key={identity.id} className="u-row u-wrap" style={{ minWidth: 0 }}>
                    <span className="u-mono u-xs">{identity.platform}</span>
                    <StatusBadge kind="identity" value={identity.state} size="sm" flash={false} />
                    {identity.rotation_endpoints !== null ? (
                      <span className="u-xs u-muted">{t('console:proxy.drawer.reserved')}</span>
                    ) : null}
                    <Link
                      className="u-mono u-xs"
                      href={`/identities?id=${encodeURIComponent(identity.id)}`}
                      title={identity.id}
                    >
                      {identity.id.slice(0, 8)}
                    </Link>
                    <span className="u-xs u-muted">
                      {identity.last_used_at
                        ? format.relative(identity.last_used_at)
                        : t('common:time.never')}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section
            title={t('console:proxy.drawer.recentTitle')}
            aside={
              <Link className="u-xs" href={logsHref}>
                {t('console:proxy.drawer.viewRequests')}
              </Link>
            }
          >
            {recent.isLoading ? (
              <Skeleton height={44} />
            ) : recent.isError ? (
              <ErrorState
                compact
                error={recent.error}
                onRetry={() => {
                  void recent.refetch()
                }}
              />
            ) : recentRows.length === 0 ? (
              <p className="u-xs u-muted" style={{ margin: 0 }}>
                {t('console:proxy.drawer.recentEmpty')}
              </p>
            ) : (
              <div className="u-stack-sm">
                <p className="u-xs u-muted" style={{ margin: 0 }}>
                  {t('console:proxy.drawer.recentHint', { count: recentRows.length })}
                </p>
                <div className="u-row u-wrap">
                  {counts.map(([outcome, count]) => (
                    <span key={outcome} className="u-row" style={{ gap: 'var(--space-1)' }}>
                      <StatusBadge kind="outcome" value={outcome} size="sm" flash={false} />
                      <span className="u-mono u-xs">{format.number(count)}</span>
                    </span>
                  ))}
                </div>
                <ul className="u-stack-sm" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                  {recentRows.slice(0, RECENT_SHOWN).map((row) => (
                    <li
                      key={`${row.request_id}-${row.ts}`}
                      className="u-row u-wrap u-xs"
                      style={{ minWidth: 0 }}
                    >
                      <span className="u-muted u-nowrap">{format.relative(row.ts)}</span>
                      <StatusBadge kind="outcome" value={row.outcome} size="sm" flash={false} />
                      <span className="u-mono u-truncate" style={{ minWidth: 0 }}>
                        {row.endpoint}
                      </span>
                      {row.http_status ? (
                        <span className="u-mono u-muted">{row.http_status}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Section>
        </div>
      ) : null}
    </Drawer>
  )
}
