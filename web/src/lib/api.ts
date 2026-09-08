/**
 * Typed client for the dtk API.
 *
 * Everything the API returns shares one envelope: {success, data, error, meta}.
 * This module unwraps it, turns the stable error-code enum into a discriminated
 * union, records the rate-limit headers, and hides the async-first protocol -
 * a 202 with a task_id is polled here so callers can simply await a result
 * (docs/design/06-api-auth-mcp.md).
 */

import { i18next } from './i18n'
import { currentLanguage } from './language'
import { paths } from './endpoints'
import type { TaskState } from './types'

/* -------------------------------------------------------------------------- */
/* Error codes                                                                 */
/* -------------------------------------------------------------------------- */

/** Mirrors ErrorCode in src/dtk/core/errors.py. Append-only, never renamed. */
export const ERROR_CODES = [
  'INVALID_URL',
  'UNSUPPORTED_CONTENT',
  'INVALID_PARAM',
  'UNAUTHENTICATED',
  'FORBIDDEN_SCOPE',
  'NOT_FOUND',
  'CONTENT_PRIVATE',
  'RATE_LIMITED',
  'IDENTITY_POOL_EXHAUSTED',
  'ENDPOINT_CIRCUIT_OPEN',
  'UPSTREAM_RISK_CONTROL',
  'UPSTREAM_CHANGED',
  'SIGNING_FAILED',
  'TASK_NOT_FOUND',
  'SETUP_ALREADY_DONE',
  'SETUP_TOKEN_INVALID',
  'NOT_CONFIGURED',
  'QUEUE_FULL',
  'INTERNAL',
] as const

export type ErrorCode = (typeof ERROR_CODES)[number]

const ERROR_CODE_SET: ReadonlySet<string> = new Set(ERROR_CODES)

export function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === 'string' && ERROR_CODE_SET.has(value)
}

/**
 * Codes a caller must not retry. Mirrors NON_RETRYABLE in errors.py: retrying a
 * deleted video or a malformed URL only burns identities.
 */
export const NON_RETRYABLE_CODES: ReadonlySet<ErrorCode> = new Set<ErrorCode>([
  'INVALID_URL',
  'UNSUPPORTED_CONTENT',
  'INVALID_PARAM',
  'UNAUTHENTICATED',
  'FORBIDDEN_SCOPE',
  'NOT_FOUND',
  'CONTENT_PRIVATE',
  'UPSTREAM_CHANGED',
  'SETUP_ALREADY_DONE',
  'SETUP_TOKEN_INVALID',
])

/**
 * The error body, discriminated on `code`, so a switch narrows exhaustively:
 *
 *   switch (error.payload.code) {
 *     case 'RATE_LIMITED': ...
 *   }
 */
export type ApiErrorPayload = {
  [Code in ErrorCode]: {
    code: Code
    message: string
    retry_after?: number | null
    details?: Record<string, unknown> | null
  }
}[ErrorCode]

/* -------------------------------------------------------------------------- */
/* Envelope                                                                    */
/* -------------------------------------------------------------------------- */

export interface Cursor {
  next: string | null
  has_more: boolean
}

export interface ResponseMeta {
  request_id?: string
  cached?: boolean
  duration_ms?: number
  cursor?: Cursor
  [key: string]: unknown
}

export interface Envelope<T> {
  success: boolean
  data: T | null
  error: ApiErrorPayload | null
  meta?: ResponseMeta
}

export interface RateLimit {
  limit: number | null
  remaining: number | null
  /** Unix seconds at which the window resets. */
  reset: number | null
  retryAfter: number | null
  observedAt: number
}

export interface ApiResponse<T> {
  data: T
  meta: ResponseMeta
  status: number
  rateLimit: RateLimit | null
}

export interface TaskEnvelope<T = unknown> {
  task_id: string
  state: TaskState
  data?: T | null
  error?: ApiErrorPayload | null
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
}

/* -------------------------------------------------------------------------- */
/* ApiError                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * `kind` separates a real API failure from the ways a request can fail before
 * an envelope ever exists. The UI phrases those differently: an unreachable
 * server is not the same message as a rejected request.
 */
export type ApiErrorKind = 'api' | 'network' | 'timeout' | 'aborted' | 'malformed'

