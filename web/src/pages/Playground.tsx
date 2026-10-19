import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation } from 'wouter'

import {
  AlertIcon,
  Button,
  Checkbox,
  CodeBlock,
  ContentNextSteps,
  CopyableId,
  Disclosure,
  EmptyState,
  ErrorCodeBadge,
  InfoIcon,
  Input,
  Select,
  SignatureDecode,
  SplitPane,
  StatusBadge,
  useErrorInfo,
  type AuthorTarget,
  type PostTarget,
  type SelectOption,
} from '@/components'
import { useApiQuery, useFormatters, useSession } from '@/hooks'
import type { DecodedParameter } from '@/components'
import { useApiMutation } from '@/hooks'
import { apiPost, apiRequest, isApiError, type ApiError, type ResponseMeta } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { canOpen } from '@/lib/nav'
import { POLL } from '@/lib/query'
import { atLeast } from '@/lib/roles'
import {
  IDENTITY_STATES,
  PLATFORMS,
  type CircuitState,
  type IdentityState,
  type Platform,
  type RequestLogRow,
  type TaskState,
  type UserRole,
} from '@/lib/types'
import styles from './playground.module.css'
import {
  CATALOG,
  DEFAULT_ENDPOINT,
  EXPLAIN_PARAM,
  IDENTITY_PARAM,
  SECTION_ORDER,
  exampleValues,
  forPlatform,
  supportOf,
  type CapabilityTable,
  type ParamDef,
  type ParamSection,
} from './playgroundCatalog'

/**
 * Endpoint workbench.
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
 *
 * The layout is three resizable panes filling the viewport - catalogue, request,
 * response - rather than one scrolling column of cards. The column wasted most
 * of a wide screen on empty gutters while the thing a reader needs next was
 * always below the fold, and every response was rendered expanded, so one large
 * payload pushed the snippet arbitrarily far down the page. Response panels are
 * now collapsed by default and opened on purpose; the failure panel is the one
 * exception, because on a failed call it is the entire reason to be here.
 */

/* -------------------------------------------------------------------------- */
/* Health, pool and identity shapes                                            */
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

