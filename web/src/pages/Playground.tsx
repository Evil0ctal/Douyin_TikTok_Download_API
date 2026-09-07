import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  Checkbox,
  CodeBlock,
  CopyableId,
  EmptyState,
  ErrorCodeBadge,
  Input,
  PageHeader,
  Select,
  StatusBadge,
  useErrorInfo,
  type SelectOption,
} from '@/components'
import { useApiQuery, useFormatters } from '@/hooks'
import { apiRequest, isApiError, type ApiError, type ResponseMeta } from '@/lib/api'
import { API_V1, paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import {
  IDENTITY_STATES,
  PLATFORMS,
  type CircuitState,
  type IdentityState,
  type Platform,
  type RequestLogRow,
  type TaskState,
} from '@/lib/types'

/**
 * Endpoint playground.
 *
 * This page exists so that a dead endpoint can be diagnosed in three minutes.
 * A failure therefore never stops at "500": it shows the stable error code, the
 * request id, which identity served the call, whether that endpoint's circuit is
 * open right now and what the pool looks like - the four facts that separate
 * "the platform changed" from "we are out of identities"
 * (docs/design/07-frontend.md).
 *
 * The copy-ready snippets are the other half. For most users the first
 * successful call is a paste of the curl line, so the snippets are generated
 * from the form as it stands rather than from a static example.
 */

/* -------------------------------------------------------------------------- */
/* Endpoint catalog                                                            */
/* -------------------------------------------------------------------------- */

type ParamKind = 'text' | 'number' | 'boolean'
type ParamWhere = 'query' | 'body'

interface ParamDef {
  /** Wire name. Never translated (docs/design/14-i18n.md). */
  name: string
  kind: ParamKind
  where: ParamWhere
  required?: boolean
  /** Members of a group are alternatives: exactly one has to be filled in. */
  oneOf?: string
  placeholder?: string
  min?: number
  max?: number
  defaultValue?: string
}

interface EndpointDef {
  id: string
  method: 'GET' | 'POST'
  /** Path template, rendered with the selected platform. */
  path: (platform: Platform) => string
  /** Scheduler endpoint suffix used to look health up, or null for the front door. */
  operation: string | null
  platformScoped: boolean
  params: ParamDef[]
}

const URL_PARAM: ParamDef = {
  name: 'url',
  kind: 'text',
  where: 'query',
  oneOf: 'target',
  placeholder: 'https://www.douyin.com/video/7300000000000000000',
}

const WAIT_PARAM: ParamDef = { name: 'wait', kind: 'number', where: 'query', min: 0, max: 30 }
const CURSOR_PARAM: ParamDef = { name: 'cursor', kind: 'text', where: 'query' }
const COUNT_PARAM: ParamDef = { name: 'count', kind: 'number', where: 'query', min: 1, max: 50 }
const RAW_PARAM: ParamDef = {
  name: 'include_raw',
  kind: 'boolean',
  where: 'query',
  defaultValue: 'true',
}

const CATALOG: readonly EndpointDef[] = [
  {
    id: 'parse',
    method: 'POST',
    path: () => paths.parse,
    operation: null,
    platformScoped: false,
    params: [
      {
        name: 'url',
        kind: 'text',
        where: 'body',
        required: true,
        placeholder: 'https://v.douyin.com/iRNBho6G/',
      },
      { ...RAW_PARAM, where: 'body' },
      WAIT_PARAM,
    ],
  },
  {
    id: 'video',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/video`,
    operation: 'content_detail',
    platformScoped: true,
    params: [
      URL_PARAM,
      {
        name: 'aweme_id',
        kind: 'text',
        where: 'query',
        oneOf: 'target',
        placeholder: '7300000000000000000',
      },
      RAW_PARAM,
      WAIT_PARAM,
    ],
  },
  {
    id: 'comments',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/video/comments`,
    operation: 'comments',
    platformScoped: true,
    params: [
      URL_PARAM,
      {
        name: 'aweme_id',
        kind: 'text',
        where: 'query',
        oneOf: 'target',
        placeholder: '7300000000000000000',
      },
      CURSOR_PARAM,
      COUNT_PARAM,
      WAIT_PARAM,
    ],
  },
  {
    id: 'replies',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/video/comments/replies`,
    operation: 'comment_replies',
    platformScoped: true,
    params: [
      { name: 'comment_id', kind: 'text', where: 'query', required: true },
      { name: 'aweme_id', kind: 'text', where: 'query' },
      CURSOR_PARAM,
      COUNT_PARAM,
      WAIT_PARAM,
    ],
  },
  {
    id: 'user',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user`,
    operation: 'author_profile',
    platformScoped: true,
    params: [
      { ...URL_PARAM, placeholder: 'https://www.douyin.com/user/MS4wLjABAAAA' },
      {
        name: 'sec_user_id',
        kind: 'text',
        where: 'query',
        oneOf: 'target',
        placeholder: 'MS4wLjABAAAA',
      },
      RAW_PARAM,
      WAIT_PARAM,
    ],
  },
  {
    id: 'posts',
    method: 'GET',
    path: (platform) => `${API_V1}/${platform}/user/posts`,
    operation: 'author_posts',
    platformScoped: true,
    params: [
      { ...URL_PARAM, placeholder: 'https://www.douyin.com/user/MS4wLjABAAAA' },
      {
        name: 'sec_user_id',
        kind: 'text',
        where: 'query',
        oneOf: 'target',
        placeholder: 'MS4wLjABAAAA',
      },
      CURSOR_PARAM,
      COUNT_PARAM,
      WAIT_PARAM,
    ],
  },
]

const DEFAULT_ENDPOINT = CATALOG[0] as EndpointDef

/* -------------------------------------------------------------------------- */
/* Health and pool shapes                                                      */
/* -------------------------------------------------------------------------- */

/**
 * Tolerant view of one health row: the scheduler reports `circuit_open`, the
 * console's own contract carries a three-state `circuit`. Read whichever is
 * present rather than blanking the panel that explains an outage.
 */
interface EndpointHealthRow {
  endpoint: string
  platform?: string
  circuit?: CircuitState
  circuit_open?: boolean
  retry_after?: number | null
  reason?: string | null
  samples?: number | null
  requests?: number | null
  success_rate?: number | null
  risk_rate?: number | null
  last_success_at?: string | null
}

function circuitOf(row: EndpointHealthRow): CircuitState {
  if (row.circuit) return row.circuit
  return row.circuit_open ? 'open' : 'closed'
}

type PoolCounts = Record<IdentityState, number>

const EMPTY_POOL: PoolCounts = {
  minting: 0,
  active: 0,
  cooling: 0,
  degraded: 0,
  retired: 0,
}

/**
 * Sums identity counts out of whatever shape the status endpoint used: a flat
 * census, or one census per platform.
 */
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

interface SystemStatusLike {
  pool?: unknown
}

function asRows<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object') {
    const items = (payload as { items?: unknown }).items
    if (Array.isArray(items)) return items as T[]
  }
  return []
}

