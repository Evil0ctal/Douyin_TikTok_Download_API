import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  CodeBlock,
  CopyableId,
  DataTable,
  Drawer,
  ErrorCodeBadge,
  Input,
  PageHeader,
  Select,
  StatusBadge,
  Switch,
  type Column,
} from '@/components'
import { useApiQuery, useFormatters } from '@/hooks'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import {
  OUTCOMES,
  REJECT_REASONS,
  type AuditLogRow,
  type Outcome,
  type RequestLogRow,
} from '@/lib/types'

/**
 * Request log and audit trail.
 *
 * Two logs, deliberately apart. The request log is high volume and answers
 * "what happened to this call"; the audit trail is low volume and answers "who
 * changed what", and it must not be lost in the other one's noise
 * (docs/design/08-security.md, docs/design/15-operations.md).
 *
 * The outcome filters carry the console's status colours, which means
 * BUSINESS_ERROR is muted rather than red: a deleted video is not a system
 * fault and must not send anyone hunting for a bug that does not exist
 * (docs/design/12-design-system.md).
 */

type Tab = 'requests' | 'audit'

/** Relative windows, in minutes. A relative window keeps the query key stable across polls. */
const RANGES = ['15', '60', '360', '1440', '10080', '43200'] as const
type Range = (typeof RANGES)[number]

const LIMITS = ['100', '200', '500'] as const

interface RequestFilters {
  requestId: string
  endpoint: string
  identityId: string
  outcomes: Outcome[]
  minutes: Range
  limit: string
}

const DEFAULT_REQUEST_FILTERS: RequestFilters = {
  requestId: '',
  endpoint: '',
  identityId: '',
  outcomes: [],
  minutes: '60',
  limit: '100',
}

interface AuditFilters {
  action: string
  limit: string
}

const DEFAULT_AUDIT_FILTERS: AuditFilters = { action: '', limit: '100' }

/** The audit route also carries these; the shared type predates them. */
interface AuditRow extends AuditLogRow {
  api_key_id?: string | null
  user_agent?: string | null
}

const KNOWN_REJECT_REASONS: ReadonlySet<string> = new Set(REJECT_REASONS)

/**
 * The scheduler stores a wire code (RejectReason in src/dtk/core/types.py). One
 * this build has no copy for is shown exactly as it arrived: an untranslated
 * reason is still the thing to quote in an issue, a missing-key stub is not.
 */
function RejectReasonCell({ value }: { value: string | null | undefined }) {
  const { t } = useTranslation('console')
  if (!value) return <span className="u-muted">—</span>
  if (!KNOWN_REJECT_REASONS.has(value)) return <span className="u-mono">{value}</span>
  return <>{t(`logs.rejectReason.${value}`)}</>
}

function asRows<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object') {
    const items = (payload as { items?: unknown }).items
    if (Array.isArray(items)) return items as T[]
  }
  return []
}

