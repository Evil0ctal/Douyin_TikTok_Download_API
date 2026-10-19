import { useEffect, useMemo, useState } from 'react'
import { useInfiniteQuery, type InfiniteData, type QueryKey } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'wouter'

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
import { useApiQuery, useFormatters, useSession } from '@/hooks'
import { apiGet, type ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { atLeast } from '@/lib/roles'
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
 *
 * The filters other pages link to live in the address -
 * `?tab=requests&identity_id=&proxy_id=&endpoint=` and `?tab=audit&user_id=` -
 * so "show me what this proxy did" is a link rather than instructions.
 */

type Tab = 'requests' | 'audit'

/** Relative windows, in minutes. A relative window keeps the query key stable across polls. */
const RANGES = ['15', '60', '360', '1440', '10080', '43200'] as const
type Range = (typeof RANGES)[number]

const LIMITS = ['100', '200', '500'] as const

/**
 * How long a typed filter sits before it is applied. Every applied change is a
 * scan of the request log, and a pasted id arrives as one change anyway.
 */
const FILTER_DEBOUNCE_MS = 350

/**
 * What the server's `uuid.UUID` accepts, minus the braces and urn forms nobody
 * types. Checked here because the server answers anything else with a 422, and
 * before this check every keystroke of a half-typed id flashed one.
 */
const UUID_PATTERN = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i

function isUuid(value: string): boolean {
  return UUID_PATTERN.test(value.trim())
}

/** The id filters, as typed. Keys are the address parameters they are kept in. */
const ID_FIELDS = ['request_id', 'identity_id', 'proxy_id'] as const
type IdField = (typeof ID_FIELDS)[number]
type IdDrafts = Record<IdField, string>

const EMPTY_DRAFTS: IdDrafts = { request_id: '', identity_id: '', proxy_id: '' }

function draftsFrom(params: URLSearchParams): IdDrafts {
  return {
    request_id: params.get('request_id') ?? '',
    identity_id: params.get('identity_id') ?? '',
    proxy_id: params.get('proxy_id') ?? '',
  }
}

/**
 * The address with every id filter that is complete applied to it.
 *
 * An incomplete one is left out rather than written: the address only ever
 * holds a filter the server will accept, so a link copied mid-edit still opens.
 */
function withDrafts(params: URLSearchParams, drafts: IdDrafts): URLSearchParams {
  const next = new URLSearchParams(params)
  for (const field of ID_FIELDS) {
    const value = drafts[field].trim()
    if (!value) next.delete(field)
    else if (isUuid(value)) next.set(field, value)
  }
  return next
}

/**
 * Follow the address where it moved without us - a link, the back button, the
 * sidebar - but never over an id that is still being typed: the address does
 * not hold incomplete ids, so it has nothing to say about one.
 */
function reconcile(current: IdDrafts, fromUrl: IdDrafts): IdDrafts {
  let changed = false
  const next = { ...current }
  for (const field of ID_FIELDS) {
    const draft = current[field].trim()
    if ((draft === '' || isUuid(draft)) && draft !== fromUrl[field]) {
      next[field] = fromUrl[field]
      changed = true
    }
  }
  return changed ? next : current
}

function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSettled(value)
    }, delay)
    return () => {
      window.clearTimeout(timer)
    }
  }, [value, delay])
  return settled
}

interface RequestFilters {
  outcomes: Outcome[]
  minutes: Range
  limit: string
}

const DEFAULT_REQUEST_FILTERS: RequestFilters = {
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

/** The last row shown, which is where the next older page starts. */
interface AuditCursor {
  ts: string
  id: string
}

/** GET /admin/endpoints/health, the half this page reads: every declared operation. */
interface EndpointHealthRow {
  endpoint: string
  platform: string
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

/**
 * Who did it, the same way in the table and in the drawer.
 *
 * The drawer once had its own copy that ignored `api_key_id`, so an action
 * taken with a key read "System" there while the row it was opened from said
 * "API key". A key belongs to an account, so a row can carry both; the account
 * is the answer and the key is how they did it.
 */
function Actor({ row }: { row: AuditRow }) {
  const { t } = useTranslation('console')
  const viaKey = row.api_key_id ? (
    <span className="u-row">
      <span className="u-xs u-muted">{t('logs.byApiKey')}</span>
      <CopyableId value={row.api_key_id} />
    </span>
  ) : null

  if (row.username || row.user_id) {
    return (
      <span className="u-row u-wrap">
        {row.username ? (
          <span className="u-truncate" title={row.user_id ?? undefined}>
            {row.username}
          </span>
        ) : (
          <CopyableId value={row.user_id} />
        )}
        {viaKey}
      </span>
    )
  }
  return viaKey ?? <span className="u-muted">{t('logs.bySystem')}</span>
}

function asRows<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object') {
    const items = (payload as { items?: unknown }).items
    if (Array.isArray(items)) return items as T[]
  }
  return []
}

