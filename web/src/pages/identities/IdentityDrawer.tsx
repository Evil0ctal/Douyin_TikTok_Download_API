import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'

import {
  AlertIcon,
  Banner,
  Button,
  Card,
  CodeBlock,
  Drawer,
  ErrorState,
  ExternalIcon,
  Skeleton,
  StatusBadge,
  useToast,
} from '@/components'
import { useApiQuery, useCopy, useFormatters } from '@/hooks'
import { paths } from '@/lib/endpoints'
import { MISSING } from '@/lib/format'

import styles from '../identities.module.css'
import { IdentityContents } from './IdentityContents'
import { RotationCard } from './RotationCard'
import { ExpiryTag, Fact, needsReset, usePlatformName } from './shared'
import type { CookieInventory, IdentityRow, PoolLevel, ProbeState } from './types'

interface RequestRow {
  ts: string
  endpoint: string
  outcome: string
  http_status: number | null
  duration_ms: number | null
  error_code: string | null
  request_id: string
}

/** The logs endpoint answers a bare list or an envelope, depending on the page. */
function asRows<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object') {
    const items = (payload as { items?: unknown }).items
    if (Array.isArray(items)) return items as T[]
  }
  return []
}

/** A week. Far enough back to explain a cooldown, near enough to stay small. */
const HISTORY_MINUTES = 7 * 24 * 60
const HISTORY_LIMIT = 200

/** Contract C2: the request log, filtered to one identity. */
function logsHref(identityId: string): string {
  return `/logs?tab=requests&identity_id=${encodeURIComponent(identityId)}`
}

interface IdentityDrawerProps {
  /** The identity the address names; the drawer is open while it is set. */
  identityId: string | null
  /**
   * That identity's row from the listing, live: read by id on every poll
   * rather than held from the click. The drawer used to keep the row as it
   * was when it opened, so a reset, a retire or a cooldown ending happened on
   * the table behind it and not in it.
   */
  identity: IdentityRow | null
  /** The row is still being fetched on its own (a deep link past the page). */
  loading: boolean
  probe: ProbeState | undefined
  canManage: boolean
  pool: PoolLevel | undefined
  /** Check the login as soon as it opens: it was just imported. */
  autoCheck: boolean
  onAutoChecked: () => void
  onTest: (identity: IdentityRow) => void
  onReset: (identity: IdentityRow) => void
  onRetire: (identity: IdentityRow) => void
  onExport: (identity: IdentityRow) => void
  exporting: boolean
  onChanged: () => Promise<void>
  onClose: () => void
}

/**
 * One identity: what it is, what it holds, and what it has been doing.
 *
 * The per-endpoint breakdown is the point of the history rather than the list.
 * One endpoint refusing while the others answer is a signature problem or a
 * sign-protected path; every endpoint refusing at once is the identity itself.
 * Those want opposite responses, and a flat list of thirty rows does not tell
 * them apart.
 *
 * And it can be acted on here. Test, reset, retire and export lived only in
 * the table row, so having read why an identity was failing an operator closed
 * the drawer to find the button for the obvious next step.
 */