export interface ApiErrorInit {
  kind?: ApiErrorKind
  status?: number
  requestId?: string | null
  rawCode?: string
  retryAfter?: number | null
  details?: Record<string, unknown> | null
  cause?: unknown
  /** Catalogue key for a message the console wrote itself; see ApiError.localized. */
  messageKey?: string
  messageArgs?: Record<string, unknown>
}

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly code: ErrorCode
  /** The code exactly as the server sent it, even if it is not in our enum yet. */
  readonly rawCode: string
  readonly status: number
  readonly requestId: string | null
  readonly retryAfter: number | null
  readonly details: Record<string, unknown>
  /** Set when the message is the console's own copy rather than the server's. */
  readonly messageKey: string | null

  constructor(code: ErrorCode, message: string, init: ApiErrorInit = {}) {
    super(message)
    this.name = 'ApiError'
    this.kind = init.kind ?? 'api'
    this.code = code
    this.rawCode = init.rawCode ?? code
    this.status = init.status ?? 0
    this.requestId = init.requestId ?? null
    this.retryAfter = init.retryAfter ?? null
    this.details = init.details ?? {}
    this.messageKey = init.messageKey ?? null
    if (init.cause !== undefined) this.cause = init.cause
    if (init.messageKey) {
      const key = init.messageKey
      const args = init.messageArgs ?? {}
      // Looked up on every read rather than once here: a failure can sit on
      // screen across a language switch, and a sentence baked in at throw time
      // would be the only thing left in the language it was thrown in.
      Object.defineProperty(this, 'message', {
        get: () => i18next.t(key, args),
        configurable: true,
        enumerable: false,
      })
    }
  }

  /**
   * A failure the console phrased itself, carried as a catalogue key.
   *
   * Errors the API returns are already in the negotiated language, so their
   * message is used as it arrives. Anything thrown on this side has no such
   * message and must be looked up instead of hardcoded in English.
   */
  static localized(
    code: ErrorCode,
    messageKey: string,
    init: Omit<ApiErrorInit, 'messageKey'> = {},
  ): ApiError {
    return new ApiError(code, '', { ...init, messageKey })
  }

  /** The narrowable payload; useful for exhaustive handling per code. */
  get payload(): ApiErrorPayload {
    return {
      code: this.code,
      message: this.message,
      retry_after: this.retryAfter,
      details: this.details,
    } as ApiErrorPayload
  }

  get retryable(): boolean {
    if (this.kind === 'aborted') return false
    if (this.kind !== 'api') return true
    return !NON_RETRYABLE_CODES.has(this.code)
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

export function errorCodeOf(error: unknown): ErrorCode | null {
  return isApiError(error) ? error.code : null
}

/* -------------------------------------------------------------------------- */
/* Rate limit store                                                            */
/* -------------------------------------------------------------------------- */

let rateLimit: RateLimit | null = null
const rateLimitListeners = new Set<() => void>()

export function getRateLimit(): RateLimit | null {
  return rateLimit
}

export function subscribeRateLimit(listener: () => void): () => void {
  rateLimitListeners.add(listener)
  return () => {
    rateLimitListeners.delete(listener)
  }
}

function readRateLimit(headers: Headers): RateLimit | null {
  const limit = numberHeader(headers, 'X-RateLimit-Limit')
  const remaining = numberHeader(headers, 'X-RateLimit-Remaining')
  const reset = numberHeader(headers, 'X-RateLimit-Reset')
  const retryAfter = numberHeader(headers, 'Retry-After')
  if (limit === null && remaining === null && reset === null && retryAfter === null) return null

  const next: RateLimit = { limit, remaining, reset, retryAfter, observedAt: Date.now() }
  rateLimit = next
  for (const listener of rateLimitListeners) listener()
  return next
}

function numberHeader(headers: Headers, name: string): number | null {
  const raw = headers.get(name)
  if (raw === null) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

/* -------------------------------------------------------------------------- */
/* Session expiry                                                              */
/* -------------------------------------------------------------------------- */

type UnauthenticatedHandler = (error: ApiError) => void

let onUnauthenticated: UnauthenticatedHandler | null = null

/** App.tsx registers a redirect to the login page here. */
export function setUnauthenticatedHandler(handler: UnauthenticatedHandler | null): void {
  onUnauthenticated = handler
}

/* -------------------------------------------------------------------------- */
/* Request                                                                     */
/* -------------------------------------------------------------------------- */

export type QueryParams = Record<
  string,
  string | number | boolean | null | undefined | Array<string | number>
>

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  params?: QueryParams
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
  /** Per-request network timeout. Task polling has its own, longer, budget. */
  timeoutMs?: number
  /** Set false to receive the raw 202 body instead of polling to completion. */
  awaitTask?: boolean
  taskTimeoutMs?: number
  onTaskState?: (task: TaskEnvelope) => void
  /** Set false on the login form so a 401 does not bounce the user mid-typing. */
  redirectOnUnauthenticated?: boolean
}

const DEFAULT_TIMEOUT_MS = 30_000
const DEFAULT_TASK_TIMEOUT_MS = 120_000
const TASK_POLL_DELAYS_MS = [400, 700, 1_000, 1_500, 2_000]

/** Prefix for every call; empty in production because the api serves the SPA. */
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

function buildUrl(path: string, params?: QueryParams): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === null || value === undefined) continue
    if (Array.isArray(value)) {
      for (const item of value) search.append(key, String(item))
    } else {
      search.append(key, String(value))
    }
  }
  const query = search.toString()
  return `${BASE_URL}${path}${query ? `?${query}` : ''}`
}

