/**
 * The one status mapping for the whole console.
 *
 * Colour is never the only carrier: every entry pairs a tone with an icon and a
 * translation key, because screenshots lose colour and pasted tables lose
 * everything but the text (docs/design/12-design-system.md).
 *
 * BUSINESS_ERROR and its relatives are deliberately muted rather than red. A
 * deleted video is not a system fault, and painting it like one sends users
 * hunting for a bug that does not exist.
 */

import type { ErrorCode } from './api'
import type { CircuitState, IdentityState, Outcome, TaskState, UserRole } from './types'

export type Tone = 'success' | 'warning' | 'caution' | 'danger' | 'neutral' | 'muted' | 'accent'

export type StatusIconName =
  | 'dot'
  | 'half'
  | 'alert'
  | 'cross'
  | 'check'
  | 'clock'
  | 'spinner'
  | 'minus'
  | 'pause'

export interface StatusMeta {
  tone: Tone
  icon: StatusIconName
  /** Key in the common namespace; the raw enum value itself is never translated. */
  labelKey: string
}

export const IDENTITY_STATE_STATUS: Record<IdentityState, StatusMeta> = {
  minting: { tone: 'accent', icon: 'spinner', labelKey: 'state.identity.minting' },
  active: { tone: 'success', icon: 'dot', labelKey: 'state.identity.active' },
  cooling: { tone: 'warning', icon: 'half', labelKey: 'state.identity.cooling' },
  degraded: { tone: 'caution', icon: 'alert', labelKey: 'state.identity.degraded' },
  retired: { tone: 'neutral', icon: 'cross', labelKey: 'state.identity.retired' },
}

export const TASK_STATE_STATUS: Record<TaskState, StatusMeta> = {
  queued: { tone: 'neutral', icon: 'clock', labelKey: 'state.task.queued' },
  running: { tone: 'accent', icon: 'spinner', labelKey: 'state.task.running' },
  done: { tone: 'success', icon: 'check', labelKey: 'state.task.done' },
  failed: { tone: 'danger', icon: 'cross', labelKey: 'state.task.failed' },
}

export const OUTCOME_STATUS: Record<Outcome, StatusMeta> = {
  ok: { tone: 'success', icon: 'check', labelKey: 'state.outcome.ok' },
  // Muted on purpose: the content is gone, the system is fine.
  business_error: { tone: 'muted', icon: 'minus', labelKey: 'state.outcome.business_error' },
  risk_control: { tone: 'danger', icon: 'alert', labelKey: 'state.outcome.risk_control' },
  network_error: { tone: 'caution', icon: 'alert', labelKey: 'state.outcome.network_error' },
}

export const CIRCUIT_STATUS: Record<CircuitState, StatusMeta> = {
  closed: { tone: 'success', icon: 'dot', labelKey: 'state.circuit.closed' },
  half_open: { tone: 'warning', icon: 'half', labelKey: 'state.circuit.half_open' },
  open: { tone: 'danger', icon: 'cross', labelKey: 'state.circuit.open' },
}

export const HEALTH_STATUS: Record<'healthy' | 'unhealthy' | 'unknown', StatusMeta> = {
  healthy: { tone: 'success', icon: 'check', labelKey: 'state.health.healthy' },
  unhealthy: { tone: 'danger', icon: 'cross', labelKey: 'state.health.unhealthy' },
  unknown: { tone: 'neutral', icon: 'minus', labelKey: 'state.health.unknown' },
}

export const KEY_STATUS: Record<'active' | 'revoked' | 'expired', StatusMeta> = {
  active: { tone: 'success', icon: 'dot', labelKey: 'state.key.active' },
  revoked: { tone: 'neutral', icon: 'cross', labelKey: 'state.key.revoked' },
  expired: { tone: 'warning', icon: 'clock', labelKey: 'state.key.expired' },
}

export const ROLE_STATUS: Record<UserRole, StatusMeta> = {
  admin: { tone: 'accent', icon: 'dot', labelKey: 'state.role.admin' },
  operator: { tone: 'success', icon: 'dot', labelKey: 'state.role.operator' },
  viewer: { tone: 'neutral', icon: 'dot', labelKey: 'state.role.viewer' },
}

const UNKNOWN_STATUS: StatusMeta = { tone: 'neutral', icon: 'minus', labelKey: 'state.unknown' }

/**
 * Tone per error code. Anything the platform decided (content gone, private,
 * unsupported) is muted; anything the operator can act on is warned or flagged.
 */
export const ERROR_CODE_TONE: Record<ErrorCode, Tone> = {
  INVALID_URL: 'muted',
  UNSUPPORTED_CONTENT: 'muted',
  INVALID_PARAM: 'caution',
  UNAUTHENTICATED: 'caution',
  FORBIDDEN_SCOPE: 'caution',
  NOT_FOUND: 'muted',
  CONTENT_PRIVATE: 'muted',
  RATE_LIMITED: 'warning',
  IDENTITY_POOL_EXHAUSTED: 'warning',
  ENDPOINT_CIRCUIT_OPEN: 'warning',
  UPSTREAM_RISK_CONTROL: 'danger',
  UPSTREAM_CHANGED: 'danger',
  SIGNING_FAILED: 'danger',
  TASK_NOT_FOUND: 'muted',
  SETUP_ALREADY_DONE: 'neutral',
  SETUP_TOKEN_INVALID: 'danger',
  INTERNAL: 'danger',
}

export type StatusKind =
  | 'identity'
  | 'task'
  | 'outcome'
  | 'circuit'
  | 'health'
  | 'key'
  | 'role'

const REGISTRY: Record<StatusKind, Record<string, StatusMeta>> = {
  identity: IDENTITY_STATE_STATUS,
  task: TASK_STATE_STATUS,
  outcome: OUTCOME_STATUS,
  circuit: CIRCUIT_STATUS,
  health: HEALTH_STATUS,
  key: KEY_STATUS,
  role: ROLE_STATUS,
}

/** Looks a status up; an unknown value degrades to neutral rather than throwing. */
export function statusMeta(kind: StatusKind, value: string | null | undefined): StatusMeta {
  if (!value) return UNKNOWN_STATUS
  return REGISTRY[kind][value] ?? UNKNOWN_STATUS
}

export function errorTone(code: ErrorCode | null | undefined): Tone {
  if (!code) return 'neutral'
  return ERROR_CODE_TONE[code] ?? 'danger'
}
