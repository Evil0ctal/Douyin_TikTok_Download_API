import { useCallback, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { useFormatters } from '@/hooks'
import type { Platform } from '@/lib/types'

import styles from '../identities.module.css'
import type { IdentityRow, PoolLevel } from './types'

/**
 * A platform as people say it - Douyin, TikTok, Bilibili - in this language.
 *
 * The page printed the wire value everywhere, which is an identifier and not
 * a name; in Chinese it read as the one English word in a sentence. Falls back
 * to the wire value for a platform this build has no name for.
 */
export function usePlatformName(): (platform: string) => string {
  const { t } = useTranslation('console')
  return useCallback(
    (platform: string) => t(`platform.name.${platform}`, { defaultValue: platform }),
    [t],
  )
}

export function Fact({
  label,
  value,
  mono,
}: {
  label: ReactNode
  value: ReactNode
  mono?: boolean
}) {
  return (
    <span className="u-stack-sm">
      <span className="u-xs u-muted">{label}</span>
      <span className={mono ? 'u-mono' : undefined}>{value}</span>
    </span>
  )
}

/**
 * Hand the browser a JSON file.
 *
 * The same shape the archive export and the comment save use. Revoked a tick
 * later rather than immediately: Safari cancels an in-flight save otherwise.
 */
export function saveJson(data: unknown, filename: string): void {
  const href = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
  )
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => {
    URL.revokeObjectURL(href)
  }, 1_000)
}

/** A file name that sorts by date and does not overwrite the last export. */
export function exportFilename(): string {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
  return `dtk-identities-${stamp}.json`
}

export function remainingMs(until: string | null | undefined): number | null {
  if (!until) return null
  const value = new Date(until).getTime() - Date.now()
  return Number.isFinite(value) && value > 0 ? value : null
}

/**
 * Whether resetting this identity would change anything.
 *
 * Cooling and degraded are the states a reset lifts. A streak matters even on
 * an active identity: it is what `usable_count` measures the pool against, so
 * an active identity carrying five failures is one the refill job has already
 * written off. Retired is excluded because its cookie jar was wiped.
 */
export function needsReset(row: IdentityRow): boolean {
  if (row.state === 'retired') return false
  return row.state === 'cooling' || row.state === 'degraded' || row.consecutive_fails > 0
}

/**
 * Session verdicts that mean the credential is spent, mirroring
 * `_session_health` in src/dtk/api/routes/admin/identities.py. `missing` holds
 * no session value at all; `too_short` holds the bootstrap value the document
 * sets rather than the one the platform SDK replaces it with. A request
 * carrying either is refused with a perfectly correct signature, so neither can
 * serve traffic, and neither is worth an operator's attention by default.
 */
export const DEAD_SESSION: ReadonlySet<string> = new Set(['missing', 'too_short'])

/**
 * Where a measured score stops reading as good and starts reading as poor. The
 * cell and the row flash share these, so the colour on screen and the change
 * worth animating cannot disagree about where a band ends.
 */
export const HEALTH_GOOD = 0.8
export const HEALTH_FAIR = 0.4

/**
 * The band HealthCell paints, not the score behind it. The flash key uses this
 * so a drift from 0.94 to 0.93 between two polls is not reported as an event.
 */
export function healthBand(identity: IdentityRow): string {
  if (identity.health === null || identity.health === undefined) return 'none'
  if (identity.health >= HEALTH_GOOD) return 'good'
  return identity.health >= HEALTH_FAIR ? 'fair' : 'poor'
}

/** Under this a login is highlighted: a week is the time to re-import one. */
const SOON_MS = 7 * 24 * 60 * 60 * 1000
const HOUR_MS = 60 * 60 * 1000
const DAY_MS = 24 * HOUR_MS

export type ExpiryTone = 'expired' | 'soon' | 'later'

/** How far off a login's end is, read against the clock when it is called. */
function expiryOf(expiresAt: string): { tone: ExpiryTone; days: number; hours: number } {
  const left = new Date(expiresAt).getTime() - Date.now()
  if (!Number.isFinite(left)) return { tone: 'later', days: 0, hours: 0 }
  const tone: ExpiryTone = left <= 0 ? 'expired' : left < SOON_MS ? 'soon' : 'later'
  return {
    tone,
    days: Math.floor(left / DAY_MS),
    hours: Math.max(1, Math.floor(left / HOUR_MS)),
  }
}

