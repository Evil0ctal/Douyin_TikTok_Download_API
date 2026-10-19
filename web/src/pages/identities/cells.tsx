import { useTranslation } from 'react-i18next'

import { LockIcon, StatusBadge } from '@/components'
import { useFormatters } from '@/hooks'

import styles from '../identities.module.css'
import { ExpiryTag, HEALTH_FAIR, HEALTH_GOOD } from './shared'
import type { IdentityRow } from './types'

/**
 * What the stored jar looks like - never how the identity's requests went.
 *
 * The verdict is a static check on one cookie: present, and long enough to be a
 * real session rather than the bootstrap value the platform hands a first-time
 * visitor. It touches no network and reads no history, which is exactly why it
 * is worth a column of its own: an identity whose credential is complete and
 * whose requests keep failing has a signing or egress problem, not a jar
 * problem, and that pair is the fastest read on this page.
 *
 * It gets its own vocabulary for the same reason. Both columns used to render
 * the health words, so two different measurements said "healthy" and a
 * disagreement between them read as the page contradicting itself.
 */
export function SessionCell({ identity }: { identity: IdentityRow }) {
  const { t } = useTranslation(['console', 'common'])
  const verdict = identity.session?.verdict ?? 'unknown'
  const cookie = identity.session?.cookie
  const title = cookie
    ? t('console:identity.session.checked', {
        cookie,
        verdict: t(`common:state.credential.${verdict}`),
      })
    : undefined

  return (
    <span className="u-row" title={title}>
      <StatusBadge kind="credential" value={verdict} size="sm" flash={false} />
      {cookie ? <span className="u-xs u-muted u-mono">{cookie}</span> : null}
    </span>
  )
}

/**
 * Health as the API reports it when it does, and as consecutive failures when
 * it does not. Both are colour plus icon plus words, never colour alone.
 *
 * `health` is null exactly when the aggregate has no traffic for this identity,
 * which is not a pass. A freshly minted identity that has never served a
 * request used to draw the same unqualified "Healthy" badge as one measured at
 * 100% over the last hour - collapsing "nothing is known" into "it works",
 * which is the one distinction this column exists to make.
 */
export function HealthCell({ identity }: { identity: IdentityRow }) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  if (identity.health !== null && identity.health !== undefined) {
    const tone =
      identity.health >= HEALTH_GOOD
        ? 'healthy'
        : identity.health >= HEALTH_FAIR
          ? 'unknown'
          : 'unhealthy'
    return (
      <span className="u-row">
        <StatusBadge kind="health" value={tone} size="sm" flash={false} />
        <span className="u-mono u-xs">{format.percent(identity.health, 0)}</span>
      </span>
    )
  }

  if (identity.consecutive_fails === 0) {
    return (
      <span className="u-row" title={t('console:identity.health.noTrafficHint')}>
        <StatusBadge kind="health" value="unknown" size="sm" flash={false} />
        <span className="u-xs u-muted">{t('console:identity.health.noTraffic')}</span>
      </span>
    )
  }
  return (
    <span className="u-row">
      <StatusBadge
        kind="health"
        value={identity.consecutive_fails >= 3 ? 'unhealthy' : 'unknown'}
        size="sm"
        flash={false}
      />
      <span className="u-mono u-xs u-muted">
        {t('console:identity.failCount', { count: identity.consecutive_fails })}
      </span>
    </span>
  )
}

/**
 * The state, with the rotation it is in underneath.
 *
 * Under the badge rather than beside it: beside it, the English tag ran into
 * the Health column, and a wider badge such as Degraded pushed it further.
 */
export function StateCell({ identity }: { identity: IdentityRow }) {
  const { t } = useTranslation('console')
  const endpoints = identity.rotation_endpoints
  if (endpoints == null) return <StatusBadge kind="identity" value={identity.state} />
  return (
    <div className="u-stack-sm" style={{ alignItems: 'flex-start' }}>
      <StatusBadge kind="identity" value={identity.state} />
      {endpoints.length > 0 ? (
        // Calling it reserved would tell the operator it never rotates, when
        // it does for these endpoints.
        <span
          className="u-xs u-muted u-nowrap"
          title={t('identity.rotation.limited', { endpoints: endpoints.join(', ') })}
        >
          {t('identity.rotation.limitedTag')}
        </span>
      ) : (
        <span className="u-xs u-muted u-nowrap" title={t('identity.rotation.reservedHint')}>
          {t('identity.rotation.reserved')}
        </span>
      )}
    </div>
  )
}

/**
 * Where the identity came from and whether it is somebody's account.
 *
 * One column where there were two, Source and Logged in, which between them
 * spent 230px of a crowded table on two words. A login also says when it
 * lapses, because that is the next thing anybody looking at a login wants.
 */
export function KindCell({ identity }: { identity: IdentityRow }) {
  const { t } = useTranslation('console')
  return (
    <span className={styles.kind}>
      <span>{t(`identity.source.${identity.source}`, { defaultValue: identity.source })}</span>
      {identity.authenticated ? (
        <span className={styles.kindLogin}>
          <span className="u-row" style={{ color: 'var(--warning)', gap: 'var(--space-1)' }}>
            <LockIcon size={11} />
            {t('identity.kind.loggedIn')}
          </span>
          {identity.expires_at ? <ExpiryTag expiresAt={identity.expires_at} /> : null}
        </span>
      ) : (
        <span className="u-xs u-muted">{t('identity.kind.guest')}</span>
      )}
    </span>
  )
}
