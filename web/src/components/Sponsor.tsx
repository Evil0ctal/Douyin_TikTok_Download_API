import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'

import { ExternalIcon } from './Icons'
import styles from './sponsor.module.css'

/**
 * The people who pay for this project, in the places they are visible.
 *
 * One definition, three placements. The alternative - a copy per page - is how
 * a sponsor ends up with a stale logo in one corner of a console and a current
 * one in another, and how a sponsorship that has ended keeps running somewhere
 * nobody remembered to look.
 *
 * Two things are deliberate and should stay that way.
 *
 * **The image is served from this instance.** `system.check_updates` ships off
 * and is documented as "outbound request; the user opts in rather than being
 * opted in". A logo fetched from the sponsor's own host on every page load is
 * that request made without asking, and it would report to a third party that
 * this deployment exists, how many people use it and how often. A 16KB local
 * copy costs the operator nothing and tells nobody anything.
 *
 * **It says "sponsor" out loud.** Paid placement that reads as a feature is a
 * dark pattern wherever it appears, and this is somebody's own admin panel.
 * Labelled, it is a fair exchange the reader can evaluate; unlabelled, it is
 * something done to them.
 */

export const SPONSOR = {
  name: 'TikHub.io',
  /** Local. Never the sponsor's host - see the note above. */
  logo: '/sponsors/tikhub.jpeg',
} as const

/**
 * Where the campaign parameters point. Deliberately not part of `SPONSOR`:
 * every link to the sponsor has to carry them, and the way to guarantee that
 * is to leave nothing else for a call site to reach for.
 */
const HOME = 'https://www.tikhub.io/'

/**
 * The sponsor's link, tagged so they can see what this project sends them.
 *
 * They are paying for placement without being able to tell whether the
 * placement works, which makes renewal a guess on their side and leaves this
 * project's funding resting on that guess. Three standard parameters fix it,
 * and they describe the deployment rather than the person using it: no
 * identifier of the operator, the instance or the visitor crosses over, and
 * every self-hosted copy sends exactly the same string.
 *
 * `placement` is where in this console the link was, so a strip that earns
 * nothing can be told from a card that does rather than both being "the admin
 * panel".
 */
export function sponsorHref(placement: string): string {
  const url = new URL(HOME)
  url.searchParams.set('utm_source', 'douyin_tiktok_download_api')
  url.searchParams.set('utm_medium', 'referral')
  url.searchParams.set('utm_campaign', 'sponsor')
  url.searchParams.set('utm_content', placement)
  return url.toString()
}

export interface SponsorProps {
  /**
   * `full` is the card for a page body. `compact` is the strip that sits in
   * the sidebar on every page, where the whole budget is one line.
   */
  variant?: 'full' | 'compact'
  /**
   * Which placement this is, for the sponsor's own reporting. Defaults to the
   * one each variant currently has; pass it when adding a third.
   */
  placement?: string
  className?: string
}

export function Sponsor({ variant = 'full', placement, className }: SponsorProps) {
  const { t } = useTranslation('console')
  const href = sponsorHref(placement ?? (variant === 'compact' ? 'sidebar' : 'about'))

  if (variant === 'compact') {
    return (
      <a
        className={cn(styles.compact, className)}
        href={href}
        target="_blank"
        rel="noreferrer noopener sponsored"
        title={t('about.sponsor.pitch')}
      >
        <img src={SPONSOR.logo} alt="" width={22} height={22} />
        <span className={styles.compactText}>
          <span className={styles.compactLabel}>{t('about.sponsor.label')}</span>
          <span className={cn(styles.compactName, 'u-truncate')}>{SPONSOR.name}</span>
        </span>
        <ExternalIcon size={11} className={styles.compactAway} />
      </a>
    )
  }

  return (
    <a
      className={cn(styles.full, className)}
      href={href}
      target="_blank"
      rel="noreferrer noopener sponsored"
    >
      <img src={SPONSOR.logo} alt={SPONSOR.name} width={72} height={72} />
      <span className="u-stack-sm" style={{ minWidth: 0 }}>
        <span className={styles.fullLabel}>{t('about.sponsor.label')}</span>
        <strong>{SPONSOR.name}</strong>
        <span className="u-xs u-secondary">{t('about.sponsor.pitch')}</span>
      </span>
      <ExternalIcon size={13} />
    </a>
  )
}