/* -------------------------------------------------------------------------- */
/* Payload helpers                                                             */
/* -------------------------------------------------------------------------- */

const MAX_STRIP_DEPTH = 8

/** The normalized view: the same payload with every `raw` island removed. */
function stripRaw(value: unknown, depth = 0): unknown {
  if (depth > MAX_STRIP_DEPTH) return value
  if (Array.isArray(value)) return value.map((entry) => stripRaw(entry, depth + 1))
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
      if (key === 'raw') continue
      out[key] = stripRaw(entry, depth + 1)
    }
    return out
  }
  return value
}

/** The platform's own payload, which only exists when include_raw was set. */
function pickRaw(value: unknown): unknown {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if (record['raw']) return record['raw']
  const items = record['items']
  if (Array.isArray(items)) {
    const raws = items
      .map((item) =>
        item && typeof item === 'object' ? (item as Record<string, unknown>)['raw'] : null,
      )
      .filter((entry) => entry != null)
    return raws.length > 0 ? raws : null
  }
  return null
}

/* -------------------------------------------------------------------------- */
/* Snippets                                                                    */
/* -------------------------------------------------------------------------- */

const SNIPPET_LANGUAGES = ['curl', 'python', 'javascript'] as const
type SnippetLanguage = (typeof SNIPPET_LANGUAGES)[number]

const KEY_PLACEHOLDER = 'dtk_xxxxxxxx_your_key_here'

interface SnippetInput {
  method: 'GET' | 'POST'
  origin: string
  path: string
  query: Record<string, string>
  body: Record<string, unknown> | null
  language: string
}

function queryString(query: Record<string, string>): string {
  const search = new URLSearchParams(query).toString()
  return search ? `?${search}` : ''
}

