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
  href: 'https://www.tikhub.io/',
  /** Local. Never the sponsor's host - see the note above. */
  logo: '/sponsors/tikhub.jpeg',
} as const

export interface SponsorProps {
  /**
   * `full` is the card for a page body. `compact` is the strip that sits in
   * the sidebar on every page, where the whole budget is one line.
   */
  variant?: 'full' | 'compact'
  className?: string
}

export function Sponsor({ variant = 'full', className }: SponsorProps) {
  const { t } = useTranslation('console')

  if (variant === 'compact') {
    return (
      <a
        className={cn(styles.compact, className)}
        href={SPONSOR.href}
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
      href={SPONSOR.href}
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
