import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, SlidersIcon } from '@/components'

import styles from '../identities.module.css'

/** A form control keeps its own width in a wrapping toolbar rather than collapsing. */
export function FilterSlot({ children, width = '160px' }: { children: ReactNode; width?: string }) {
  return <div style={{ width, flex: '0 0 auto', maxWidth: '100%' }}>{children}</div>
}

interface SuppressionNoticeProps {
  retired: number
  deadSession: number
  showAll: boolean
  onToggle: () => void
}

/**
 * What the default view is holding back, in numbers, with the way back.
 *
 * A table that silently omits rows is how an operator concludes their
 * identities have vanished, so this says how many and on what grounds rather
 * than leaving the count to be inferred from a row total. It sits above the
 * table rather than in the toolbar because it is a sentence, and a wrapping row
 * of filters is the wrong place to explain why a row is missing.
 */
export function SuppressionNotice({ retired, deadSession, showAll, onToggle }: SuppressionNoticeProps) {
  const { t } = useTranslation(['console', 'common'])
  const hidden = retired + deadSession

  return (
    <div className={styles.notice}>
      <span className={styles.noticeIcon} aria-hidden="true">
        <SlidersIcon size={13} />
      </span>
      <div className="u-stack-sm u-grow">
        <span className="u-row u-wrap">
          <strong>
            {showAll
              ? t('console:identity.hidden.showingAll')
              : t('console:identity.hidden.title', { count: hidden })}
          </strong>
          {retired > 0 ? (
            <span className={styles.reason}>
              {t('console:identity.hidden.retired', { count: retired })}
            </span>
          ) : null}
          {deadSession > 0 ? (
            <span className={styles.reason}>
              {t('console:identity.hidden.deadSession', { count: deadSession })}
            </span>
          ) : null}
        </span>
        <p className="u-xs u-secondary">{t('console:identity.hidden.why')}</p>
      </div>
      <Button size="sm" variant="secondary" onClick={onToggle}>
        {showAll
          ? t('console:identity.hidden.hideUnusable')
          : t('console:identity.hidden.showAll')}
      </Button>
    </div>
  )
}