export default function Logs() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  const [tab, setTab] = useState<Tab>('requests')
  const [live, setLive] = useState(true)
  const [filters, setFilters] = useState<RequestFilters>(DEFAULT_REQUEST_FILTERS)
  const [auditFilters, setAuditFilters] = useState<AuditFilters>(DEFAULT_AUDIT_FILTERS)
  const [openRequest, setOpenRequest] = useState<RequestLogRow | null>(null)
  const [openAudit, setOpenAudit] = useState<AuditRow | null>(null)

  const requestParams = useMemo(
    () => ({
      request_id: filters.requestId.trim() || undefined,
      endpoint: filters.endpoint.trim() || undefined,
      identity_id: filters.identityId.trim() || undefined,
      outcome: filters.outcomes.length > 0 ? [...filters.outcomes] : undefined,
      minutes: Number(filters.minutes),
      limit: Number(filters.limit),
    }),
    [filters],
  )

  const requests = useApiQuery<unknown>({
    key: ['admin', 'logs', 'requests', requestParams],
    path: paths.logs.requests,
    params: requestParams,
    enabled: tab === 'requests',
    poll: live ? POLL.fast : false,
  })

  const auditParams = useMemo(
    () => ({
      action: auditFilters.action.trim() || undefined,
      limit: Number(auditFilters.limit),
    }),
    [auditFilters],
  )

  const audit = useApiQuery<unknown>({
    key: ['admin', 'logs', 'audit', auditParams],
    path: paths.logs.audit,
    params: auditParams,
    enabled: tab === 'audit',
    poll: live ? POLL.slow : false,
  })

  const requestRows = useMemo(() => asRows<RequestLogRow>(requests.data), [requests.data])
  const auditRows = useMemo(() => asRows<AuditRow>(audit.data), [audit.data])

  const timeZone = format.timeZone()

  const toggleOutcome = (outcome: Outcome): void => {
    setFilters((current) => ({
      ...current,
      outcomes: current.outcomes.includes(outcome)
        ? current.outcomes.filter((entry) => entry !== outcome)
        : [...current.outcomes, outcome],
    }))
  }

  const requestColumns = useMemo<Array<Column<RequestLogRow>>>(
    () => [
      {
        id: 'ts',
        header: t('common:time.timestamp'),
        mono: true,
        hideable: false,
        cell: (row) => <span className="u-nowrap">{format.timestamp(row.ts)}</span>,
        sortValue: (row) => row.ts,
      },
      {
        id: 'outcome',
        header: t('console:field.outcome'),
        hideable: false,
        cell: (row) => <StatusBadge kind="outcome" value={row.outcome} size="sm" />,
        sortValue: (row) => row.outcome,
      },
      {
        id: 'endpoint',
        header: t('console:field.endpoint'),
        mono: true,
        cell: (row) => <span className="u-truncate">{row.endpoint}</span>,
        sortValue: (row) => row.endpoint,
      },
      {
        id: 'requestId',
        // A floor, not a width: auto layout treats `width` as a suggestion
        // and overrode it, leaving the last six characters cut - which is
        // where two uuids from the same second differ least visibly. If the
        // table now wants more room than it has, it scrolls, which is a cost
        // paid by the reader who can see it rather than one hidden in an
        // ellipsis.
        minWidth: '292px',
        header: t('console:field.requestId'),
        mono: true,
        cell: (row) => <CopyableId value={row.request_id} />,
      },
      {
        id: 'httpStatus',
        header: t('console:field.httpStatus'),
        align: 'right',
        mono: true,
        cell: (row) => row.http_status ?? <span className="u-muted">—</span>,
        sortValue: (row) => row.http_status ?? null,
      },
      {
        id: 'duration',
        header: t('console:field.duration'),
        align: 'right',
        mono: true,
        cell: (row) => format.latency(row.duration_ms),
        sortValue: (row) => row.duration_ms,
      },
      {
        id: 'identity',
        // A floor, not a width: auto layout treats `width` as a suggestion
        // and overrode it, leaving the last six characters cut - which is
        // where two uuids from the same second differ least visibly. If the
        // table now wants more room than it has, it scrolls, which is a cost
        // paid by the reader who can see it rather than one hidden in an
        // ellipsis.
        minWidth: '292px',
        header: t('console:field.identity'),
        mono: true,
        cell: (row) => <CopyableId value={row.identity_id} />,
      },
      {
        id: 'proxy',
        // A floor, not a width: auto layout treats `width` as a suggestion
        // and overrode it, leaving the last six characters cut - which is
        // where two uuids from the same second differ least visibly. If the
        // table now wants more room than it has, it scrolls, which is a cost
        // paid by the reader who can see it rather than one hidden in an
        // ellipsis.
        minWidth: '292px',
        header: t('console:field.proxy'),
        mono: true,
        defaultHidden: true,
        cell: (row) => <CopyableId value={row.proxy_id} />,
      },
      {
        id: 'errorCode',
        header: t('console:field.errorCode'),
        cell: (row) =>
          row.error_code ? (
            <ErrorCodeBadge code={row.error_code} />
          ) : (
            <span className="u-muted">—</span>
          ),
        sortValue: (row) => row.error_code ?? null,
      },
      {
        id: 'rejectReason',
        header: t('logs.field.rejectReason'),
        defaultHidden: true,
        cell: (row) => <RejectReasonCell value={row.reject_reason} />,
        sortValue: (row) => row.reject_reason ?? null,
      },
      {
        id: 'cacheHit',
        header: t('console:field.cacheHit'),
        defaultHidden: true,
        cell: (row) => (
          <span className="u-mono">
            {row.cache_hit ? t('common:value.yes') : t('common:value.no')}
          </span>
        ),
        sortValue: (row) => String(row.cache_hit),
      },
      {
        id: 'signer',
        header: t('console:field.signer'),
        mono: true,
        defaultHidden: true,
        cell: (row) => row.signer ?? <span className="u-muted">—</span>,
      },
      {
        id: 'platform',
        header: t('console:field.platform'),
        mono: true,
        defaultHidden: true,
        cell: (row) => row.platform,
        sortValue: (row) => row.platform,
      },
    ],
    [t, format],
  )

  const auditColumns = useMemo<Array<Column<AuditRow>>>(
    () => [
      {
        id: 'ts',
        header: t('common:time.timestamp'),
        mono: true,
        hideable: false,
        cell: (row) => <span className="u-nowrap">{format.timestamp(row.ts)}</span>,
        sortValue: (row) => row.ts,
      },
      {
        id: 'action',
        header: t('logs.field.action'),
        mono: true,
        hideable: false,
        cell: (row) => <span className="u-truncate">{row.action}</span>,
        sortValue: (row) => row.action,
      },
      {
        id: 'actor',
        header: t('logs.field.actor'),
        cell: (row) =>
          row.username ? (
            <span className="u-truncate">{row.username}</span>
          ) : row.user_id ? (
            <CopyableId value={row.user_id} />
          ) : row.api_key_id ? (
            <span className="u-row">
              <span className="u-xs u-muted">{t('logs.byApiKey')}</span>
              <CopyableId value={row.api_key_id} />
            </span>
          ) : (
            <span className="u-muted">{t('logs.bySystem')}</span>
          ),
        sortValue: (row) => row.username ?? row.user_id ?? null,
      },
      {
        id: 'target',
        header: t('logs.field.target'),
        mono: true,
        cell: (row) =>
          row.target_type ? (
            <span className="u-row">
              <span>{row.target_type}</span>
              <CopyableId value={row.target_id} />
            </span>
          ) : (
            <span className="u-muted">—</span>
          ),
        sortValue: (row) => row.target_type ?? null,
      },
      {
        id: 'ip',
        header: t('logs.field.ip'),
        mono: true,
        defaultHidden: true,
        cell: (row) => row.ip ?? <span className="u-muted">—</span>,
      },
    ],
    [t, format],
  )

  const tabs: Array<{ id: Tab; label: string }> = [
    { id: 'requests', label: t('logs.tab.requests') },
    { id: 'audit', label: t('logs.tab.audit') },
  ]

  return (
    <div className="u-page">
      <PageHeader
        title={t('page.logs.title')}
        description={t('page.logs.description')}
        actions={
          <Switch
            checked={live}
            onChange={(event) => {
              setLive(event.target.checked)
            }}
            label={t('logs.live')}
            hint={t('logs.liveHint')}
          />
        }
      />

      <div className="u-row" role="tablist" aria-label={t('page.logs.title')}>
        {tabs.map((entry) => (
          <Button
            key={entry.id}
            role="tab"
            id={`logs-tab-${entry.id}`}
            aria-selected={tab === entry.id}
            aria-controls={`logs-panel-${entry.id}`}
            variant={tab === entry.id ? 'secondary' : 'ghost'}
            onClick={() => {
              setTab(entry.id)
            }}
          >
            {entry.label}
          </Button>
        ))}
      </div>

      {tab === 'requests' ? (
        <div
          className="u-stack"
          id="logs-panel-requests"
          role="tabpanel"
          aria-labelledby="logs-tab-requests"
        >
          <Card title={t('logs.filtersTitle')} description={t('logs.filtersDescription')}>
            <div className="u-stack">
              <div
                style={{
                  display: 'grid',
                  gap: 'var(--space-3)',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                }}
              >
                <Input
                  label={t('console:field.requestId')}
                  mono
                  showOptional
                  value={filters.requestId}
                  placeholder="0c9f1f0e-..."
                  onChange={(event) => {
                    setFilters((current) => ({ ...current, requestId: event.target.value }))
                  }}
                />
                <Input
                  label={t('console:field.endpoint')}
                  mono
                  showOptional
                  value={filters.endpoint}
                  placeholder="douyin.content_detail"
                  onChange={(event) => {
                    setFilters((current) => ({ ...current, endpoint: event.target.value }))
                  }}
                />
                <Input
                  label={t('console:field.identityId')}
                  mono
                  showOptional
                  value={filters.identityId}
                  onChange={(event) => {
                    setFilters((current) => ({ ...current, identityId: event.target.value }))
                  }}
                />
                <Select
                  label={t('logs.range')}
                  value={filters.minutes}
                  options={RANGES.map((range) => ({
                    value: range,
                    label: t(`logs.rangeOption.${range}`),
                  }))}
                  onChange={(event) => {
                    setFilters((current) => ({ ...current, minutes: event.target.value as Range }))
                  }}
                />
                <Select
                  label={t('logs.limit')}
                  value={filters.limit}
                  options={LIMITS.map((limit) => ({ value: limit, label: limit }))}
                  onChange={(event) => {
                    setFilters((current) => ({ ...current, limit: event.target.value }))
                  }}
                />
              </div>

              <div className="u-stack-sm">
                <span className="u-xs u-muted" id="logs-outcome-label">
                  {t('console:field.outcome')}
                </span>
                <div className="u-row u-wrap" role="group" aria-labelledby="logs-outcome-label">
                  {OUTCOMES.map((outcome) => {
                    const selected = filters.outcomes.includes(outcome)
                    return (
                      <Button
                        key={outcome}
                        size="sm"
                        variant={selected ? 'secondary' : 'ghost'}
                        aria-pressed={selected}
                        onClick={() => {
                          toggleOutcome(outcome)
                        }}
                      >
                        <StatusBadge kind="outcome" value={outcome} size="sm" flash={false} />
                      </Button>
                    )
                  })}
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={filters.outcomes.length === 0}
                    onClick={() => {
                      setFilters((current) => ({ ...current, outcomes: [] }))
                    }}
                  >
                    {t('common:value.all')}
                  </Button>
                </div>
              </div>

              <div className="u-row u-wrap">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setFilters(DEFAULT_REQUEST_FILTERS)
                  }}
                >
                  {t('common:action.reset')}
                </Button>
                <span className="u-xs u-muted">
                  {t('common:time.localTimeZone', { zone: timeZone.short })} ·{' '}
                  {t('common:time.utcNote')}
                </span>
              </div>
            </div>
          </Card>

          <DataTable
            columns={requestColumns}
            rows={requestRows}
            getRowId={(row) => `${row.request_id}-${row.ts}`}
            loading={requests.isLoading}
            error={requests.isError ? requests.error : undefined}
            onRetry={() => {
              void requests.refetch()
            }}
            storageKey="logs-requests"
            defaultSort={{ columnId: 'ts', direction: 'desc' }}
            flashValue={(row) => row.outcome}
            onRowClick={(row) => {
              setOpenRequest(row)
            }}
            emptyTitle={t('logs.emptyTitle')}
            emptyDescription={t('logs.emptyDescription')}
            caption={t('logs.tab.requests')}
            maxHeight="60vh"
          />
        </div>
      ) : (
        <div
          className="u-stack"
          id="logs-panel-audit"
          role="tabpanel"
          aria-labelledby="logs-tab-audit"
        >
          <Card title={t('logs.auditTitle')} description={t('logs.auditDescription')}>
            <div
              style={{
                display: 'grid',
                gap: 'var(--space-3)',
                gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              }}
            >
              <Input
                label={t('logs.field.action')}
                mono
                showOptional
                value={auditFilters.action}
                placeholder="api_key.created"
                onChange={(event) => {
                  setAuditFilters((current) => ({ ...current, action: event.target.value }))
                }}
              />
              <Select
                label={t('logs.limit')}
                value={auditFilters.limit}
                options={LIMITS.map((limit) => ({ value: limit, label: limit }))}
                onChange={(event) => {
                  setAuditFilters((current) => ({ ...current, limit: event.target.value }))
                }}
              />
            </div>
          </Card>

          <DataTable
            columns={auditColumns}
            rows={auditRows}
            getRowId={(row) => row.id}
            loading={audit.isLoading}
            error={audit.isError ? audit.error : undefined}
            onRetry={() => {
              void audit.refetch()
            }}
            storageKey="logs-audit"
            defaultSort={{ columnId: 'ts', direction: 'desc' }}
            onRowClick={(row) => {
              setOpenAudit(row)
            }}
            emptyTitle={t('logs.auditEmptyTitle')}
            emptyDescription={t('logs.auditEmptyDescription')}
            caption={t('logs.tab.audit')}
            maxHeight="60vh"
          />
        </div>
      )}

      <Drawer
        open={openRequest !== null}
        onClose={() => {
          setOpenRequest(null)
        }}
        title={t('logs.detailTitle')}
        description={openRequest ? format.timestamp(openRequest.ts) : undefined}
      >
        {openRequest ? (
          <div className="u-stack">
            <div className="u-row u-wrap">
              <StatusBadge kind="outcome" value={openRequest.outcome} />
              {openRequest.error_code ? <ErrorCodeBadge code={openRequest.error_code} /> : null}
              <span className="u-mono u-xs">
                {openRequest.http_status ?? '—'} · {format.latency(openRequest.duration_ms)}
              </span>
            </div>

            <dl
              style={{
                margin: 0,
                display: 'grid',
                gridTemplateColumns: 'auto 1fr',
                gap: 'var(--space-2) var(--space-3)',
                alignItems: 'center',
              }}
            >
              <dt className="u-xs u-muted">{t('console:field.requestId')}</dt>
              <dd style={{ margin: 0 }}>
                <CopyableId value={openRequest.request_id} />
              </dd>
              <dt className="u-xs u-muted">{t('console:field.taskId')}</dt>
              <dd style={{ margin: 0 }}>
                <CopyableId value={openRequest.task_id} />
              </dd>
              <dt className="u-xs u-muted">{t('console:field.endpoint')}</dt>
              <dd className="u-mono" style={{ margin: 0 }}>
                {openRequest.endpoint}
              </dd>
              <dt className="u-xs u-muted">{t('console:field.identity')}</dt>
              <dd style={{ margin: 0 }}>
                <CopyableId value={openRequest.identity_id} />
              </dd>
              <dt className="u-xs u-muted">{t('console:field.proxy')}</dt>
              <dd style={{ margin: 0 }}>
                <CopyableId value={openRequest.proxy_id} />
              </dd>
              <dt className="u-xs u-muted">{t('console:field.signer')}</dt>
              <dd className="u-mono" style={{ margin: 0 }}>
                {openRequest.signer ?? '—'}
              </dd>
              <dt className="u-xs u-muted">{t('logs.field.rejectReason')}</dt>
              <dd style={{ margin: 0 }}>
                <RejectReasonCell value={openRequest.reject_reason} />
              </dd>
              <dt className="u-xs u-muted">{t('console:field.cacheHit')}</dt>
              <dd className="u-mono" style={{ margin: 0 }}>
                {openRequest.cache_hit ? t('common:value.yes') : t('common:value.no')}
              </dd>
            </dl>

            <CodeBlock json={openRequest} title={t('logs.detailRaw')} defaultCollapsed />
          </div>
        ) : null}
      </Drawer>

      <Drawer
        open={openAudit !== null}
        onClose={() => {
          setOpenAudit(null)
        }}
        title={t('logs.auditDetailTitle')}
        description={openAudit ? format.timestamp(openAudit.ts) : undefined}
      >
        {openAudit ? (
          <div className="u-stack">
            <span className="u-mono">{openAudit.action}</span>
            <dl
              style={{
                margin: 0,
                display: 'grid',
                gridTemplateColumns: 'auto 1fr',
                gap: 'var(--space-2) var(--space-3)',
                alignItems: 'center',
              }}
            >
              <dt className="u-xs u-muted">{t('logs.field.actor')}</dt>
              <dd style={{ margin: 0 }}>
                {openAudit.username ?? openAudit.user_id ?? t('logs.bySystem')}
              </dd>
              <dt className="u-xs u-muted">{t('logs.field.target')}</dt>
              <dd className="u-mono" style={{ margin: 0 }}>
                {openAudit.target_type ?? '—'} {openAudit.target_id ?? ''}
              </dd>
              <dt className="u-xs u-muted">{t('logs.field.ip')}</dt>
              <dd className="u-mono" style={{ margin: 0 }}>
                {openAudit.ip ?? '—'}
              </dd>
            </dl>
            <CodeBlock json={openAudit.detail ?? openAudit} title={t('logs.detailRaw')} />
          </div>
        ) : null}
      </Drawer>
    </div>
  )
}
