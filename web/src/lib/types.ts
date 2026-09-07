/**
 * Domain types shared by every page.
 *
 * These mirror src/dtk/core/types.py and docs/design/11-data-contracts.md. Enum
 * values are the wire format and are never translated or reshaped: the console
 * displays a localized label beside the stable value, never instead of it.
 *
 * Ids are strings everywhere. aweme_id is 19 digits and silently loses precision
 * as a JavaScript number.
 */

export const PLATFORMS = ['douyin', 'tiktok'] as const
export type Platform = (typeof PLATFORMS)[number]

export const CONTENT_KINDS = ['video', 'image_album', 'live'] as const
export type ContentKind = (typeof CONTENT_KINDS)[number]

export const IDENTITY_STATES = ['minting', 'active', 'cooling', 'degraded', 'retired'] as const
export type IdentityState = (typeof IDENTITY_STATES)[number]

export const IDENTITY_SOURCES = ['minted', 'imported'] as const
export type IdentitySource = (typeof IDENTITY_SOURCES)[number]

export const TASK_STATES = ['queued', 'running', 'done', 'failed'] as const
export type TaskState = (typeof TASK_STATES)[number]

export const OUTCOMES = ['ok', 'business_error', 'risk_control', 'network_error'] as const
export type Outcome = (typeof OUTCOMES)[number]

export const REJECT_REASONS = [
  'circuit_open',
  'no_identity',
  'no_token',
  'all_inflight',
  'queue_full',
  'wait_timeout',
] as const
export type RejectReason = (typeof REJECT_REASONS)[number]

export const USER_ROLES = ['admin', 'operator', 'viewer'] as const
export type UserRole = (typeof USER_ROLES)[number]

export const SCOPES = ['douyin:read', 'tiktok:read', 'identity:manage', 'admin'] as const
export type Scope = (typeof SCOPES)[number]

export const BROWSER_FAMILIES = ['chrome', 'firefox', 'safari'] as const
export type BrowserFamily = (typeof BROWSER_FAMILIES)[number]

/** Circuit state of one upstream endpoint. */
export const CIRCUIT_STATES = ['closed', 'half_open', 'open'] as const
export type CircuitState = (typeof CIRCUIT_STATES)[number]

/** Where a settings value currently comes from (docs/design/10-configuration.md). */
export const CONFIG_SOURCES = ['database', 'env', 'default'] as const
export type ConfigSource = (typeof CONFIG_SOURCES)[number]

export const CONFIG_TIERS = ['bootstrap', 'runtime', 'sensitive'] as const
export type ConfigTier = (typeof CONFIG_TIERS)[number]

export interface SetupStatus {
  initialized: boolean
}

export interface SessionUser {
  id: string
  username: string
  role: UserRole
  last_login_at?: string | null
}

export interface Identity {
  id: string
  platform: Platform
  state: IdentityState
  source: IdentitySource
  authenticated: boolean
  proxy_id?: string | null
  proxy_label?: string | null
  cooldown_until?: string | null
  consecutive_fails: number
  minted_at: string
  last_used_at?: string | null
  retired_at?: string | null
  retire_reason?: string | null
  health?: number | null
  fingerprint?: Record<string, unknown> | null
  cookie_expires_at?: string | null
}

export interface Proxy {
  id: string
  label?: string | null
  /** Always masked by the server; credentials never travel to the console. */
  url_masked: string
  country?: string | null
  timezone?: string | null
  healthy: boolean
  last_check_at?: string | null
  latency_ms?: number | null
  identity_count?: number | null
  created_at: string
}

export interface ApiKeySummary {
  id: string
  name: string
  prefix: string
  scopes: Scope[]
  rate_limit?: number | null
  expires_at?: string | null
  revoked_at?: string | null
  last_used_at?: string | null
  created_at: string
}

/** Returned once, at creation time, and never again. */
export interface ApiKeyCreated extends ApiKeySummary {
  secret: string
}

export interface EndpointHealth {
  platform: Platform
  endpoint: string
  circuit: CircuitState
  success_rate?: number | null
  risk_rate?: number | null
  requests?: number | null
  last_success_at?: string | null
  reopen_at?: string | null
}

export interface PoolSummary {
  active: number
  cooling: number
  degraded: number
  minting?: number
  retired?: number
}

export interface ComponentStatus {
  ok: boolean
  latency_ms?: number | null
  detail?: string | null
  warm_contexts?: number | null
  chromium_major?: number | null
  wreq_profile_major?: number | null
}

export interface SystemStatus {
  version: string
  commit: string
  uptime_seconds: number
  components: Record<string, ComponentStatus>
  pool: PoolSummary
  storage: {
    db_size_bytes?: number | null
    request_log_rows?: number | null
    [table: string]: number | null | undefined
  }
}

export interface TimeSeriesPoint {
  /** ISO 8601, UTC. The console converts to local time for display. */
  ts: string
  value: number | null
}

export interface TimeSeries {
  id: string
  points: TimeSeriesPoint[]
}

export interface RequestLogRow {
  ts: string
  request_id: string
  task_id?: string | null
  platform: Platform
  endpoint: string
  identity_id?: string | null
  proxy_id?: string | null
  outcome: Outcome
  http_status?: number | null
  duration_ms: number
  cache_hit: boolean
  signer?: string | null
  error_code?: string | null
  reject_reason?: RejectReason | null
}

export interface AuditLogRow {
  id: string
  ts: string
  user_id?: string | null
  username?: string | null
  action: string
  target_type?: string | null
  target_id?: string | null
  detail?: Record<string, unknown> | null
  ip?: string | null
}

export interface SettingItem {
  key: string
  value: unknown
  source: ConfigSource
  tier: ConfigTier
  default?: unknown
  requires_restart?: boolean
}

export interface DiagnosticStep {
  id: string
  ok: boolean
  detail?: string | null
  suggestion?: string | null
  duration_ms?: number | null
}

export interface DiagnosticReport {
  ok: boolean
  started_at: string
  steps: DiagnosticStep[]
  /** Pre-redacted text, ready to paste into an issue. */
  text?: string | null
}