function buildSnippet(kind: SnippetLanguage, input: SnippetInput): string {
  const { method, origin, path, query, body, language } = input
  const fullUrl = `${origin}${path}${queryString(query)}`
  const jsonBody = body ? JSON.stringify(body) : null

  if (kind === 'curl') {
    const lines = [
      `curl -X ${method} "${fullUrl}"`,
      `  -H "Authorization: Bearer ${KEY_PLACEHOLDER}"`,
      `  -H "Accept-Language: ${language}"`,
    ]
    if (jsonBody) {
      lines.push('  -H "Content-Type: application/json"')
      lines.push(`  -d '${jsonBody}'`)
    }
    return lines.join(' \\\n')
  }

  if (kind === 'python') {
    const args = [`    "${origin}${path}"`]
    if (Object.keys(query).length > 0) args.push(`    params=${JSON.stringify(query)}`)
    if (body) args.push(`    json=${JSON.stringify(body)}`)
    args.push(
      `    headers={"Authorization": "Bearer ${KEY_PLACEHOLDER}", "Accept-Language": "${language}"}`,
    )
    args.push('    timeout=60.0')
    return [
      'import httpx',
      '',
      `response = httpx.${method.toLowerCase()}(`,
      `${args.join(',\n')},`,
      ')',
      'response.raise_for_status()',
      'payload = response.json()',
      'print(payload["data"])',
    ].join('\n')
  }

  const init = [`  method: "${method}"`, '  headers: {']
  init.push(`    Authorization: "Bearer ${KEY_PLACEHOLDER}",`)
  init.push(`    "Accept-Language": "${language}",`)
  if (jsonBody) init.push('    "Content-Type": "application/json",')
  init.push('  }')
  if (jsonBody) init.push(`  body: JSON.stringify(${jsonBody})`)

  return [
    `const response = await fetch("${fullUrl}", {`,
    `${init.join(',\n')},`,
    '})',
    'const payload = await response.json()',
    'if (!payload.success) throw new Error(payload.error.code)',
    'console.log(payload.data)',
  ].join('\n')
}

/* -------------------------------------------------------------------------- */
/* Run state                                                                   */
/* -------------------------------------------------------------------------- */

interface RunResult {
  ok: boolean
  method: 'GET' | 'POST'
  url: string
  startedAt: number
  elapsedMs: number
  httpStatus: number | null
  data?: unknown
  meta?: ResponseMeta
  error?: unknown
  requestId: string | null
  healthEndpoint: string | null
}