interface LinkedSignal {
  signal: AbortSignal
  timedOut: () => boolean
  cleanup: () => void
}

function linkSignal(external: AbortSignal | undefined, timeoutMs: number): LinkedSignal {
  const controller = new AbortController()
  let timedOut = false

  const timer = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  const onExternalAbort = (): void => {
    controller.abort()
  }

  if (external) {
    if (external.aborted) controller.abort()
    else external.addEventListener('abort', onExternalAbort)
  }

  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    cleanup: () => {
      window.clearTimeout(timer)
      external?.removeEventListener('abort', onExternalAbort)
    },
  }
}

function toApiError(payload: ApiErrorPayload, status: number, requestId: string | null): ApiError {
  const code = isErrorCode(payload.code) ? payload.code : 'INTERNAL'
  return new ApiError(code, payload.message || code, {
    kind: 'api',
    status,
    requestId,
    rawCode: String(payload.code),
    retryAfter: payload.retry_after ?? null,
    details: payload.details ?? null,
  })
}

function isEnvelope(value: unknown): value is Envelope<unknown> {
  return typeof value === 'object' && value !== null && 'success' in value
}

function isTaskEnvelope(value: unknown): value is TaskEnvelope {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { task_id?: unknown }).task_id === 'string' &&
    typeof (value as { state?: unknown }).state === 'string'
  )
}

/**
 * Performs one request and unwraps the envelope. Returns data plus meta; use the
 * verb helpers below when meta is not needed.
 */