export function IdentityDrawer({
  identityId,
  identity,
  loading,
  probe,
  canManage,
  pool,
  autoCheck,
  onAutoChecked,
  onTest,
  onReset,
  onRetire,
  onExport,
  exporting,
  onChanged,
  onClose,
}: IdentityDrawerProps) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const { copy } = useCopy()
  const platformName = usePlatformName()

  const history = useApiQuery<RequestRow[]>({
    key: ['admin', 'logs', 'identity', identityId ?? 'none'],
    path: paths.logs.requests,
    params: identityId
      ? {
          identity_id: identityId,
          minutes: String(HISTORY_MINUTES),
          limit: String(HISTORY_LIMIT),
        }
      : undefined,
    enabled: Boolean(identityId),
  })

  /**
   * What the identity actually holds.
   *
   * Fetched only while the drawer is open, and not polled: a cookie jar does
   * not change while somebody is reading it, and this is the one endpoint on
   * the page that decrypts a credential column to answer. It sits under the
   * page's key, so an action taken from the drawer - a retire wipes the jar,
   * and the drawer now stays open across it - refetches it with the rest.
   */
  const jar = useApiQuery<CookieInventory>({
    key: ['admin', 'identities', 'cookies', identityId ?? 'none'],
    path: identityId ? paths.identities.cookies(identityId) : paths.identities.list,
    enabled: Boolean(identityId),
  })

  const rows = useMemo(() => asRows<RequestRow>(history.data), [history.data])

  /** Per endpoint: how many, how many refused, and what the platform said. */
  const byEndpoint = useMemo(() => {
    const buckets = new Map<
      string,
      { endpoint: string; total: number; risk: number; failed: number; reasons: Set<string> }
    >()
    for (const row of rows) {
      const bucket = buckets.get(row.endpoint) ?? {
        endpoint: row.endpoint,
        total: 0,
        risk: 0,
        failed: 0,
        reasons: new Set<string>(),
      }
      bucket.total += 1
      if (row.outcome === 'risk_control') bucket.risk += 1
      if (row.outcome === 'network_error') bucket.failed += 1
      if (row.error_code) bucket.reasons.add(row.error_code)
      buckets.set(row.endpoint, bucket)
    }
    // Whatever refused most, first. That is the row somebody opened this for.
    return [...buckets.values()].sort((a, b) => b.risk - a.risk || b.total - a.total)
  }, [rows])

  const risky = byEndpoint.filter((entry) => entry.risk > 0)
  const retired = identity?.state === 'retired'

  return (
    <Drawer
      open={identityId !== null}
      onClose={onClose}
      title={t('console:identity.drawerTitle')}
      description={identityId ?? undefined}
    >
      {identityId === null ? null : !identity ? (
        loading ? (
          <div className="u-stack-sm">
            <Skeleton height={18} />
            <Skeleton height={18} />
          </div>
        ) : (
          <p className="u-muted" style={{ margin: 0 }}>
            {t('console:identity.drawer.notFound')}
          </p>
        )
      ) : (
        <div className="u-stack">
          <div className="u-row u-wrap">
            <StatusBadge kind="identity" value={identity.state} flash={false} />
            <Fact
              label={t('console:field.platform')}
              value={platformName(identity.platform)}
            />
            <Fact
              label={t('console:field.consecutiveFails')}
              value={identity.consecutive_fails}
              mono
            />
            <Fact
              label={t('console:field.lastUsedAt')}
              value={identity.last_used_at ? format.relative(identity.last_used_at) : MISSING}
            />
            {identity.cooldown_until ? (
              <Fact
                label={t('console:identity.column.cooldown')}
                value={format.relative(identity.cooldown_until)}
              />
            ) : null}
            {identity.authenticated && identity.expires_at ? (
              <Fact
                label={t('console:identity.drawer.expires')}
                value={
                  <span className="u-row" style={{ gap: 'var(--space-2)' }}>
                    {format.date(identity.expires_at)}
                    <ExpiryTag expiresAt={identity.expires_at} />
                  </span>
                }
              />
            ) : null}
            {retired && identity.retired_at ? (
              <Fact
                label={t('console:identity.drawer.retiredAt')}
                value={
                  <span title={format.timestamp(identity.retired_at)}>
                    {format.relative(identity.retired_at)}
                  </span>
                }
              />
            ) : null}
          </div>

          {/* Retired for a reason somebody wrote down - by hand, by the proxy
              prober, by the pool - and the row returned it on every poll
              without the page ever showing it. */}
          {retired && identity.retire_reason ? (
            <p className="u-xs u-secondary" style={{ margin: 0 }}>
              {t('console:identity.drawer.retireReason', { reason: identity.retire_reason })}
            </p>
          ) : null}

          <div className={styles.drawerActions}>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                void copy(identity.id)
                toast.success(t('console:identity.drawer.copied'))
              }}
            >
              {t('console:identity.drawer.copyId')}
            </Button>
            <Link href={logsHref(identity.id)} className={styles.drawerLink}>
              <ExternalIcon size={12} />
              {t('console:identity.drawer.openLogs')}
            </Link>
            {canManage && !retired ? (
              <>
                <Button
                  size="sm"
                  variant="secondary"
                  loading={probe?.running ?? false}
                  onClick={() => {
                    onTest(identity)
                  }}
                >
                  {t('common:action.test')}
                </Button>
                {needsReset(identity) ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      onReset(identity)
                    }}
                  >
                    {t('console:identity.reset.action')}
                  </Button>
                ) : null}
              </>
            ) : null}
            {canManage ? (
              <Button
                size="sm"
                variant="secondary"
                loading={exporting}
                onClick={() => {
                  onExport(identity)
                }}
              >
                {t('console:identity.drawer.export')}
              </Button>
            ) : null}
            {canManage && !retired ? (
              <Button
                size="sm"
                variant="danger"
                onClick={() => {
                  onRetire(identity)
                }}
              >
                {t('console:identity.retire')}
              </Button>
            ) : null}
          </div>

          {probe?.result || probe?.error ? <ProbeCard probe={probe} /> : null}

          <IdentityContents
            identity={identity}
            jar={jar.data}
            loading={jar.isLoading}
            canManage={canManage}
            autoCheck={autoCheck}
            onAutoChecked={onAutoChecked}
          />

          {!retired ? (
            <RotationCard
              identity={identity}
              canManage={canManage}
              pool={pool}
              onChanged={onChanged}
            />
          ) : null}

          {/* The answer to "what caused the risk control", stated rather than
              left to be inferred from a list. */}
          {risky.length > 0 ? (
            <Banner tone="caution" icon={<AlertIcon size={14} />}>
              <div className="u-stack-sm">
                <span>
                  {risky.length === byEndpoint.length && byEndpoint.length > 1
                    ? t('console:identity.history.everythingRefused')
                    : t('console:identity.history.oneRefused', {
                        endpoint: risky[0]?.endpoint ?? '',
                      })}
                </span>
                <span className="u-xs u-muted">
                  {risky.length === byEndpoint.length && byEndpoint.length > 1
                    ? t('console:identity.history.everythingRefusedHint')
                    : t('console:identity.history.oneRefusedHint')}
                </span>
              </div>
            </Banner>
          ) : null}

          <Card title={t('console:identity.history.byEndpoint')} flush>
            {history.isLoading ? (
              <div className="u-stack-sm" style={{ padding: 'var(--space-4)' }}>
                <Skeleton height={18} />
                <Skeleton height={18} />
              </div>
            ) : byEndpoint.length === 0 ? (
              <p className="u-muted" style={{ margin: 0, padding: 'var(--space-4)' }}>
                {t('console:identity.history.empty')}
              </p>
            ) : (
              <ul className={styles.endpointList}>
                {byEndpoint.map((entry) => (
                  <li key={entry.endpoint} className={styles.endpointRow}>
                    <span className="u-mono u-truncate" title={entry.endpoint}>
                      {entry.endpoint}
                    </span>
                    <span className="u-xs u-muted">
                      {t('console:identity.history.calls', { count: entry.total })}
                    </span>
                    {entry.risk > 0 ? (
                      <span className={styles.riskCount}>
                        {t('console:identity.history.refused', { count: entry.risk })}
                      </span>
                    ) : (
                      <span className="u-xs u-muted">{t('console:identity.history.clean')}</span>
                    )}
                    <span className="u-xs u-muted u-truncate">
                      {[...entry.reasons].join(', ')}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title={t('console:identity.history.recent')} flush>
            <ul className={styles.requestList}>
              {rows.slice(0, 25).map((row) => (
                <li key={row.request_id} className={styles.requestRow}>
                  <StatusBadge kind="outcome" value={row.outcome} size="sm" flash={false} />
                  <span className="u-mono u-xs u-truncate" title={row.endpoint}>
                    {row.endpoint}
                  </span>
                  <span className="u-xs u-muted u-mono">{row.http_status ?? MISSING}</span>
                  <span className="u-xs u-muted u-mono">
                    {row.duration_ms === null ? MISSING : format.latency(row.duration_ms)}
                  </span>
                  <span className="u-xs u-muted">{format.relative(row.ts)}</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </Drawer>
  )
}

/**
 * The last probe, whole.
 *
 * It showed a pass/fail badge and the classifier rule, which left out the
 * three things that tell a refusal from a timeout from a slow exit: what the
 * platform's answer was classified as, its status, and how long it took.
 */
function ProbeCard({ probe }: { probe: ProbeState }) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const result = probe.result

  return (
    <Card title={t('console:identity.test.title')}>
      {probe.error ? <ErrorState error={probe.error} compact /> : null}
      {result ? (
        <div className="u-stack-sm">
          <div className="u-row u-wrap">
            <StatusBadge kind="health" value={result.ok ? 'healthy' : 'unhealthy'} flash={false} />
            {result.outcome ? (
              <StatusBadge kind="outcome" value={result.outcome} flash={false} />
            ) : null}
            <Fact label={t('console:field.httpStatus')} value={result.status ?? MISSING} mono />
            <Fact label={t('console:field.latency')} value={format.latency(result.latency_ms)} mono />
            <Fact label={t('console:identity.test.rule')} value={result.rule ?? MISSING} mono />
          </div>
          {result.detail ? <CodeBlock code={result.detail} language="text" /> : null}
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t(`console:identity.test.note.${result.outcome ?? 'unknown'}`, {
              defaultValue: t('console:identity.test.note.unknown'),
            })}
          </p>
        </div>
      ) : null}
    </Card>
  )
}
