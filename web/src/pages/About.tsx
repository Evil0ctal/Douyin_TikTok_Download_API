import { useTranslation } from 'react-i18next'

import { Banner, Card, ExternalIcon, InfoIcon, Logo, PageHeader, Sponsor } from '@/components'
import { useApiQuery } from '@/hooks'
import { paths } from '@/lib/endpoints'

import styles from './About.module.css'

/**
 * What this is, who made it, and what you may do with it.
 *
 * The licence section says what the LICENSE file in this repository actually
 * says. That is worth stating outright because the temptation is the other
 * way: this project is given away, so it feels like it should come with
 * conditions on who profits. Apache-2.0 does not have those - the grant is
 * irrevocable and explicitly covers selling - and a console that claimed
 * otherwise would be contradicting a file shipped three directories away.
 *
 * The author's preference about commercial use is here too, and separately,
 * labelled as what it is. A request you are free to decline is a different
 * thing from a term you agreed to, and running them together would mislead in
 * both directions: it would overstate the licence and understate the ask.
 */

const REPO = 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API'

const LINKS = [
  { id: 'repo', href: REPO },
  { id: 'issues', href: `${REPO}/issues` },
  { id: 'license', href: `${REPO}/blob/main/LICENSE` },
] as const

/**
 * How to reach the author, and what each route is for.
 *
 * Two of them, in the order they should be tried. An issue is public, keeps its
 * own history and can be answered by anybody who has hit the same thing; email
 * reaches one person's inbox. Saying which is which here is worth more than
 * listing both and leaving the reader to guess - most of what arrives by email
 * would have been answered faster in the tracker.
 *
 * The address is spelled out rather than hidden behind a mailto: label. It is
 * already public on the repository, and a reader who wants to copy it should
 * not have to hover a link to find out what they are copying.
 */
/** A username and an address. Identifiers, so neither is translated. */
const AUTHOR_NAME = 'Evil0ctal'
const AUTHOR_EMAIL = 'Evil0ctal1985@gmail.com'
const AUTHOR_GITHUB = 'https://github.com/Evil0ctal'

interface SystemStatus {
  version: string
  commit?: string | null
}

export default function About() {
  const { t } = useTranslation(['console', 'common'])

  const system = useApiQuery<SystemStatus>({
    key: ['system', 'status'],
    path: paths.system.status,
  })

  return (
    <div className="u-page">
      <PageHeader
        title={t('console:about.title')}
        description={t('console:about.description')}
        badge={
          system.data?.version ? (
            <span className="u-mono u-xs u-muted">v{system.data.version}</span>
          ) : null
        }
      />

      <Card>
        <div className={styles.identity}>
          <span className={styles.mark}>
            <Logo size={44} />
          </span>
          <div className="u-stack-sm" style={{ minWidth: 0 }}>
            <h2 className={styles.projectName}>Douyin_TikTok_Download_API</h2>
            <p className="u-secondary" style={{ margin: 0 }}>
              {t('console:about.tagline')}
            </p>
            <p className="u-xs u-muted" style={{ margin: 0 }}>
              {t('console:about.copyright', { year: new Date().getFullYear() })}
            </p>
          </div>
        </div>

        <div className={styles.links}>
          {LINKS.map((link) => (
            <a key={link.id} href={link.href} target="_blank" rel="noreferrer noopener">
              <ExternalIcon size={12} />
              {t(`console:about.link.${link.id}`)}
            </a>
          ))}
        </div>
      </Card>

      <Card title={t('console:about.contact.title')} description={t('console:about.contact.body')}>
        <dl className={styles.contact}>
          <dt>{t('console:about.contact.issuesLabel')}</dt>
          <dd>
            <a href={`${REPO}/issues`} target="_blank" rel="noreferrer noopener">
              <ExternalIcon size={12} />
              {t('console:about.contact.issuesAction')}
            </a>
            <span className="u-xs u-muted">{t('console:about.contact.issuesWhy')}</span>
          </dd>

          <dt>{t('console:about.contact.emailLabel')}</dt>
          <dd>
            {/* A real address, copyable as text. `mailto:` is the convenience,
                not the answer. */}
            <a className="u-mono" href={`mailto:${AUTHOR_EMAIL}`}>
              {AUTHOR_EMAIL}
            </a>
            <span className="u-xs u-muted">{t('console:about.contact.emailWhy')}</span>
          </dd>

          <dt>{t('console:about.contact.authorLabel')}</dt>
          <dd>
            <a href={AUTHOR_GITHUB} target="_blank" rel="noreferrer noopener">
              <ExternalIcon size={12} />
              {AUTHOR_NAME}
            </a>
          </dd>
        </dl>
      </Card>

      <Card title={t('console:about.licence.title')}>
        <div className="u-stack">
          <p className="u-secondary" style={{ margin: 0 }}>
            {t('console:about.licence.body')}
          </p>
          <ul className={styles.terms}>
            <li>{t('console:about.licence.notice')}</li>
            <li>{t('console:about.licence.changes')}</li>
            <li>{t('console:about.licence.warranty')}</li>
          </ul>
        </div>
      </Card>

      <Card title={t('console:about.request.title')}>
        {/* Marked as a request, not a term. Doing otherwise would overstate
            the licence and understate the ask at the same time. */}
        <Banner tone="accent" icon={<InfoIcon size={14} />}>
          <div className="u-stack-sm">
            <span>{t('console:about.request.body')}</span>
            <span className="u-xs u-muted">{t('console:about.request.note')}</span>
          </div>
        </Banner>
      </Card>

      <Card title={t('console:about.responsibility.title')}>
        <p className="u-secondary" style={{ margin: 0 }}>
          {t('console:about.responsibility.body')}
        </p>
      </Card>

      <Card title={t('console:about.sponsor.title')} description={t('console:about.sponsor.body')}>
        <Sponsor />
        <p className="u-xs u-muted" style={{ margin: 'var(--space-3) 0 0' }}>
          {t('console:about.sponsor.become')}
        </p>
      </Card>
    </div>
  )
}