/** Only the fields the picker needs; the Identities page owns the full shape. */
interface IdentityOption {
  id: string
  platform: string
  state: IdentityState
  source: 'minted' | 'imported'
  authenticated: boolean
  proxy_label?: string | null
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

/**
 * One platform's census, or every platform's summed when the call names none.
 *
 * The panel used to sum all three whatever was selected, so a Bilibili call
 * failing with IDENTITY_POOL_EXHAUSTED sat beside "active 6" - six Douyin
 * identities that could never have served it - and the empty-pool hint only
 * appeared once every platform was empty.
 */
function poolFor(pool: unknown, platform: Platform | null): PoolCounts {
  if (platform === null) return poolCounts(pool)
  if (typeof pool !== 'object' || pool === null) return { ...EMPTY_POOL }
  return poolCounts((pool as Record<string, unknown>)[platform])
}

interface SystemStatusLike {
  pool?: unknown
}

function isPlatform(value: unknown): value is Platform {
  return typeof value === 'string' && (PLATFORMS as readonly string[]).includes(value)
}

/** A single post, as `video` and the front door answer one. A page has `items`
 *  and is not one post, however many it holds. */
function postTargetOf(data: unknown): PostTarget | null {
  if (!data || typeof data !== 'object') return null
  const record = data as Record<string, unknown>
  if ('items' in record) return null
  const contentId = record['content_id']
  const platform = record['platform']
  if (typeof contentId !== 'string' || !contentId || !isPlatform(platform)) return null
  return { platform, contentId }
}

/** An author profile. `uid` is the archive's key for an author, which is why it
 *  and not `sec_uid` goes to the Library: on TikTok the two differ. */
function authorTargetOf(data: unknown): AuthorTarget | null {
  if (!data || typeof data !== 'object') return null
  const record = data as Record<string, unknown>
  if ('items' in record || 'content_id' in record) return null
  const uid = record['uid']
  const platform = record['platform']
  if (typeof uid !== 'string' || !uid || typeof record['nickname'] !== 'string') return null
  if (!isPlatform(platform)) return null
  return { platform, uid }
}

/**
 * What a guest's answer from this endpoint leaves out, where that has been
 * measured; everything else gets the general sentence. The same two cases
 * `dtk.mcp.prose` explains to an agent, and keyed the same way, by endpoint.
 */
const GUEST_NOTES: Readonly<Record<string, string>> = {
  'douyin.author_posts': 'playground.guest.douyinAuthorPosts',
  'bilibili.comments': 'playground.guest.bilibiliComments',
}

/** The worker's own flag, wherever this call's metadata put it: on the HTTP
 *  envelope when `wait` held the connection, on the task when it did not. */
function servedByGuest(result: RunResult): boolean {
  return result.resultMeta?.['guest_fallback'] === true || result.meta?.['guest_fallback'] === true
}

/**
 * The request this instance actually made, as `explain` reports it.
 *
 * The panels used to offer a signature calculator here, which was an honest
 * admission of a gap and not a fix for it: the signature that mattered was
 * computed in the worker against the platform's own URL, and that URL never
 * came back. Now it does, and with it the jar that signed it - so the panels
 * show this request rather than an unrelated one computed on demand.
 */
interface Explanation {
  method: string
  /** The full platform URL, signature parameters included. */
  url: string
  headers: Record<string, string>
  /** The identity's jar as one Cookie header, ready to paste. */
  cookie_header: string
  identity_id: string | null
  signer: string | null
  endpoint: string
  /** Masked. The exit is an operator credential and stays one. */
  proxy: string | null
  /** A POST's body as sent, or null. The signature hashed it. */
  body: string | null
}

/** What /tools/decode answers, as this page reads it. */
interface DecodeResult {
  parameters: readonly DecodedParameter[]
}

/**
 * The signature parameters, picked out of the URL the request was sent to.
 *
 * Spelled here rather than fetched: the panel has to be able to show what this
 * request signed without spending a round trip on it, and the names are a fixed
 * property of the platforms. Decoding them into plaintext is a separate,
 * deliberate click.
 */
const SIGNATURE_PARAMS: readonly string[] = [
  'a_bogus',
  'X-Bogus',
  'X-Gnarly',
  'X-Dynosaur',
  'msToken',
  'x-secsdk-web-signature',
  'verifyFp',
  'fp',
  'uifid',
  'w_rid',
  'wts',
]

/** Only ever null when the caller did not ask, or may not see it. */
function readExplanation(meta: ResponseMeta | undefined): Explanation | null {
  const value = meta?.['explain']
  if (!value || typeof value !== 'object') return null
  const explanation = value as Partial<Explanation>
  if (typeof explanation.url !== 'string') return null
  return {
    method: explanation.method ?? 'GET',
    url: explanation.url,
    headers: explanation.headers ?? {},
    cookie_header: explanation.cookie_header ?? '',
    identity_id: explanation.identity_id ?? null,
    signer: explanation.signer ?? null,
    endpoint: explanation.endpoint ?? '',
    proxy: explanation.proxy ?? null,
    body: explanation.body ?? null,
  }
}

/**
 * The User-Agent out of a header map, whatever case the sender spelled it.
 *
 * Worth finding: Douyin and TikTok hash it into the signature, so it is the
 * input the decoder most needs there, and "the UA I am sending is not the UA I
 * signed with" is the most common way a reproduced request fails while looking
 * correct. Bilibili's WBI signs the query alone and never sees it.
 */
function userAgentOf(headers: Record<string, string>): string | null {
  for (const [name, value] of Object.entries(headers)) {
    if (name.toLowerCase() === 'user-agent') return value
  }
  return null
}

/** Signature parameters in the order the platform appended them. */
function signatureParamsOf(url: string): [string, string][] {
  const query = url.split('?').slice(1).join('?')
  const out: [string, string][] = []
  for (const pair of query.split('&')) {
    if (!pair) continue
    const index = pair.indexOf('=')
    const name = index === -1 ? pair : pair.slice(0, index)
    if (!SIGNATURE_PARAMS.includes(name)) continue
    out.push([name, decodeURIComponent(index === -1 ? '' : pair.slice(index + 1))])
  }
  return out
}

/** Single-quote a value for a POSIX shell, the only way that is safe for all input. */
function shellQuote(value: string): string {
  // End the quoted run, emit an escaped quote, start a new one. The only
  // escaping a POSIX shell accepts inside single quotes, and cookies do
  // occasionally carry one.
  return `'${value.split("'").join("'\\''")}'`
}

/**
 * The upstream request as a curl line - the platform's own endpoint, not this
 * API's.
 *
 * This is the thing anybody debugging a refused call ends up building by hand,
 * and building it by hand is where it goes wrong: the query has to be sent byte
 * for byte because the signature covers it, the User-Agent has to be the one it
 * was hashed with, and the jar has to be the identity's own. All three are
 * known here and none of them were guessable before.
 */
function upstreamCurl(explanation: Explanation): string {
  const lines = [`curl -X ${explanation.method} ${shellQuote(explanation.url)} \\`]
  for (const [name, value] of Object.entries(explanation.headers)) {
    lines.push(`  -H ${shellQuote(`${name}: ${value}`)} \\`)
  }
  const cookie = `  -H ${shellQuote(`Cookie: ${explanation.cookie_header}`)}`
  if (explanation.body === null) {
    lines.push(cookie)
  } else {
    // The body too, or a POST replays empty under a signature that hashed one.
    lines.push(`${cookie} \\`, `  --data-raw ${shellQuote(explanation.body)}`)
  }
  return lines.join('\n')
}

/**
 * What this request signed, straight out of the URL it was sent to.
 *
 * Values are shown whole. They run to a couple of hundred characters and an
 * elided one is useless: the reason to look at a signature is to compare it
 * with something, and half of one compares with nothing.
 */
function SignatureParams({ pairs }: { pairs: readonly [string, string][] }) {
  const { t } = useTranslation('console')
  if (pairs.length === 0) {
    return <p className="u-xs u-muted" style={{ margin: 0 }}>{t('playground.signatureNone')}</p>
  }

  return (
    <dl className={styles.facts}>
      {pairs.map(([name, value]) => (
        <Fragment key={name}>
          <dt className="u-xs u-muted u-mono">{name}</dt>
          <dd className="u-mono u-xs" style={{ overflowWrap: 'anywhere' }}>
            {value === '' ? <span className="u-muted">{t('common:value.none')}</span> : value}
          </dd>
        </Fragment>
      ))}
    </dl>
  )
}

/**
 * What to do when the run did not ask for an explanation.
 *
 * An empty panel would read as "there is nothing to show", which is wrong: the
 * request happened and this instance knows exactly what it sent. It just was
 * not asked. So the panel says what turning it on costs and offers the button
 * rather than making the reader find a checkbox.
 */
function ExplainPrompt({
  enabled,
  running,
  permitted,
  onRun,
}: {
  enabled: boolean
  running: boolean
  /** False below operator. The panel then says why instead of offering a
   *  button whose only possible answer is 403. */
  permitted: boolean
  onRun: () => void
}) {
  const { t } = useTranslation('console')

  if (!permitted) {
    return (
      <p className="u-xs u-muted" style={{ margin: 0 }}>
        {t('playground.explainForbidden')}
      </p>
    )
  }

  return (
    <div className="u-stack-sm">
      <p className="u-xs u-secondary" style={{ margin: 0 }}>
        {t(enabled ? 'playground.explainPending' : 'playground.explainAbsent')}
      </p>
      <p className="u-xs u-muted" style={{ margin: 0 }}>
        {t('playground.explainCost')}
      </p>
      <div>
        <Button size="sm" variant="secondary" loading={running} onClick={onRun}>
          {t('playground.explainRun')}
        </Button>
      </div>
    </div>
  )
}

/**
 * Which identity served this call, what jar it used, and how to send the same
 * request by hand.
 *
 * The identity id alone was already here on a failure. It answers "who" and
 * nothing else, and "who" is rarely the question - a jar that has gone stale
 * and a jar that was never logged in look identical from the id. The cookie and
 * the curl are what make the answer actionable, and both are why this panel is
 * gated behind an explicit `explain`.
 */
function ServingIdentity({
  explanation,
  identityId,
  pending,
}: {
  explanation: Explanation | null
  identityId: string | null
  pending: boolean
}) {
  const { t } = useTranslation('console')
  const [showCookie, setShowCookie] = useState(false)

  if (!identityId && !explanation) {
    return pending ? (
      <span className="u-xs u-muted">{t('common:loading')}</span>
    ) : null
  }

  return (
    <div className="u-stack-sm">
      <span className="u-xs u-muted">{t('playground.servedBy')}</span>
      <div className="u-row u-wrap">
        {identityId ? (
          <CopyableId value={identityId} />
        ) : (
          <span className="u-muted u-xs">{t('playground.servedByUnknown')}</span>
        )}
        {explanation?.signer ? (
          <span className="u-xs u-muted u-mono">{explanation.signer}</span>
        ) : null}
        {explanation?.proxy ? (
          <span className="u-xs u-muted u-mono">{explanation.proxy}</span>
        ) : null}
      </div>

      {explanation ? (
        <>
          <span className="u-xs u-muted">{t('playground.cookieLabel')}</span>
          {/* Behind a click, not behind a mask. Half a cookie helps nobody, and
              the reader has already asked for the jar by name; what the click
              buys is that it is not sitting open on a shared screen. */}
          {showCookie ? (
            <CodeBlock code={explanation.cookie_header} language="text" />
          ) : (
            <div className="u-row u-wrap">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setShowCookie(true)
                }}
              >
                {t('playground.cookieReveal')}
              </Button>
              <span className="u-xs u-muted">
                {t('playground.cookieCount', {
                  count: explanation.cookie_header
                    ? explanation.cookie_header.split(';').length
                    : 0,
                })}
              </span>
            </div>
          )}

          <span className="u-xs u-muted">{t('playground.upstreamCurl')}</span>
          <CodeBlock
            code={upstreamCurl(explanation)}
            language="text"
            title={t('playground.upstreamCurl')}
            collapsible={false}
          />
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('playground.upstreamCurlNote')}
          </p>
        </>
      ) : null}
    </div>
  )
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