export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<ApiResponse<T>> {
  const { method = 'GET', params, body, headers = {}, timeoutMs = DEFAULT_TIMEOUT_MS } = options
  const link = linkSignal(options.signal, timeoutMs)

  const requestHeaders: Record<string, string> = {
    Accept: 'application/json',
    // The server localizes error messages from this; codes stay stable.
    'Accept-Language': currentLanguage(),
    ...headers,
  }
  if (body !== undefined && !(body instanceof FormData)) {
    requestHeaders['Content-Type'] = 'application/json'
  }

  let response: Response
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers: requestHeaders,
      // Session auth is an httpOnly cookie on the same origin.
      credentials: 'same-origin',
      signal: link.signal,
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    })
  } catch (cause) {
    link.cleanup()
    if (link.timedOut()) {
      throw new ApiError('INTERNAL', `Request to ${path} timed out.`, {
        kind: 'timeout',
        details: { path, timeout_ms: timeoutMs },
        cause,
      })
    }
    if (options.signal?.aborted) {
      throw new ApiError('INTERNAL', 'Request aborted.', { kind: 'aborted', cause })
    }
    throw new ApiError('INTERNAL', `Cannot reach the API at ${path}.`, {
      kind: 'network',
      details: { path },
      cause,
    })
  }
  link.cleanup()

  const limits = readRateLimit(response.headers)
  const requestIdHeader = response.headers.get('X-Request-ID')

  let payload: unknown = null
  if (response.status !== 204) {
    const text = await response.text()
    if (text.length > 0) {
      try {
        payload = JSON.parse(text) as unknown
      } catch (cause) {
        throw new ApiError('INTERNAL', `The API returned a non-JSON response for ${path}.`, {
          kind: 'malformed',
          status: response.status,
          requestId: requestIdHeader,
          details: { path, body: text.slice(0, 400) },
          cause,
        })
      }
    }
  }

  if (!isEnvelope(payload)) {
    if (response.ok) {
      // 204 and other empty bodies are legitimate.
      return {
        data: (payload ?? null) as T,
        meta: {},
        status: response.status,
        rateLimit: limits,
      }
    }
    throw new ApiError('INTERNAL', `HTTP ${response.status} from ${path}.`, {
      kind: 'malformed',
      status: response.status,
      requestId: requestIdHeader,
      details: { path },
    })
  }

  const meta: ResponseMeta = payload.meta ?? {}
  const requestId = meta.request_id ?? requestIdHeader ?? null

  if (!response.ok || payload.success === false || payload.error) {
    const error = payload.error
      ? toApiError(payload.error, response.status, requestId)
      : new ApiError('INTERNAL', `HTTP ${response.status} from ${path}.`, {
          status: response.status,
          requestId,
        })

    if (
      error.code === 'UNAUTHENTICATED' &&
      options.redirectOnUnauthenticated !== false &&
      onUnauthenticated
    ) {
      onUnauthenticated(error)
    }
    throw error
  }

  // Async-first: a 202 carries a task_id, not a result. Poll it here so callers
  // never have to write a polling loop (docs/design/06).
  if (
    response.status === 202 &&
    options.awaitTask !== false &&
    isTaskEnvelope(payload.data) &&
    payload.data.data == null
  ) {
    const finished = await waitForTask<T>(payload.data.task_id, {
      signal: options.signal,
      timeoutMs: options.taskTimeoutMs ?? DEFAULT_TASK_TIMEOUT_MS,
      onState: options.onTaskState,
      initial: payload.data,
    })
    return { data: finished, meta, status: response.status, rateLimit: limits }
  }

  return { data: payload.data as T, meta, status: response.status, rateLimit: limits }
}

/* -------------------------------------------------------------------------- */
/* Tasks                                                                       */
/* -------------------------------------------------------------------------- */