/** Whether a login's end is behind us. */
export function hasLapsed(expiresAt: string | null | undefined): boolean {
  return Boolean(expiresAt) && expiryOf(expiresAt ?? '').tone === 'expired'
}

/**
 * "expires in 3d", "expires in 5h" or "expired", with the date on hover.
 *
 * Read from the session cookie, so it is what the platform said when it set
 * the login - a session the platform ended early still shows its old date,
 * which is what "Check the login" is for.
 */
export function ExpiryTag({ expiresAt }: { expiresAt: string }) {
  const { t } = useTranslation('console')
  const format = useFormatters()
  const { tone, days, hours } = expiryOf(expiresAt)

  const label =
    tone === 'expired'
      ? t('identity.expiry.expired')
      : days >= 1
        ? t('identity.expiry.days', { count: days })
        : t('identity.expiry.hours', { count: hours })

  return (
    <span
      className={styles.expiry}
      data-tone={tone}
      title={
        tone === 'expired'
          ? t('identity.expiry.expiredOn', { date: format.dateTime(expiresAt) })
          : t('identity.expiry.on', { date: format.dateTime(expiresAt) })
      }
    >
      {label}
    </span>
  )
}

/**
 * The endpoints that lose their last login if `identity` leaves rotation.
 *
 * Null when nothing would: a guest, an identity already out of rotation, a
 * platform whose endpoints all serve a guest, or one with another login in
 * rotation. The scheduler skips a reserved login even for an endpoint that
 * requires one (`Scheduler._rotation_pool`), so the moment the last one goes,
 * paging answers IDENTITY_LOGIN_REQUIRED.
 */
export function lastLoginEndpoints(
  identity: IdentityRow,
  pool: PoolLevel | undefined,
): string[] | null {
  if (!identity.authenticated || !pool) return null
  if (identity.state !== 'active' && identity.state !== 'degraded') return null
  if (identity.rotation_endpoints != null) return null
  const row = pool.platforms.find((entry) => entry.platform === identity.platform)
  const needed = row?.login_required_for ?? []
  if (!row?.logins || needed.length === 0) return null
  return row.logins.rotating <= 1 ? needed : null
}

/** Each platform among `targets` whose last login in rotation is in `targets`. */
export function lastLoginLosses(
  targets: IdentityRow[],
  pool: PoolLevel | undefined,
): Array<{ platform: Platform; endpoints: string[] }> {
  if (!pool) return []
  const losses: Array<{ platform: Platform; endpoints: string[] }> = []
  for (const row of pool.platforms) {
    const needed = row.login_required_for ?? []
    if (!row.logins || needed.length === 0 || row.logins.rotating === 0) continue
    const leaving = targets.filter(
      (target) =>
        target.platform === row.platform &&
        target.authenticated &&
        target.rotation_endpoints == null &&
        (target.state === 'active' || target.state === 'degraded'),
    ).length
    if (leaving >= row.logins.rotating) losses.push({ platform: row.platform, endpoints: needed })
  }
  return losses
}

/** What a bulk action did, one target at a time. */
export interface BulkOutcome<T> {
  done: T[]
  failed: Array<{ target: T; error: unknown }>
}

/**
 * Act on every target, whatever happens to the others.
 *
 * Sequential, because these are writes against one database and a burst of
 * them buys nothing. It used to stop at the first refusal and say only that
 * one, so after "retire 12" failed on the fourth an operator could not tell
 * whether three had gone or none.
 */
export async function runAll<T>(
  targets: T[],
  act: (target: T) => Promise<unknown>,
): Promise<BulkOutcome<T>> {
  const outcome: BulkOutcome<T> = { done: [], failed: [] }
  for (const target of targets) {
    try {
      await act(target)
      outcome.done.push(target)
    } catch (error) {
      outcome.failed.push({ target, error })
    }
  }
  return outcome
}