/** Every declared endpoint, grouped by platform, for the endpoint picker. */
function endpointGroups(rows: EndpointHealthRow[], current: string): Array<[string, string[]]> {
  const groups = new Map<string, Set<string>>()
  for (const row of rows) {
    const bucket = groups.get(row.platform) ?? new Set<string>()
    bucket.add(row.endpoint)
    groups.set(row.platform, bucket)
  }
  // A link can name an endpoint this build no longer declares. The picker
  // shows it rather than silently reading "every endpoint" over a filtered
  // table.
  const known = rows.some((row) => row.endpoint === current)
  if (current && !known) {
    const platform = current.split('.')[0] || 'other'
    const bucket = groups.get(platform) ?? new Set<string>()
    bucket.add(current)
    groups.set(platform, bucket)
  }
  return [...groups.entries()]
    .map(([platform, names]): [string, string[]] => [platform, [...names].sort()])
    .sort(([a], [b]) => a.localeCompare(b))
}

const DETAIL_GRID = {
  margin: 0,
  display: 'grid',
  gridTemplateColumns: 'auto 1fr',
  gap: 'var(--space-2) var(--space-3)',
  alignItems: 'center',
} as const

export default function Logs() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const session = useSession()
  const role = session.data?.role ?? null
  // The audit route is a read_admin one: the demo account is refused it, so
  // the tab is not offered rather than offered and then answered with a 403.
  const canReadAudit = atLeast(role, 'viewer')

  const [searchParams, setSearchParams] = useSearchParams()
  const search = searchParams.toString()

  const tab: Tab = searchParams.get('tab') === 'audit' && canReadAudit ? 'audit' : 'requests'
  const [live, setLive] = useState(true)
  const [filters, setFilters] = useState<RequestFilters>(DEFAULT_REQUEST_FILTERS)
  const [auditFilters, setAuditFilters] = useState<AuditFilters>(DEFAULT_AUDIT_FILTERS)
  const [openRequest, setOpenRequest] = useState<RequestLogRow | null>(null)
  const [openAudit, setOpenAudit] = useState<AuditRow | null>(null)

  const [drafts, setDrafts] = useState<IdDrafts>(() => draftsFrom(searchParams))
  const [seenSearch, setSeenSearch] = useState(search)
  if (search !== seenSearch) {
    setSeenSearch(search)
    setDrafts((current) => reconcile(current, draftsFrom(searchParams)))
  }
  const settled = useDebounced(drafts, FILTER_DEBOUNCE_MS)
  const settledAction = useDebounced(auditFilters.action, FILTER_DEBOUNCE_MS)

  // Once typing stops, complete ids go into the address and the address is
  // what the query reads. Compared as strings, so a poll re-rendering the page
  // writes nothing. Only while the debounced copy has caught up: right after
  // Reset or a "show only this" it still holds the ids from before, and
  // writing those back undid the click.
  const typingSettled = ID_FIELDS.every((field) => settled[field] === drafts[field])
  const desired = typingSettled ? withDrafts(searchParams, settled).toString() : search
  useEffect(() => {
    if (desired !== search) setSearchParams(desired, { replace: true })
  }, [desired, search, setSearchParams])

  /** Change the address now, carrying every complete id typed so far with it. */
  const updateParams = (patch: Record<string, string | null>): void => {
    const next = withDrafts(searchParams, drafts)
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    setSearchParams(next, { replace: true })
  }

  const showOnly = (field: IdField, value: string): void => {
    setDrafts((current) => ({ ...current, [field]: value }))
    updateParams({ tab: 'requests', [field]: value })
    setOpenRequest(null)
  }

  const applied = {
    requestId: searchParams.get('request_id') ?? '',
    identityId: searchParams.get('identity_id') ?? '',
    proxyId: searchParams.get('proxy_id') ?? '',
    endpoint: searchParams.get('endpoint') ?? '',
    userId: searchParams.get('user_id') ?? '',
  }

  const requestParams = useMemo(
    () => ({
      request_id: isUuid(applied.requestId) ? applied.requestId : undefined,
      identity_id: isUuid(applied.identityId) ? applied.identityId : undefined,
      proxy_id: isUuid(applied.proxyId) ? applied.proxyId : undefined,
      endpoint: applied.endpoint || undefined,
      outcome: filters.outcomes.length > 0 ? [...filters.outcomes] : undefined,
      minutes: Number(filters.minutes),
      limit: Number(filters.limit),
    }),
    [
      applied.requestId,
      applied.identityId,
      applied.proxyId,
      applied.endpoint,
      filters.outcomes,
      filters.minutes,
      filters.limit,
    ],
  )

  const requests = useApiQuery<unknown>({
    key: ['admin', 'logs', 'requests', requestParams],
    path: paths.logs.requests,
    params: requestParams,
    enabled: tab === 'requests',
    poll: live ? POLL.fast : false,
  })

  // The endpoint filter matches exactly (src/dtk/db/timeseries.py), so it is
  // a choice from the registry rather than a box to type a name into. The
  // health route lists every declared operation whether or not it has traffic.
  const health = useApiQuery<EndpointHealthRow[]>({
    key: ['admin', 'endpoints-health'],
    path: paths.endpointsHealth,
    enabled: tab === 'requests',
  })
  const endpointOptions = useMemo(
    () => endpointGroups(Array.isArray(health.data) ? health.data : [], applied.endpoint),
    [health.data, applied.endpoint],
  )

  const auditLimit = Number(auditFilters.limit)
  const auditParams = useMemo(
    () => ({
      action: settledAction.trim() || undefined,
      user_id: isUuid(applied.userId) ? applied.userId : undefined,
      limit: auditLimit,
    }),
    [settledAction, applied.userId, auditLimit],
  )

  // Paged by the last row shown. A refetch re-reads every loaded page from the
  // newest down, so polling while older pages are open cannot leave a gap
  // between the first page and the ones under it.
  const audit = useInfiniteQuery<
    AuditRow[],
    ApiError,
    InfiniteData<AuditRow[], AuditCursor | null>,
    QueryKey,
    AuditCursor | null
  >({
    queryKey: ['admin', 'logs', 'audit', auditParams],
    queryFn: async ({ pageParam, signal }) =>
      asRows<AuditRow>(
        await apiGet<unknown>(paths.logs.audit, {
          params: {
            ...auditParams,
            ...(pageParam ? { before: pageParam.ts, before_id: pageParam.id } : {}),
          },
          signal,
        }),
      ),
    initialPageParam: null,
    getNextPageParam: (lastPage) => {
      const last = lastPage[lastPage.length - 1]
      return lastPage.length >= auditLimit && last ? { ts: last.ts, id: last.id } : undefined
    },
    enabled: tab === 'audit',
    refetchInterval: live ? POLL.slow : false,
    meta: { path: paths.logs.audit },
  })

  const requestRows = useMemo(() => asRows<RequestLogRow>(requests.data), [requests.data])
  const auditRows = useMemo(() => {
    const seen = new Set<string>()
    return (audit.data?.pages ?? []).flat().filter((row) => {
      if (seen.has(row.id)) return false
      seen.add(row.id)
      return true
    })
  }, [audit.data])

  /**
   * The accounts to filter by, from the rows on screen. The account list
   * itself is administrator-only, and a viewer reading the trail still needs
   * "only what this person did".
   */
  const actorOptions = useMemo(() => {
    const names = new Map<string, string>()
    for (const row of auditRows) {
      if (row.user_id && !names.has(row.user_id)) names.set(row.user_id, row.username ?? row.user_id)
    }
    if (applied.userId && !names.has(applied.userId)) names.set(applied.userId, applied.userId)
    return [...names.entries()].sort(([, a], [, b]) => a.localeCompare(b))
  }, [auditRows, applied.userId])

  const timeZone = format.timeZone()

  const toggleOutcome = (outcome: Outcome): void => {
    setFilters((current) => ({
      ...current,
      outcomes: current.outcomes.includes(outcome)
        ? current.outcomes.filter((entry) => entry !== outcome)
        : [...current.outcomes, outcome],
    }))
  }

  const idError = (field: IdField): string | undefined => {
    const value = settled[field].trim()
    // Only once typing has stopped, and only for what is on screen now.
    if (!value || value !== drafts[field].trim() || isUuid(value)) return undefined
    return t('logs.invalidId')
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
        cell: (row) => <Actor row={row} />,
        sortValue: (row) => row.username ?? row.user_id ?? row.api_key_id ?? null,
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
    ...(canReadAudit ? [{ id: 'audit' as const, label: t('logs.tab.audit') }] : []),
  ]

  const idInput = (field: IdField, label: string, placeholder?: string) => (
    <Input
      label={label}
      mono
      showOptional
      spellCheck={false}
      autoComplete="off"
      value={drafts[field]}
      placeholder={placeholder}
      error={idError(field)}
      onChange={(event) => {
        const value = event.target.value
        setDrafts((current) => ({ ...current, [field]: value }))
      }}
    />
  )

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

      <div className="u-row u-row-wrap" role="tablist" aria-label={t('page.logs.title')}>
        {tabs.map((entry) => (
          <Button
            key={entry.id}
            role="tab"
            id={`logs-tab-${entry.id}`}
            aria-selected={tab === entry.id}
            aria-controls={`logs-panel-${entry.id}`}
            variant={tab === entry.id ? 'secondary' : 'ghost'}
            onClick={() => {
              updateParams({ tab: entry.id })
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
                {idInput('request_id', t('console:field.requestId'), '0c9f1f0e-...')}
                <Select
                  label={t('console:field.endpoint')}
                  showOptional
                  className="u-mono"
                  value={applied.endpoint}
                  onChange={(event) => {
                    updateParams({ endpoint: event.target.value || null })
                  }}
                >
                  <option value="">{t('logs.endpointAll')}</option>
                  {endpointOptions.map(([platform, names]) => (
                    <optgroup key={platform} label={platform}>
                      {names.map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </Select>
                {idInput('identity_id', t('console:field.identityId'))}
                {idInput('proxy_id', t('logs.field.proxyId'))}
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
                    setDrafts(EMPTY_DRAFTS)
                    updateParams({
                      request_id: null,
                      identity_id: null,
                      proxy_id: null,
                      endpoint: null,
                    })
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
                label={t('logs.field.actor')}
                showOptional
                value={applied.userId}
                onChange={(event) => {
                  updateParams({ user_id: event.target.value || null })
                }}
              >
                <option value="">{t('logs.actorAll')}</option>
                {actorOptions.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </Select>
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

          {auditRows.length > 0 ? (
            <div className="u-row u-wrap">
              {audit.hasNextPage ? (
                <Button
                  size="sm"
                  variant="secondary"
                  loading={audit.isFetchingNextPage}
                  onClick={() => {
                    void audit.fetchNextPage()
                  }}
                >
                  {t('logs.loadOlder')}
                </Button>
              ) : (
                <span className="u-xs u-muted">{t('logs.noOlder')}</span>
              )}
              <span className="u-xs u-muted">
                {t('logs.auditShown', { count: auditRows.length })}
              </span>
            </div>
          ) : null}
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

            <dl style={DETAIL_GRID}>
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
              <dd className="u-stack-sm" style={{ margin: 0 }}>
                <CopyableId value={openRequest.identity_id} />
                {openRequest.identity_id ? (
                  <span className="u-row u-wrap u-xs">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        if (openRequest.identity_id) showOnly('identity_id', openRequest.identity_id)
                      }}
                    >
                      {t('logs.onlyThisIdentity')}
                    </Button>
                    <Link href={`/identities?id=${encodeURIComponent(openRequest.identity_id)}`}>
                      {t('logs.openIdentity')}
                    </Link>
                  </span>
                ) : null}
              </dd>
              <dt className="u-xs u-muted">{t('console:field.proxy')}</dt>
              <dd className="u-stack-sm" style={{ margin: 0 }}>
                <CopyableId value={openRequest.proxy_id} />
                {openRequest.proxy_id ? (
                  <span className="u-row u-wrap u-xs">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        if (openRequest.proxy_id) showOnly('proxy_id', openRequest.proxy_id)
                      }}
                    >
                      {t('logs.onlyThisProxy')}
                    </Button>
                  </span>
                ) : null}
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
            <dl style={DETAIL_GRID}>
              <dt className="u-xs u-muted">{t('logs.field.actor')}</dt>
              <dd className="u-stack-sm" style={{ margin: 0 }}>
                <Actor row={openAudit} />
                {openAudit.user_id && openAudit.user_id !== applied.userId ? (
                  <span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        updateParams({ user_id: openAudit.user_id ?? null })
                        setOpenAudit(null)
                      }}
                    >
                      {t('logs.onlyThisActor')}
                    </Button>
                  </span>
                ) : null}
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