export default function Playground() {
  const { t, i18n } = useTranslation(['console', 'common'])
  const format = useFormatters()

  const [endpointId, setEndpointId] = useState<string>(DEFAULT_ENDPOINT.id)
  const [platform, setPlatform] = useState<Platform>('douyin')
  const [values, setValues] = useState<Record<string, string>>({ include_raw: 'true' })
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [running, setRunning] = useState(false)
  const [taskState, setTaskState] = useState<TaskState | null>(null)
  const [result, setResult] = useState<RunResult | null>(null)
  const [snippet, setSnippet] = useState<SnippetLanguage>('curl')
  const abortRef = useRef<AbortController | null>(null)

  const endpoint = useMemo(
    () => CATALOG.find((entry) => entry.id === endpointId) ?? DEFAULT_ENDPOINT,
    [endpointId],
  )

  const healthEndpointName = endpoint.operation
    ? endpoint.platformScoped
      ? `${platform}.${endpoint.operation}`
      : endpoint.operation
    : null

  const health = useApiQuery<EndpointHealthRow[]>({
    key: ['admin', 'endpoints-health'],
    path: paths.endpointsHealth,
    poll: POLL.fast,
  })

  const system = useApiQuery<SystemStatusLike>({
    key: ['system', 'status'],
    path: paths.system.status,
    poll: POLL.fast,
  })

  const requestId = result?.requestId ?? null

  const logLookupRows = useApiQuery<unknown>({
    key: ['playground', 'request-log', requestId ?? 'none'],
    path: paths.logs.requests,
    params: { request_id: requestId ?? '', limit: 1 },
    enabled: requestId !== null,
    retry: false,
  })

  const servedBy = useMemo(
    () => asRows<RequestLogRow>(logLookupRows.data)[0] ?? null,
    [logLookupRows.data],
  )

  const errorInfo = useErrorInfo(result?.error)

  /* ---------------------------------------------------------------- form -- */

  const setValue = (name: string, value: string): void => {
    setValues((current) => ({ ...current, [name]: value }))
  }

  const valueOf = (param: ParamDef, overrides?: Record<string, string>): string =>
    overrides?.[param.name] ?? values[param.name] ?? param.defaultValue ?? ''

  /** Overrides let a button ("include the raw payload and run again") apply a value
   *  and send in the same click, without waiting for the state to settle. */
  const buildRequest = (
    overrides?: Record<string, string>,
  ): {
    query: Record<string, string>
    body: Record<string, unknown> | null
    errors: Record<string, string>
  } => {
    const query: Record<string, string> = {}
    const body: Record<string, unknown> = {}
    const errors: Record<string, string> = {}
    const groupsSeen = new Set<string>()
    const groupsFilled = new Set<string>()

    for (const param of endpoint.params) {
      const raw = valueOf(param, overrides).trim()
      if (param.oneOf) {
        groupsSeen.add(param.oneOf)
        if (raw) groupsFilled.add(param.oneOf)
      }

      if (!raw) {
        if (param.required) errors[param.name] = t('playground.required')
        continue
      }

      if (param.kind === 'number') {
        const parsed = Number(raw)
        if (!Number.isFinite(parsed)) {
          errors[param.name] = t('playground.notANumber')
          continue
        }
        if (param.min !== undefined && parsed < param.min) {
          errors[param.name] = t('playground.outOfRange', { min: param.min, max: param.max ?? 0 })
          continue
        }
        if (param.max !== undefined && parsed > param.max) {
          errors[param.name] = t('playground.outOfRange', { min: param.min ?? 0, max: param.max })
          continue
        }
      }

      const parsedValue: unknown = param.kind === 'boolean' ? raw === 'true' : raw
      if (param.where === 'body') body[param.name] = parsedValue
      else query[param.name] = String(parsedValue)
    }

    for (const group of groupsSeen) {
      if (groupsFilled.has(group)) continue
      for (const param of endpoint.params) {
        if (param.oneOf === group) errors[param.name] = t('playground.oneOfRequired')
      }
    }

    return { query, body: endpoint.method === 'POST' ? body : null, errors }
  }

  const preview = buildRequest()
  const path = endpoint.path(platform)

  const snippetText = useMemo(
    () =>
      buildSnippet(snippet, {
        method: endpoint.method,
        origin: window.location.origin,
        path,
        query: preview.query,
        body: preview.body,
        language: i18n.language.startsWith('zh') ? 'zh' : 'en',
      }),
    // The snippet mirrors the form, so it is rebuilt whenever the form changes.
    [snippet, endpoint.method, path, preview.query, preview.body, i18n.language],
  )

  const send = async (overrides?: Record<string, string>): Promise<void> => {
    const { query, body, errors } = buildRequest(overrides)
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) return

    const controller = new AbortController()
    abortRef.current = controller
    setRunning(true)
    setTaskState(null)
    setResult(null)

    const startedAt = Date.now()
    const startedPerf = performance.now()

    try {
      const response = await apiRequest<unknown>(path, {
        method: endpoint.method,
        params: query,
        body: body ?? undefined,
        signal: controller.signal,
        timeoutMs: 60_000,
        taskTimeoutMs: 180_000,
        onTaskState: (task) => {
          setTaskState(task.state)
        },
      })
      setResult({
        ok: true,
        method: endpoint.method,
        url: `${path}${queryString(query)}`,
        startedAt,
        elapsedMs: performance.now() - startedPerf,
        httpStatus: response.status,
        data: response.data,
        meta: response.meta,
        requestId: response.meta.request_id ?? null,
        healthEndpoint: healthEndpointName,
      })
    } catch (error) {
      const apiError: ApiError | null = isApiError(error) ? error : null
      setResult({
        ok: false,
        method: endpoint.method,
        url: `${path}${queryString(query)}`,
        startedAt,
        elapsedMs: performance.now() - startedPerf,
        httpStatus: apiError?.status ?? null,
        error,
        requestId: apiError?.requestId ?? null,
        healthEndpoint: healthEndpointName,
      })
    } finally {
      abortRef.current = null
      setRunning(false)
    }
  }

  /* -------------------------------------------------------------- health -- */

  const healthRows = health.data ?? []
  const matchedHealth = healthEndpointName
    ? (healthRows.find((row) => row.endpoint === healthEndpointName) ?? null)
    : null
  const openCircuits = healthRows.filter((row) => circuitOf(row) !== 'closed')
  const pool = poolCounts(system.data?.pool)

  const endpointOptions: SelectOption[] = CATALOG.map((entry) => ({
    value: entry.id,
    label: `${t(`playground.endpoint.${entry.id}`)} · ${entry.method} ${entry.path(platform)}`,
  }))

  const normalized = result?.ok ? stripRaw(result.data) : null
  const raw = result?.ok ? pickRaw(result.data) : null

  return (
    <div className="u-stack-lg">
      <PageHeader
        title={t('page.playground.title')}
        description={t('page.playground.description')}
        badge={
          running ? (
            <StatusBadge kind="task" value={taskState ?? 'queued'} size="sm" />
          ) : result ? (
            <StatusBadge kind="task" value={result.ok ? 'done' : 'failed'} size="sm" />
          ) : null
        }
      />

      <div
        style={{
          display: 'grid',
          gap: 'var(--space-4)',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          alignItems: 'start',
        }}
      >
        <Card
          title={t('playground.requestTitle')}
          description={t('playground.requestDescription')}
        >
          <form
            className="u-stack"
            onSubmit={(event) => {
              event.preventDefault()
              void send()
            }}
          >
            <Select
              label={t('playground.endpointLabel')}
              value={endpointId}
              options={endpointOptions}
              onChange={(event) => {
                setEndpointId(event.target.value)
                setFieldErrors({})
              }}
            />

            {endpoint.platformScoped ? (
              <Select
                label={t('console:field.platform')}
                value={platform}
                options={PLATFORMS.map((entry) => ({ value: entry, label: entry }))}
                onChange={(event) => {
                  setPlatform(event.target.value as Platform)
                }}
              />
            ) : null}

            <p className="u-mono u-xs u-secondary" style={{ margin: 0, wordBreak: 'break-all' }}>
              {endpoint.method} {path}
              {queryString(preview.query)}
            </p>

            {endpoint.params.map((param) =>
              param.kind === 'boolean' ? (
                <Checkbox
                  key={param.name}
                  checked={valueOf(param) === 'true'}
                  onChange={(event) => {
                    setValue(param.name, event.target.checked ? 'true' : 'false')
                  }}
                  label={<span className="u-mono">{param.name}</span>}
                  hint={t(`playground.param.${param.name}`)}
                />
              ) : (
                <Input
                  key={param.name}
                  label={<span className="u-mono">{param.name}</span>}
                  description={t(`playground.param.${param.name}`)}
                  required={param.required}
                  showOptional={!param.required && !param.oneOf}
                  mono
                  inputMode={param.kind === 'number' ? 'numeric' : undefined}
                  placeholder={param.placeholder}
                  value={valueOf(param)}
                  error={fieldErrors[param.name]}
                  onChange={(event) => {
                    setValue(param.name, event.target.value)
                  }}
                />
              ),
            )}

            <div className="u-row">
              <Button type="submit" variant="primary" loading={running}>
                {t('playground.send')}
              </Button>
              {running ? (
                <Button
                  variant="ghost"
                  onClick={() => {
                    abortRef.current?.abort()
                  }}
                >
                  {t('common:action.cancel')}
                </Button>
              ) : null}
              {running && taskState ? (
                <span className="u-xs u-muted">
                  {t('playground.taskState')} <StatusBadge kind="task" value={taskState} size="sm" />
                </span>
              ) : null}
            </div>
          </form>
        </Card>

        <Card title={t('playground.contextTitle')} description={t('playground.contextDescription')}>
          <div className="u-stack">
            <div className="u-stack-sm">
              <span className="u-xs u-muted">{t('playground.circuitLabel')}</span>
              {health.isLoading ? (
                <span className="u-muted u-xs">{t('common:loading')}</span>
              ) : health.isError ? (
                <span className="u-xs" style={{ color: 'var(--caution)' }}>
                  {t('playground.healthUnavailable')}
                </span>
              ) : matchedHealth ? (
                <div className="u-stack-sm">
                  <span className="u-row u-wrap">
                    <StatusBadge kind="circuit" value={circuitOf(matchedHealth)} />
                    <span className="u-mono u-xs u-secondary">{matchedHealth.endpoint}</span>
                  </span>
                  <span className="u-xs u-muted u-row u-wrap">
                    <span>
                      {t('console:metric.successRate')}{' '}
                      <span className="u-mono">{format.percent(matchedHealth.success_rate)}</span>
                    </span>
                    <span>
                      {t('console:metric.riskRate')}{' '}
                      <span className="u-mono">{format.percent(matchedHealth.risk_rate)}</span>
                    </span>
                    <span>
                      {t('playground.samples')}{' '}
                      <span className="u-mono">
                        {format.number(matchedHealth.samples ?? matchedHealth.requests)}
                      </span>
                    </span>
                  </span>
                  {matchedHealth.retry_after ? (
                    <span className="u-xs" style={{ color: 'var(--warning)' }}>
                      {t('playground.retryAfter', {
                        seconds: format.duration(matchedHealth.retry_after * 1000),
                      })}
                    </span>
                  ) : null}
                </div>
              ) : (
                <div className="u-stack-sm">
                  <span className="u-xs u-secondary">
                    {t('playground.circuitAggregate', {
                      open: openCircuits.length,
                      total: healthRows.length,
                    })}
                  </span>
                  {openCircuits.slice(0, 5).map((row) => (
                    <span key={row.endpoint} className="u-row u-wrap">
                      <StatusBadge kind="circuit" value={circuitOf(row)} size="sm" />
                      <span className="u-mono u-xs u-secondary">{row.endpoint}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>

            <div className="u-stack-sm">
              <span className="u-xs u-muted">{t('playground.poolLabel')}</span>
              {system.isLoading ? (
                <span className="u-muted u-xs">{t('common:loading')}</span>
              ) : system.isError ? (
                <span className="u-xs" style={{ color: 'var(--caution)' }}>
                  {t('playground.poolUnavailable')}
                </span>
              ) : (
                <div className="u-row u-wrap">
                  {IDENTITY_STATES.filter((state) => state !== 'retired').map((state) => (
                    <span key={state} className="u-row" style={{ gap: 'var(--space-1)' }}>
                      <StatusBadge kind="identity" value={state} size="sm" flash={false} />
                      <span className="u-mono">{format.number(pool[state])}</span>
                    </span>
                  ))}
                </div>
              )}
              {pool.active === 0 && !system.isLoading && !system.isError ? (
                <span className="u-xs" style={{ color: 'var(--warning)' }}>
                  {t('playground.poolEmptyHint')}
                </span>
              ) : null}
            </div>
          </div>
        </Card>
      </div>

      {result && !result.ok ? (
        <Card title={t('playground.failureTitle')} description={t('playground.failureDescription')}>
          <div className="u-stack">
            <div className="u-row u-wrap">
              {errorInfo.error ? <ErrorCodeBadge code={errorInfo.error.code} /> : null}
              {result.httpStatus ? (
                <span className="u-mono u-xs u-secondary">HTTP {result.httpStatus}</span>
              ) : null}
              {errorInfo.error?.retryAfter ? (
                <span className="u-xs" style={{ color: 'var(--warning)' }}>
                  {t('playground.retryAfter', {
                    seconds: format.duration(errorInfo.error.retryAfter * 1000),
                  })}
                </span>
              ) : null}
            </div>
            <p style={{ margin: 0 }}>{errorInfo.message}</p>
            {errorInfo.hint ? (
              <p className="u-xs u-muted" style={{ margin: 0 }}>
                {errorInfo.hint}
              </p>
            ) : null}

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
                {result.requestId ? (
                  <CopyableId value={result.requestId} length={18} middle />
                ) : (
                  <span className="u-muted">{t('playground.noRequestId')}</span>
                )}
              </dd>

              <dt className="u-xs u-muted">{t('playground.servedBy')}</dt>
              <dd style={{ margin: 0 }}>
                {result.requestId === null ? (
                  <span className="u-muted u-xs">{t('playground.servedByUnknown')}</span>
                ) : logLookupRows.isLoading ? (
                  <span className="u-muted u-xs">{t('common:loading')}</span>
                ) : servedBy ? (
                  <span className="u-row u-wrap">
                    <CopyableId value={servedBy.identity_id} length={12} middle />
                    <StatusBadge kind="outcome" value={servedBy.outcome} size="sm" flash={false} />
                    {servedBy.proxy_id ? (
                      <span className="u-xs u-muted">
                        {t('console:field.proxy')} <CopyableId value={servedBy.proxy_id} length={8} />
                      </span>
                    ) : null}
                    {servedBy.signer ? (
                      <span className="u-xs u-muted u-mono">{servedBy.signer}</span>
                    ) : null}
                  </span>
                ) : (
                  <span className="u-row u-wrap">
                    <span className="u-muted u-xs">{t('playground.servedByPending')}</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        void logLookupRows.refetch()
                      }}
                    >
                      {t('common:action.refresh')}
                    </Button>
                  </span>
                )}
              </dd>

              <dt className="u-xs u-muted">{t('playground.endpointLabel')}</dt>
              <dd style={{ margin: 0 }} className="u-row u-wrap">
                <span className="u-mono u-xs">{result.healthEndpoint ?? result.url}</span>
                {matchedHealth ? (
                  <StatusBadge kind="circuit" value={circuitOf(matchedHealth)} size="sm" />
                ) : null}
              </dd>

              <dt className="u-xs u-muted">{t('playground.poolLabel')}</dt>
              <dd style={{ margin: 0 }} className="u-mono u-xs">
                {t('playground.poolInline', {
                  active: pool.active,
                  cooling: pool.cooling,
                  degraded: pool.degraded,
                })}
              </dd>
            </dl>
          </div>
        </Card>
      ) : null}

      {!result && !running ? (
        <Card>
          <EmptyState
            title={t('playground.emptyTitle')}
            description={t('playground.emptyDescription')}
          />
        </Card>
      ) : null}

      {result?.ok ? (
        <div className="u-stack">
          <div className="u-row u-wrap u-xs u-muted">
            <span>
              {t('console:field.requestId')}{' '}
              <CopyableId value={result.requestId} length={18} middle />
            </span>
            <span>
              {t('console:field.duration')}{' '}
              <span className="u-mono">{format.latency(result.elapsedMs)}</span>
            </span>
            {result.meta?.duration_ms !== undefined ? (
              <span>
                {t('playground.serverDuration')}{' '}
                <span className="u-mono">{format.latency(result.meta.duration_ms)}</span>
              </span>
            ) : null}
            <span>
              {t('console:field.cacheHit')}{' '}
              <span className="u-mono">
                {result.meta?.cached ? t('common:value.yes') : t('common:value.no')}
              </span>
            </span>
            <span className="u-mono">HTTP {result.httpStatus}</span>
          </div>

          <div
            style={{
              display: 'grid',
              gap: 'var(--space-4)',
              gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
              alignItems: 'start',
            }}
          >
            <Card
              title={t('playground.normalizedTitle')}
              description={t('playground.normalizedDescription')}
              flush
            >
              <CodeBlock json={normalized} title={t('playground.normalizedTitle')} />
            </Card>
            <Card
              title={t('playground.rawTitle')}
              description={t('playground.rawDescription')}
              flush
            >
              {raw ? (
                <CodeBlock json={raw} title={t('playground.rawTitle')} defaultCollapsed />
              ) : (
                <div className="u-stack-sm" style={{ padding: 'var(--space-4)' }}>
                  <p className="u-secondary" style={{ margin: 0 }}>
                    {t('playground.rawMissing')}
                  </p>
                  <div>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        setValue('include_raw', 'true')
                        void send({ include_raw: 'true' })
                      }}
                    >
                      {t('playground.rawEnable')}
                    </Button>
                  </div>
                </div>
              )}
            </Card>
          </div>
        </div>
      ) : null}

      <Card title={t('playground.snippetTitle')} description={t('playground.snippetDescription')}>
        <div className="u-stack">
          <div className="u-row" role="tablist" aria-label={t('playground.snippetTitle')}>
            {SNIPPET_LANGUAGES.map((language) => (
              <Button
                key={language}
                size="sm"
                variant={snippet === language ? 'secondary' : 'ghost'}
                role="tab"
                aria-selected={snippet === language}
                onClick={() => {
                  setSnippet(language)
                }}
              >
                {t(`playground.snippet.${language}`)}
              </Button>
            ))}
          </div>
          <CodeBlock
            code={snippetText}
            language="text"
            title={t(`playground.snippet.${snippet}`)}
            collapsible={false}
          />
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('playground.snippetNote')}
          </p>
        </div>
      </Card>
    </div>
  )
}
