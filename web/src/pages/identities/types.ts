/**
 * What the identity routes answer, as the Identities page reads it.
 *
 * Mirrors src/dtk/api/routes/admin/identities.py. No shape here carries a
 * cookie value except `RevealedJar`, which is the one route built to - audited
 * and behind the operator role.
 */

import type { ApiError } from '@/lib/api'
import type { Identity, Outcome, Platform } from '@/lib/types'

/** A listing row: the shared shape plus what only this page reads. */
export interface IdentityRow extends Identity {
  /** When the login lapses, from the session cookie. Null for a guest. */
  expires_at?: string | null
}

/** One page of the listing, with what `meta` says about the rest. */
export interface IdentityPage {
  rows: IdentityRow[]
  /** Every identity the filters match, not just this page. */
  total: number
  /** Retired identities the same filters left out. */
  retired: number
}

export type CookieRole = 'required' | 'session' | 'useful' | 'other'

/** What the import preview understood. No endpoint ever returns a cookie value. */
export interface CookieReport {
  detected_format: string
  /** The platform the cookie names point at, whatever was chosen. */
  detected_platform?: Platform | null
  cookie_names: string[]
  cookies_masked: Record<string, string>
  /** Each cookie's role for the chosen platform, judged by the server. */
  cookie_roles?: Record<string, CookieRole>
  authenticated: boolean
  expires_at: string | null
  missing_required: string[]
  warnings: string[]
  usable: boolean
  browser_family: string | null
  browser_major: number | null
}

export interface ImportResponse {
  stored: boolean
  identity_id?: string
  report: CookieReport
}

/** One identity probe (mirrors IdentityProbe in src/dtk/ops/probes.py). */
export interface IdentityProbeResult {
  ok?: boolean
  outcome?: Outcome | null
  status?: number | null
  latency_ms?: number | null
  rule?: string | null
  detail?: string | null
}

export interface ProbeState {
  running: boolean
  result?: IdentityProbeResult
  error?: ApiError
}

/**
 * What one identity's cookie jar holds, from
 * `GET /admin/identities/{id}/cookies`.
 *
 * Masked values, never real ones. What is here is enough to answer the
 * questions an operator actually has when an identity stops working: is the
 * cookie there at all, is it the length it should be, and is it the same jar
 * as the one that works.
 */
export interface CookieEntry {
  name: string
  role: CookieRole
  masked: string
  length: number
}

export interface CookieInventory {
  identity_id: string
  platform: Platform
  source: string
  authenticated: boolean
  /** False when the jar cannot be decrypted - the secret key was rotated. */
  readable: boolean
  retired: boolean
  session: { cookie: string | null; verdict: string; held?: boolean }
  missing_required: string[]
  cookies: CookieEntry[]
}

/** What the session check reports once its task finishes. */
export interface SessionCheck {
  logged_in: boolean
  account_id: string | null
  /** `live`, `signed_out`, `indeterminate`, `refused` or `unreachable`. */
  reason: string
  status: number | null
  latency_ms: number | null
  detail: string | null
}

/** What `POST /admin/identities/export` writes, and what the file import reads. */
export interface IdentityBundleFile {
  version: number
  exported_at: string
  warning: string
  identities: Array<Record<string, unknown>>
}

export interface BundleEntryResult {
  index: number
  platform?: string
  usable?: boolean
  authenticated?: boolean
  expires_at?: string | null
  /** Why it was skipped: `no_cookies`, `unknown_platform` or `unusable`. */
  reason?: string
  missing_required?: string[]
  identity_id?: string
  warnings?: string[]
}

export interface BundleImportResult {
  stored: number
  submitted: number
  results: BundleEntryResult[]
}

/** `GET /admin/identities/{id}/cookies/reveal` - the jar, values and all. */
export interface RevealedJar {
  identity_id: string
  platform: Platform
  authenticated: boolean
  cookies: Record<string, string>
  /** The same jar as one `Cookie:` header, ready to paste into curl. */
  header: string
  expires_at: string | null
}

export interface ProxyOption {
  id: string
  label?: string | null
  url_masked: string | null
  healthy: boolean
}

/** A platform's logged-in identities, by what rotation can do with them. */
export interface LoginCoverage {
  /** Handed to the endpoints that need a login. */
  rotating: number
  /** In rotation only for endpoints that need none. */
  limited: number
  /** Only a request naming them reaches them. */
  reserved: number
  /** Back in rotation once their cooldown runs out. */
  cooling: number
}

export interface PoolPlatform {
  platform: Platform
  usable: number
  live: number
  active: number
  minting: number
  below_minimum: boolean
  /** This platform's marks after inheritance: what the worker compares. */
  min_size: number
  target_size: number
  /** True when the number is the global one rather than an override. */
  min_inherited: boolean
  target_inherited: boolean
  /** False when `min_size` is 0: no automatic minting, no level alerts. */
  auto: boolean
  /**
   * Whether this platform can be minted here at all. Bilibili guests are plain
   * HTTP, so they can be where the others need the browser container. Absent
   * from an API older than that, where the top-level `can_mint` decided.
   */
  can_mint?: boolean
  /** Absent from an API older than the login coverage. */
  logins?: LoginCoverage
  /** Endpoints that answer IDENTITY_LOGIN_REQUIRED with no login in rotation. */
  login_required_for?: string[]
}

/** One attempt, as the worker recorded it. `reason` is the code it logs. */
export interface MintAttempt {
  ts: string
  platform: string
  ok: boolean
  reason: string
  identity_id: string | null
  error: string | null
}

export interface MintBackoff {
  route?: string
  failures: number
  until: string
}

export interface MintActivity {
  current: { platform: string; started_at: string } | null
  recent: MintAttempt[]
  /** The longest backoff, for an API older than the per-platform record. */
  backoff: MintBackoff | null
  /** Per platform, only the ones resting. */
  backoffs?: Partial<Record<Platform, MintBackoff>>
}

export interface PoolLevel {
  /** The global pair, which a platform without an override follows. */
  min_size: number
  target_size: number
  max_fail_streak: number
  can_mint: boolean
  platforms: PoolPlatform[]
  activity: MintActivity
}

/** Every key the page's list and pool queries live under. */
export const IDENTITIES_KEY = ['admin', 'identities'] as const
export const POOL_KEY = ['admin', 'identities', 'pool'] as const
export const PROXY_OPTIONS_KEY = ['admin', 'proxies', 'options'] as const