export interface WaitForTaskOptions {
  signal?: AbortSignal
  timeoutMs?: number
  onState?: (task: TaskEnvelope) => void
  initial?: TaskEnvelope
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    function onAbort(): void {
      window.clearTimeout(timer)
      reject(new ApiError('INTERNAL', 'Request aborted.', { kind: 'aborted' }))
    }
    if (signal?.aborted) {
      window.clearTimeout(timer)
      reject(new ApiError('INTERNAL', 'Request aborted.', { kind: 'aborted' }))
      return
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

export async function getTask<T>(taskId: string, signal?: AbortSignal): Promise<TaskEnvelope<T>> {
  const response = await apiRequest<TaskEnvelope<T>>(paths.tasks.byId(taskId), {
    signal,
    awaitTask: false,
  })
  return response.data
}

/**
 * Polls a task to completion. Backs off from 400ms to 2s: fast enough that a
 * cached hit feels synchronous, slow enough that a queued task does not hammer
 * the API.
 */
export async function waitForTask<T>(
  taskId: string,
  options: WaitForTaskOptions = {},
): Promise<T> {
  const deadline = Date.now() + (options.timeoutMs ?? DEFAULT_TASK_TIMEOUT_MS)
  let attempt = 0
  let task: TaskEnvelope<T> | undefined = options.initial as TaskEnvelope<T> | undefined

  for (;;) {
    if (task) {
      options.onState?.(task)
      if (task.state === 'done') return (task.data ?? null) as T
      if (task.state === 'failed') {
        throw task.error
          ? toApiError(task.error, 200, taskId)
          : ApiError.localized('INTERNAL', 'common:task.failed', {
              messageArgs: { taskId },
              details: { task_id: taskId },
            })
      }
    }

    if (Date.now() >= deadline) {
      throw ApiError.localized('INTERNAL', 'errors:generic.taskTimeout', {
        kind: 'timeout',
        details: { task_id: taskId, state: task?.state ?? 'queued' },
      })
    }

    const delay = TASK_POLL_DELAYS_MS[Math.min(attempt, TASK_POLL_DELAYS_MS.length - 1)] ?? 2_000
    attempt += 1
    await sleep(delay, options.signal)
    task = await getTask<T>(taskId, options.signal)
  }
}

export interface TaskEventHandlers {
  onState?: (task: TaskEnvelope) => void
  onError?: (error: ApiError) => void
  /** The stream ended on its own, having reached a terminal state or timed out. */
  onEnd?: () => void
}

//: The frames src/dtk/api/routes/tasks.py emits, by name.
const TASK_STATE_EVENTS = ['state', 'result'] as const

/**
 * Server-sent progress for one task. Optional: waitForTask covers the same
 * ground by polling, and the console falls back to it when SSE is unavailable.
 * Returns a close function.
 *
 * Every frame is NAMED - the server writes `event: state` before its data - and
 * a named frame is delivered to addEventListener(name), never to onmessage.
 * This listened on onmessage alone, so the stream delivered nothing at all and
 * the only reason no page noticed is that no page calls this yet.
 */
export function openTaskEvents(taskId: string, handlers: TaskEventHandlers): () => void {
  let source: EventSource
  try {
    source = new EventSource(`${BASE_URL}${paths.tasks.events(taskId)}`, {
      withCredentials: true,
    })
  } catch (cause) {
    handlers.onError?.(
      new ApiError('INTERNAL', 'Cannot open the task event stream.', { kind: 'network', cause }),
    )
    return () => {}
  }

  const parse = (raw: string): unknown => {
    try {
      return JSON.parse(raw)
    } catch {
      // A malformed frame is not worth tearing the stream down for.
      return null
    }
  }

  for (const name of TASK_STATE_EVENTS) {
    source.addEventListener(name, (event: MessageEvent<string>) => {
      const parsed = parse(event.data)
      if (isTaskEnvelope(parsed)) handlers.onState?.(parsed)
    })
  }

  // The server's own error frame, which carries a code the console can
  // translate - unlike onerror below, which only knows the socket broke.
  source.addEventListener('error' as 'message', (event: MessageEvent<string>) => {
    if (typeof event.data !== 'string') return
    const parsed = parse(event.data)
    const raw =
      typeof parsed === 'object' && parsed !== null && 'code' in parsed
        ? (parsed as { code: unknown }).code
        : null
    // An unrecognised code becomes INTERNAL rather than being passed through:
    // the console renders the code as a catalogue key, and a key nobody
    // translated shows the reader a raw identifier.
    handlers.onError?.(
      new ApiError(isErrorCode(raw) ? raw : 'INTERNAL', '', { kind: 'api' }),
    )
    source.close()
  })

  for (const name of ['end', 'timeout'] as const) {
    source.addEventListener(name, () => {
      handlers.onEnd?.()
      source.close()
    })
  }

  source.onerror = () => {
    // Fires for a dropped connection AND, in some browsers, once after the
    // server closes a completed stream. Reporting the second as a failure would
    // turn every finished task into an error, so a stream this function has
    // already closed says nothing.
    if (source.readyState === EventSource.CLOSED) return
    handlers.onError?.(
      new ApiError('INTERNAL', 'The task event stream was interrupted.', { kind: 'network' }),
    )
    source.close()
  }

  return () => {
    source.close()
  }
}

/* -------------------------------------------------------------------------- */
/* Verb helpers                                                                */
/* -------------------------------------------------------------------------- */

export async function apiGet<T>(
  path: string,
  options: Omit<RequestOptions, 'method' | 'body'> = {},
): Promise<T> {
  const response = await apiRequest<T>(path, { ...options, method: 'GET' })
  return response.data
}

export async function apiPost<T>(
  path: string,
  body?: unknown,
  options: Omit<RequestOptions, 'method' | 'body'> = {},
): Promise<T> {
  const response = await apiRequest<T>(path, { ...options, method: 'POST', body })
  return response.data
}

export async function apiPut<T>(
  path: string,
  body?: unknown,
  options: Omit<RequestOptions, 'method' | 'body'> = {},
): Promise<T> {
  const response = await apiRequest<T>(path, { ...options, method: 'PUT', body })
  return response.data
}

export async function apiPatch<T>(
  path: string,
  body?: unknown,
  options: Omit<RequestOptions, 'method' | 'body'> = {},
): Promise<T> {
  const response = await apiRequest<T>(path, { ...options, method: 'PATCH', body })
  return response.data
}

export async function apiDelete<T>(
  path: string,
  options: Omit<RequestOptions, 'method'> = {},
): Promise<T> {
  const response = await apiRequest<T>(path, { ...options, method: 'DELETE' })
  return response.data
}
