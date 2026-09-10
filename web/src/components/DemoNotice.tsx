import { useTranslation } from 'react-i18next'

import { useSession } from '@/hooks'

import { ExternalIcon, InfoIcon } from './Icons'
import styles from './shell.module.css'

/**
 * "You are looking at a demo", on every console page.
 *
 * Shown by role rather than by asking the server whether a demo is running, and
 * the difference matters: an operator signed into their own instance while the
 * demo happens to be on is not on a demo, and telling them so would be wrong.
 * The people this is for are the ones holding the published account, so the
 * condition is exactly "this session is the demo account".
 *
 * It renders nothing at all for everybody else, which is every deployment that
 * never turns the feature on.
 */

const REPO = 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API'

export function DemoNotice() {
  const { t } = useTranslation('console')
  const session = useSession()

  if (session.data?.role !== 'demo') return null

  return (
    <div className={styles.demoBar} role="note">
      <InfoIcon size={14} />
      <span>
        <strong>{t('demo.banner.title')}</strong> {t('demo.banner.body')}
      </span>
      <a href={REPO} target="_blank" rel="noreferrer noopener">
        <ExternalIcon size={12} />
        {t('demo.banner.deploy')}
      </a>
    </div>
  )
}