/** The cursor for the next page, when the payload is a page and has one. */
function nextCursor(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if (!Array.isArray(record['items'])) return null
  if (record['has_more'] === false) return null
  const cursor = record['cursor']
  return typeof cursor === 'string' && cursor ? cursor : null
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

/**
 * One sentence about this call the reader should see before reading the
 * rest: that the platform does not serve it, that it needs a pinned identity,
 * that a guest answered it. A rule down the left in the tone's colour, the
 * same shape as the overview's alerts, so it reads as a condition and not as
 * part of the form.
 */
function Notice({
  tone,
  action,
  children,
}: {
  tone: 'info' | 'caution' | 'warning'
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <div className={styles.notice} data-tone={tone} role={tone === 'info' ? 'note' : 'status'}>
      <span className={styles.noticeIcon}>
        {tone === 'info' ? <InfoIcon size={13} /> : <AlertIcon size={13} />}
      </span>
      <span className={styles.noticeBody}>{children}</span>
      {action}
    </div>
  )
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
  /** What the worker recorded, as opposed to what this HTTP call did. */
  resultMeta?: ResponseMeta
  error?: unknown
  requestId: string | null
  healthEndpoint: string | null
  /** The platform selected when it was sent; the form may have moved on since. */
  platform: Platform
  /** Whether that platform was part of the call, or only of the form. */
  platformScoped: boolean
}

/** One entry in the session's run history. Held in memory only: a request can
 *  carry a proxy password or an identity id, and neither belongs in storage
 *  that outlives the tab. */
interface HistoryEntry {
  key: number
  endpointId: string
  platform: Platform
  values: Record<string, string>
  ok: boolean
  at: number
  elapsedMs: number
}

const HISTORY_LIMIT = 12

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
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const abortRef = useRef<AbortController | null>(null)

  /** Which controls this session may operate. Null until /auth/me answers, and
   *  null reads as "below everything" - see `atLeast`. */
  const session = useSession()
  const role = session.data?.role ?? null
  const allows = (param: ParamDef): boolean =>
    (!param.minRole || atLeast(role, param.minRole)) &&
    (!param.platforms || param.platforms.includes(platform))

  const [, navigate] = useLocation()

  const endpoint = useMemo(
    () => CATALOG.find((entry) => entry.id === endpointId) ?? DEFAULT_ENDPOINT,
    [endpointId],
  )

  /** The board decides the operation on `popular`; everywhere else it is fixed. */
  const operation = endpoint.operationFor ? endpoint.operationFor(values) : endpoint.operation
  const healthEndpointName = operation
    ? endpoint.platformScoped
      ? `${platform}.${operation}`
      : operation
    : null

  /**
   * What each platform serves. Static for the life of the API process, so it
   * is fetched once and never polled. A failure, or an API too old to have
   * the route, leaves every endpoint offered, which is how the page behaved
   * before it could ask.
   */
  const capabilities = useApiQuery<CapabilityTable>({
    key: ['system', 'capabilities'],
    path: paths.system.capabilities,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  })
  // Memoized for the compiler rather than for speed. The form's rules read this,
  // and as a plain call's result the React compiler could not rule out that it
  // changed after the request preview was built from it: it then refused to
  // compile the page, and reported `send` as running during render.
  const { served: servedHere, sessionBound } = useMemo(
    () => supportOf(endpoint, platform, capabilities.data, operation),
    [endpoint, platform, capabilities.data, operation],
  )
  const platformLabel = (name: Platform): string =>
    t(`platform.name.${name}`, { defaultValue: name })
  /** The call cannot succeed for this session: the endpoint answers only about
   *  a pinned identity, and pinning is above this role. */
  const identityOutOfReach = sessionBound && !atLeast(role, 'operator')
  /** What "Load example" would fill in here, or null when nothing can - which
   *  includes an endpoint this platform does not serve. */
  const example = servedHere === false ? null : exampleValues(endpoint, platform)

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

  /**
   * The pool, for the identity picker. Read-only and best effort: a caller
   * without `identity:manage` cannot list identities and also cannot use the
   * parameter, so a failure here leaves the picker empty rather than raising -
   * the rest of the page works without it.
   */
  const identities = useApiQuery<IdentityOption[]>({
    key: ['admin', 'identities', 'picker'],
    path: paths.identities.list,
    params: { limit: 200 },
    retry: false,
  })

  /**
   * Read this request's own signature parameters back into plaintext.
   *
   * On demand rather than on arrival. The panel can already show WHAT was sent
   * without asking anything - the parameters are in the URL - and decoding them
   * is a separate question a reader either has or does not. Firing it
   * automatically would spend a round trip on every run to answer a question
   * most runs do not ask.
   */
  const decodeSignature = useApiMutation<DecodeResult, Explanation>((explanation) =>
    apiPost<DecodeResult>(paths.tools.decode, {
      value: explanation.url,
      user_agent: userAgentOf(explanation.headers),
    }),
  )

  const requestId = result?.requestId ?? null
  const explanation = useMemo(() => readExplanation(result?.resultMeta), [result?.resultMeta])

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

  /** Required by the catalogue, or - for `identity` - by the endpoint answering
   *  only about the identity asking it. */
  const isRequired = (param: ParamDef): boolean =>
    Boolean(param.required) || (param.name === IDENTITY_PARAM.name && sessionBound)

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
      // A parameter the session may not use never reaches the query, even if a
      // value survived in state from before the role was known. The API would
      // refuse it anyway; this is what stops the refusal from being the way a
      // reader finds out.
      if (!allows(param)) continue
      const raw = valueOf(param, overrides).trim()
      if (param.oneOf) {
        groupsSeen.add(param.oneOf)
        if (raw) groupsFilled.add(param.oneOf)
      }

      if (!raw) {
        if (isRequired(param)) errors[param.name] = t('playground.required')
        continue
      }

      if (param.kind === 'number') {
        const parsed = Number(raw)
        const max = forPlatform(param.max, platform)
        if (!Number.isFinite(parsed)) {
          errors[param.name] = t('playground.notANumber')
          continue
        }
        if (param.min !== undefined && parsed < param.min) {
          errors[param.name] = t('playground.outOfRange', { min: param.min, max: max ?? 0 })
          continue
        }
        if (max !== undefined && parsed > max) {
          errors[param.name] = t('playground.outOfRange', { min: param.min ?? 0, max })
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

  /** Why Send is off, or null when it is on. Both are calls whose only possible
   *  answer is a refusal this page can already name. */
  const blocked: string | null =
    servedHere === false
      ? t('playground.unservedNotice', { platform: platformLabel(platform) })
      : identityOutOfReach
        ? t('playground.identityRequiredForbidden')
        : null

  const send = async (overrides?: Record<string, string>): Promise<void> => {
    if (blocked) return
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
    const sentValues = { ...values, ...overrides }

    const remember = (ok: boolean, elapsedMs: number): void => {
      setHistory((current) =>
        [
          { key: startedAt, endpointId: endpoint.id, platform, values: sentValues, ok, at: startedAt, elapsedMs },
          ...current,
        ].slice(0, HISTORY_LIMIT),
      )
    }

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
      const elapsedMs = performance.now() - startedPerf
      setResult({
        ok: true,
        method: endpoint.method,
        url: `${path}${queryString(query)}`,
        startedAt,
        elapsedMs,
        httpStatus: response.status,
        data: response.data,
        meta: response.meta,
        resultMeta: response.resultMeta,
        requestId: response.meta.request_id ?? null,
        healthEndpoint: healthEndpointName,
        platform,
        platformScoped: endpoint.platformScoped,
      })
      remember(true, elapsedMs)
    } catch (error) {
      const apiError: ApiError | null = isApiError(error) ? error : null
      const elapsedMs = performance.now() - startedPerf
      setResult({
        ok: false,
        method: endpoint.method,
        url: `${path}${queryString(query)}`,
        startedAt,
        elapsedMs,
        httpStatus: apiError?.status ?? null,
        error,
        resultMeta: apiError?.resultMeta,
        requestId: apiError?.requestId ?? null,
        healthEndpoint: healthEndpointName,
        platform,
        platformScoped: endpoint.platformScoped,
      })
      remember(false, elapsedMs)
    } finally {
      abortRef.current = null
      setRunning(false)
    }
  }

  /** Put a past run's parameters back in the form. Deliberately does not send:
   *  a click that replays an upstream call would spend an identity by accident. */
  const restore = (entry: HistoryEntry): void => {
    setEndpointId(entry.endpointId)
    setPlatform(entry.platform)
    setValues(entry.values)
    setFieldErrors({})
  }

  /* -------------------------------------------------------------- health -- */

  const healthRows = health.data ?? []
  const matchedHealth = healthEndpointName
    ? (healthRows.find((row) => row.endpoint === healthEndpointName) ?? null)
    : null
  const openCircuits = healthRows.filter((row) => circuitOf(row) !== 'closed')
  /** The pool that could serve this call: the selected platform's, or all of
   *  them for the front door, which works the platform out from the link. */
  const poolPlatform: Platform | null = endpoint.platformScoped ? platform : null
  const pool = poolFor(system.data?.pool, poolPlatform)
  const identitiesHref = poolPlatform
    ? `/identities?platform=${encodeURIComponent(poolPlatform)}`
    : '/identities'
  /** The failure panel describes the call that failed, not the form as it is now. */
  const failurePool = result?.platformScoped
    ? poolFor(system.data?.pool, result.platform)
    : poolFor(system.data?.pool, null)

  /**
   * Identities this call could actually be pinned to: the right platform, and
   * not retired - retirement wipes the jar, so a retired identity has nothing
   * left to send as. The state and the "logged in" marker travel in the label
   * because choosing between accounts is what this control is for.
   */
  const identityOptions: SelectOption[] = useMemo(() => {
    const rows = (identities.data ?? []).filter(
      (row) =>
        row.state !== 'retired' && (!endpoint.platformScoped || row.platform === platform),
    )
    return [
      { value: '', label: t('playground.identityAuto') },
      ...rows.map((row) => ({
        value: row.id,
        label: [
          // Whole. Two identities sharing their first eight characters is
          // unlikely and picking the wrong one because of it is silent, and
          // an <option> is one of the few places with no hover and no copy
          // button to fall back on.
          row.id,
          row.state,
          row.authenticated ? t('playground.identityLoggedIn') : row.source,
        ].join(' · '),
      })),
    ]
  }, [identities.data, endpoint.platformScoped, platform, t])

  const normalized = result?.ok ? stripRaw(result.data) : null
  const raw = result?.ok ? pickRaw(result.data) : null
  const cursor = result?.ok ? nextCursor(result.data) : null
  const resultPost = result?.ok ? postTargetOf(result.data) : null
  const resultAuthor = result?.ok && !resultPost ? authorTargetOf(result.data) : null
  const guestServed = result?.ok ? servedByGuest(result) : false
  /** The platform the guest answered on. The front door's comes from the post
   *  it returned, since the form's platform played no part in that call. */
  const guestPlatform = resultPost?.platform ?? resultAuthor?.platform ?? result?.platform

  /* A platform switch can strip the pinned identity out from under the form.
   * Clearing it beats sending a Douyin identity to a TikTok endpoint and
   * reading the 400 that comes back. */
  useEffect(() => {
    const pinned = values['identity']
    if (!pinned) return
    const row = (identities.data ?? []).find((entry) => entry.id === pinned)
    if (row && endpoint.platformScoped && row.platform !== platform) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- drops an identity pin the newly selected platform cannot serve
      setValue('identity', '')
    }
    // Only the platform and the roster can invalidate a pin.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform, identities.data, endpoint.platformScoped])

  /* ---------------------------------------------------------------- view -- */

  const paramsBySection = (section: ParamSection): ParamDef[] =>
    endpoint.params.filter((param) => param.section === section)

  const renderParam = (param: ParamDef) => {
    const permitted = allows(param)
    const required = isRequired(param)
    /* Say which role it takes, in place of what the parameter does. A reader
       who cannot use a control is not helped by a description of it. A
       platform whose ids look different gets its own sentence; the page size
       is told its platform's ceiling. */
    const description = permitted
      ? t(
          param.descriptions?.[platform] ??
            (param.name === IDENTITY_PARAM.name && sessionBound
              ? 'playground.param.identitySessionBound'
              : (param.descriptionKey ?? `playground.param.${param.name}`)),
          { max: forPlatform(param.max, platform) },
        )
      : t('playground.param.needsRole', { role: param.minRole as UserRole })

    if (param.name === IDENTITY_PARAM.name) {
      return (
        <Select
          key={param.name}
          label={<span className="u-mono">{param.name}</span>}
          description={description}
          required={required}
          error={fieldErrors[param.name]}
          value={permitted ? valueOf(param) : ''}
          options={identityOptions}
          disabled={!permitted}
          onChange={(event) => {
            setValue(param.name, event.target.value)
          }}
        />
      )
    }
    if (param.kind === 'choice') {
      return (
        <Select
          key={param.name}
          label={<span className="u-mono">{param.name}</span>}
          description={description}
          value={permitted ? valueOf(param) : ''}
          options={[
            { value: '', label: t('playground.choiceDefault') },
            ...(param.choices ?? []).map((choice) => ({ value: choice, label: choice })),
          ]}
          disabled={!permitted}
          onChange={(event) => {
            setValue(param.name, event.target.value)
          }}
        />
      )
    }
    if (param.kind === 'boolean') {
      return (
        <Checkbox
          key={param.name}
          checked={permitted && valueOf(param) === 'true'}
          disabled={!permitted}
          onChange={(event) => {
            setValue(param.name, event.target.checked ? 'true' : 'false')
          }}
          label={<span className="u-mono">{param.name}</span>}
          hint={description}
        />
      )
    }
    return (
      <Input
        key={param.name}
        label={<span className="u-mono">{param.name}</span>}
        description={description}
        required={required}
        showOptional={!required && !param.oneOf}
        mono
        inputMode={param.kind === 'number' ? 'numeric' : undefined}
        placeholder={forPlatform(param.placeholder, platform)}
        value={permitted ? valueOf(param) : ''}
        error={fieldErrors[param.name]}
        disabled={!permitted}
        onChange={(event) => {
          setValue(param.name, event.target.value)
        }}
      />
    )
  }

  return (
    <div data-shell-fill className={styles.workbench}>
      <div className={styles.addressBar}>
        <h1 className={styles.pageTitle}>{t('page.playground.title')}</h1>
        <span className={styles.url}>
          <span className={styles.method}>{endpoint.method}</span>{' '}
          {path}
          {queryString(preview.query)}
        </span>
        <div className={styles.actions}>
          {running ? (
            <StatusBadge kind="task" value={taskState ?? 'queued'} size="sm" />
          ) : result ? (
            <StatusBadge kind="task" value={result.ok ? 'done' : 'failed'} size="sm" />
          ) : null}
          {running ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                abortRef.current?.abort()
              }}
            >
              {t('common:action.cancel')}
            </Button>
          ) : null}
          <Button
            variant="primary"
            size="sm"
            loading={running}
            // `undefined` rather than false when sendable: the button then
            // falls back to disabling itself while loading.
            disabled={blocked !== null ? true : undefined}
            title={blocked ?? undefined}
            onClick={() => {
              void send()
            }}
          >
            {t('playground.send')}
          </Button>
        </div>
      </div>

      <SplitPane
        storageKey="playground"
        gutterLabels={[
          t('common:layout.splitterAt', { index: 1 }),
          t('common:layout.splitterAt', { index: 2 }),
        ]}
        defaultSizes={[16, 42, 42]}
        minSizes={[170, 320, 320]}
      >
        {/* ---------------------------------------------------- catalogue -- */}
        <div className={styles.pane}>
          {/* Above the list, not inside the form: the list depends on it. It
              used to sit in the request pane, so the catalogue offered every
              endpoint whatever the platform, and the first a reader learned
              of a gap was the refusal. */}
          <Select
            label={t('console:field.platform')}
            value={platform}
            options={PLATFORMS.map((entry) => ({ value: entry, label: platformLabel(entry) }))}
            onChange={(event) => {
              setPlatform(event.target.value as Platform)
              setFieldErrors({})
            }}
          />
          <h2 className={styles.paneHeading}>{t('playground.catalogTitle')}</h2>
          <ul className={styles.endpointList}>
            {CATALOG.map((entry) => {
              // Greyed, not hidden: a reader looking for Bilibili likes should
              // find the row and the reason, not wonder whether they missed it.
              const unserved = supportOf(entry, platform, capabilities.data).served === false
              return (
                <li key={entry.id}>
                  <button
                    type="button"
                    className={styles.endpointItem}
                    aria-current={entry.id === endpointId}
                    data-unserved={unserved || undefined}
                    onClick={() => {
                      setEndpointId(entry.id)
                      setFieldErrors({})
                    }}
                  >
                    <span className={styles.endpointVerb}>{entry.method}</span>
                    <span className={styles.endpointLabel}>
                      <span>{t(`playground.endpoint.${entry.id}`)}</span>
                      {unserved ? (
                        <span className={styles.endpointReason}>
                          {t('playground.unserved', { platform: platformLabel(platform) })}
                        </span>
                      ) : null}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>

          <h2 className={styles.paneHeading}>{t('playground.historyTitle')}</h2>
          {history.length === 0 ? (
            <p className="u-xs u-muted" style={{ margin: 0 }}>
              {t('playground.historyEmpty')}
            </p>
          ) : (
            <ul className={styles.endpointList}>
              {history.map((entry) => (
                <li key={entry.key}>
                  <button
                    type="button"
                    className={styles.historyItem}
                    onClick={() => {
                      restore(entry)
                    }}
                    title={t('playground.historyRestore')}
                  >
                    <span
                      className={`${styles.historyDot} ${entry.ok ? styles.historyOk : styles.historyFailed}`}
                      aria-hidden="true"
                    />
                    <span>
                      {t(`playground.endpoint.${entry.endpointId}`)}
                      {CATALOG.find((e) => e.id === entry.endpointId)?.platformScoped
                        ? ` · ${platformLabel(entry.platform)}`
                        : ''}
                    </span>
                    <span className="u-mono u-muted">{format.latency(entry.elapsedMs)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* ------------------------------------------------------ request -- */}
        <div className={styles.pane}>
          <form
            className="u-stack"
            onSubmit={(event) => {
              event.preventDefault()
              void send()
            }}
          >
            {servedHere === false ? (
              <Notice tone="caution">
                {t('playground.unservedNotice', { platform: platformLabel(platform) })}
              </Notice>
            ) : sessionBound ? (
              <Notice tone={identityOutOfReach ? 'caution' : 'info'}>
                {identityOutOfReach
                  ? t('playground.identityRequiredForbidden')
                  : t('playground.identityRequired')}
              </Notice>
            ) : null}

            {/* Every endpoint whose target can be named in advance. The values
                are real and public, so the first call made with them succeeds -
                which is the thing a reader pressing this is asking for. */}
            {example ? (
              <div className="u-row u-wrap">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setValues((current) => ({ ...current, ...example }))
                    setFieldErrors({})
                  }}
                >
                  {t('playground.loadExample', { platform: platformLabel(platform) })}
                </Button>
                <span className="u-xs u-muted">{t('playground.loadExampleHint')}</span>
              </div>
            ) : null}

            {SECTION_ORDER.map((section) => {
              const params = paramsBySection(section)
              if (params.length === 0) return null
              const switches = params.filter((param) => param.kind === 'boolean')
              const fields = params.filter((param) => param.kind !== 'boolean')
              return (
                <div key={section} className={styles.paramGroup}>
                  <h2 className={styles.paneHeading}>{t(`playground.section.${section}`)}</h2>
                  {fields.length > 0 ? (
                    <div className={styles.paramGrid}>{fields.map(renderParam)}</div>
                  ) : null}
                  {switches.length > 0 ? (
                    <div className={styles.switchRow}>{switches.map(renderParam)}</div>
                  ) : null}
                </div>
              )
            })}

            {/* Submitting with Enter has to keep working; the visible Send is in
                the address bar, where the URL it will call already is. */}
            <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
          </form>
        </div>

        {/* ----------------------------------------------------- response -- */}
        <div className={styles.pane}>
          {!result && !running ? (
            <EmptyState
              title={t('playground.emptyTitle')}
              description={t('playground.emptyDescription')}
            />
          ) : null}

          {result ? (
            <div className={styles.statusStrip}>
              <span className="u-mono">HTTP {result.httpStatus ?? '-'}</span>
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
              {result.ok ? (
                <span>
                  {t('console:field.cacheHit')}{' '}
                  <span className="u-mono">
                    {result.meta?.cached ? t('common:value.yes') : t('common:value.no')}
                  </span>
                </span>
              ) : null}
              <span>
                {t('console:field.requestId')}{' '}
                {result.requestId ? (
                  <CopyableId value={result.requestId} />
                ) : (
                  <span className="u-muted">{t('playground.noRequestId')}</span>
                )}
              </span>
            </div>
          ) : null}

          {/* Above the payload, because the payload looks complete. A guest's
              answer is real data with the newest posts or all but three
              comments missing, and nothing in it says so except this flag. */}
          {result && guestServed ? (
            <Notice
              tone="warning"
              action={
                guestPlatform && canOpen(role, '/identities') ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      navigate(
                        `/identities?platform=${encodeURIComponent(guestPlatform)}&login=yes`,
                      )
                    }}
                  >
                    {t('playground.guest.action')}
                  </Button>
                ) : null
              }
            >
              <strong>{t('playground.guest.title')}</strong>{' '}
              {t(
                (result.healthEndpoint ? GUEST_NOTES[result.healthEndpoint] : undefined) ??
                  'playground.guest.generic',
              )}
            </Notice>
          ) : null}

          {resultPost || resultAuthor ? (
            <ContentNextSteps post={resultPost} author={resultAuthor} />
          ) : null}

          {/* The failure panel is the page's reason for existing and is never
              collapsed: error code, request id, serving identity, circuit and
              pool in one place is what separates "the platform changed" from
              "we are out of identities". */}
          {result && !result.ok ? (
            <div className="u-stack">
              <div className="u-row u-wrap">
                {errorInfo.error ? <ErrorCodeBadge code={errorInfo.error.code} /> : null}
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

              <dl className={styles.facts}>
                <dt className="u-xs u-muted">{t('playground.servedBy')}</dt>
                <dd>
                  {/* The explanation first when there is one: it names the
                      identity the moment the task finishes, where the request
                      log lags behind it and has a "not caught up yet" state
                      with a refresh button. Same answer, sooner. */}
                  {explanation?.identity_id ? (
                    <span className="u-row u-wrap">
                      <CopyableId value={explanation.identity_id} />
                      {explanation.signer ? (
                        <span className="u-xs u-muted u-mono">{explanation.signer}</span>
                      ) : null}
                    </span>
                  ) : result.requestId === null ? (
                    <span className="u-muted u-xs">{t('playground.servedByUnknown')}</span>
                  ) : logLookupRows.isLoading ? (
                    <span className="u-muted u-xs">{t('common:loading')}</span>
                  ) : servedBy ? (
                    <span className="u-row u-wrap">
                      <CopyableId value={servedBy.identity_id} />
                      <StatusBadge kind="outcome" value={servedBy.outcome} size="sm" flash={false} />
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
                <dd className="u-row u-wrap">
                  <span className="u-mono u-xs">{result.healthEndpoint ?? result.url}</span>
                  {matchedHealth ? (
                    <StatusBadge kind="circuit" value={circuitOf(matchedHealth)} size="sm" />
                  ) : null}
                </dd>

                <dt className="u-xs u-muted">
                  {result.platformScoped
                    ? t('playground.poolLabelFor', { platform: platformLabel(result.platform) })
                    : t('playground.poolLabel')}
                </dt>
                <dd className="u-mono u-xs">
                  {t('playground.poolInline', {
                    active: failurePool.active,
                    cooling: failurePool.cooling,
                    degraded: failurePool.degraded,
                  })}
                </dd>
              </dl>
            </div>
          ) : null}

          {result?.ok ? (
            <div className={styles.panels}>
              <Disclosure title={t('playground.normalizedTitle')} flush bodyClassName={styles.payload}>
                <CodeBlock json={normalized} title={t('playground.normalizedTitle')} />
              </Disclosure>

              <Disclosure
                title={t('playground.rawTitle')}
                meta={
                  raw ? null : <span className="u-xs u-muted">{t('playground.rawAbsent')}</span>
                }
                flush
                bodyClassName={styles.payload}
              >
                {raw ? (
                  <CodeBlock json={raw} title={t('playground.rawTitle')} />
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
              </Disclosure>

              <Disclosure title={t('playground.metaTitle')}>
                <div className="u-stack-sm">
                  <CodeBlock json={result.meta ?? {}} title={t('playground.metaTitle')} />
                  {cursor ? (
                    <div className="u-row u-wrap">
                      <span className="u-xs u-muted">{t('playground.nextPageHint')}</span>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          setValue('cursor', cursor)
                          void send({ cursor })
                        }}
                      >
                        {t('playground.nextPage')}
                      </Button>
                    </div>
                  ) : null}
                </div>
              </Disclosure>

              <Disclosure title={t('playground.snippetTitle')}>
                <div className="u-stack-sm">
                  <div className="u-row u-row-wrap" role="tablist" aria-label={t('playground.snippetTitle')}>
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
              </Disclosure>
            </div>
          ) : null}

          <div className={styles.panels}>
            <Disclosure
              title={t('playground.signatureTitle')}
              defaultOpen={explanation !== null}
              meta={
                explanation?.signer ?? servedBy?.signer ? (
                  <span className="u-mono u-xs u-muted">
                    {explanation?.signer ?? servedBy?.signer}
                  </span>
                ) : null
              }
            >
              {/* The signature this request carried, not one computed on
                  demand. The calculator that used to live here answered a
                  different question - it signed a URL you typed, while the one
                  that mattered was built in the worker - and it is still on the
                  tools page, where signing a URL is the whole point. */}
              {explanation ? (
                <div className="u-stack-sm">
                  <p className="u-xs u-muted" style={{ margin: 0 }}>
                    {t('playground.signatureDescription')}
                  </p>
                  <SignatureParams pairs={signatureParamsOf(explanation.url)} />
                  <div className="u-row u-wrap">
                    <Button
                      size="sm"
                      variant="secondary"
                      loading={decodeSignature.isPending}
                      onClick={() => {
                        decodeSignature.mutate(explanation)
                      }}
                    >
                      {t('playground.signatureDecode')}
                    </Button>
                    <span className="u-xs u-muted">{t('playground.signatureDecodeHint')}</span>
                  </div>
                  {decodeSignature.data ? (
                    <SignatureDecode parameters={decodeSignature.data.parameters} />
                  ) : null}
                </div>
              ) : (
                <ExplainPrompt
                  enabled={values['explain'] === 'true'}
                  running={running}
                  permitted={allows(EXPLAIN_PARAM)}
                  onRun={() => {
                    setValue('explain', 'true')
                    void send({ explain: 'true' })
                  }}
                />
              )}
            </Disclosure>
          </div>

          {/* Always available, whether or not a call has been made: an operator
              who arrives to find out why nothing works needs the circuit and
              the pool before they need a response. */}
          <div className={styles.panels}>
            <Disclosure
              title={t('playground.contextTitle')}
              meta={
                <span className={styles.contextRow}>
                  {matchedHealth ? (
                    <StatusBadge kind="circuit" value={circuitOf(matchedHealth)} size="sm" />
                  ) : (
                    <span className="u-xs u-muted">
                      {t('playground.circuitAggregate', {
                        open: openCircuits.length,
                        total: healthRows.length,
                      })}
                    </span>
                  )}
                  <span className="u-mono u-xs">
                    {t('playground.poolInline', {
                      active: pool.active,
                      cooling: pool.cooling,
                      degraded: pool.degraded,
                    })}
                  </span>
                </span>
              }
            >
              <div className="u-stack-sm">
                {/* This call first, the pool second. "Which identity served
                    this, and with what jar" is a fact about the run in front of
                    you; the circuit and the pool are the background it happened
                    against, and a reader who has just made a request wants the
                    foreground. */}
                <ServingIdentity
                  explanation={explanation}
                  identityId={explanation?.identity_id ?? servedBy?.identity_id ?? null}
                  pending={result !== null && requestId !== null && logLookupRows.isLoading}
                />

                <span className="u-xs u-muted">{t('playground.circuitLabel')}</span>
                {health.isError ? (
                  <span className="u-xs" style={{ color: 'var(--caution)' }}>
                    {t('playground.healthUnavailable')}
                  </span>
                ) : matchedHealth ? (
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
                ) : (
                  <div className="u-stack-sm">
                    {openCircuits.slice(0, 5).map((row) => (
                      <span key={row.endpoint} className="u-row u-wrap">
                        <StatusBadge kind="circuit" value={circuitOf(row)} size="sm" />
                        <span className="u-mono u-xs u-secondary">{row.endpoint}</span>
                      </span>
                    ))}
                  </div>
                )}

                <span className="u-row u-wrap">
                  <span className="u-xs u-muted">
                    {poolPlatform
                      ? t('playground.poolLabelFor', { platform: platformLabel(poolPlatform) })
                      : t('playground.poolLabel')}
                  </span>
                  {canOpen(role, '/identities') ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        navigate(identitiesHref)
                      }}
                    >
                      {t('playground.poolManage')}
                    </Button>
                  ) : null}
                </span>
                {system.isError ? (
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
                    {poolPlatform
                      ? t('playground.poolEmptyHintFor', { platform: platformLabel(poolPlatform) })
                      : t('playground.poolEmptyHint')}
                  </span>
                ) : null}
              </div>
            </Disclosure>
          </div>
        </div>
      </SplitPane>
    </div>
  )
}
