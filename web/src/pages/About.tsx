import { Fragment, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertIcon,
  Banner,
  Card,
  CopyableId,
  ExternalIcon,
  InfoIcon,
  Logo,
  PageHeader,
  Sponsor,
} from '@/components'
import { useApiQuery, useLocalStorageState } from '@/hooks'
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

/**
 * Where a donation can be sent, by network.
 *
 * The network is not decoration: these are different chains, and an address
 * pasted into the wrong one loses the money with nobody able to return it. So
 * each row leads with the chain rather than with the address, and the note
 * under the list says it plainly.
 *
 * Ethereum and BNB Smart Chain share one address because both are EVM chains
 * and the same key controls it. That looks like a mistake unless it is said, so
 * it is said.
 */
interface Wallet {
  /** Chain name as a wallet's own network picker spells it. */
  network: string
  address: string
}

const WALLETS: readonly Wallet[] = [
  { network: 'Solana', address: 'HvtkxmDERbNXfCoojpdFAYN5mSWowjpXgedsG9eF7y9z' },
  { network: 'Tron (TRC20)', address: 'TQwSM2vjcnrdRU7gY7KNp2tCgMnK33azkT' },
  { network: 'Ethereum (ERC20)', address: '0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad' },
  { network: 'BNB Smart Chain (BEP20)', address: '0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad' },
  { network: 'Bitcoin', address: 'bc1q785j55cxlnjqe8lkwy8cq57t8t9vn3ak9tlsfy' },
]
const AUTHOR_EMAIL = 'Evil0ctal1985@gmail.com'
const AUTHOR_GITHUB = 'https://github.com/Evil0ctal'

interface SystemStatus {
  version: string
  commit?: string | null
}

/**
 * Shields for the repository, shown only once somebody has gone looking.
 *
 * They are images from shields.io, which means loading one tells shields.io
 * that a browser somewhere opened this page. Every other screen in this
 * console makes no outbound request at all, and the README says so about the
 * update check - "the server never sends anything outward, so it does not tell
 * anyone this instance exists". A row of badges on a page everybody opens would
 * have quietly made that untrue.
 *
 * So they are behind the easter egg. Open About and nothing is fetched; tap the
 * version five times and you have asked.
 */
const SHIELDS: readonly { id: string; src: string }[] = [
  { id: 'stars', src: 'https://img.shields.io/github/stars/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square&logo=github&label=stars' },
  { id: 'forks', src: 'https://img.shields.io/github/forks/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square&logo=github&label=forks' },
  { id: 'commit', src: 'https://img.shields.io/github/last-commit/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square&label=last%20commit' },
  { id: 'release', src: 'https://img.shields.io/github/v/release/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square&label=release' },
  { id: 'pulls', src: 'https://img.shields.io/docker/pulls/evil0ctal/douyin_tiktok_download_api?style=flat-square&logo=docker&label=docker%20pulls' },
  { id: 'licence', src: 'https://img.shields.io/github/license/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square&label=licence' },
]

/** How many taps on the version it takes. Five: enough not to happen by
 *  accident, few enough that somebody who suspects there is something here
 *  finds it on the first try. */
const TAPS_TO_UNLOCK = 5

/**
 * The mark, as it appears at the top of the signing modules.
 *
 * Written as a string rather than as JSX so the eslint rule that bans literal
 * text in markup does not have to decide whether a cat is prose.
 */
const CAT = [
  '　　　　 　　  ＿＿',
  '　　　 　　 ／＞　　フ',
  '　　　 　　| 　_　 _ l',
  '　 　　 　／` ミ＿xノ',
  '　　 　 /　　　 　 |',
  '　　　 /　 ヽ　　 ﾉ',
  '　 　 │　　|　|　|',
  '　／￣|　　 |　|　|',
  '　| (￣ヽ＿_ヽ_)__)',
  '　＼二つ',
].join('\n')

export default function About() {
  const { t } = useTranslation(['console', 'common'])
  const [unlocked, setUnlocked] = useLocalStorageState('about.egg', false)
  // A ref, not state: the count is never rendered, and state here was a bug.
  // React batches the clicks of somebody tapping quickly - which is exactly
  // what a person does when they suspect there is something to find - so every
  // tap in the burst read the same stale zero and the fifth never arrived.
  const taps = useRef(0)

  const system = useApiQuery<SystemStatus>({
    key: ['system', 'status'],
    path: paths.system.status,
  })

  const tapVersion = (): void => {
    if (unlocked) return
    taps.current += 1
    if (taps.current >= TAPS_TO_UNLOCK) setUnlocked(true)
  }

  return (
    <div className="u-page">
      <PageHeader
        title={t('console:about.title')}
        description={t('console:about.description')}
        badge={
          system.data?.version ? (
            <button
              type="button"
              className={styles.version}
              onClick={tapVersion}
              title={unlocked ? undefined : t('console:about.egg.hint')}
            >
              <span className="u-mono u-xs u-muted">v{system.data.version}</span>
            </button>
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

        {unlocked ? (
          <div className={styles.egg}>
            <pre className={styles.cat} aria-hidden="true">
              {CAT}
            </pre>
            <div className="u-stack-sm" style={{ minWidth: 0 }}>
              <p className="u-xs u-secondary" style={{ margin: 0 }}>
                {t('console:about.egg.found')}
              </p>
              <div className={styles.shields}>
                {SHIELDS.map((shield) => (
                  <a
                    key={shield.id}
                    href={REPO}
                    target="_blank"
                    rel="noreferrer noopener"
                    aria-label={t(`console:about.egg.shield.${shield.id}`)}
                  >
                    <img src={shield.src} alt={t(`console:about.egg.shield.${shield.id}`)} />
                  </a>
                ))}
              </div>
              <p className="u-xs u-muted" style={{ margin: 0 }}>
                {t('console:about.egg.privacy')}
              </p>
            </div>
          </div>
        ) : null}
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

      <Card title={t('console:about.donate.title')} description={t('console:about.donate.body')}>
        <div className="u-stack">
          <p className="u-secondary" style={{ margin: 0 }}>
            {t('console:about.donate.tokens')}
          </p>

          <dl className={styles.wallets}>
            {WALLETS.map((wallet) => (
              <Fragment key={wallet.network}>
                <dt>{wallet.network}</dt>
                <dd>
                  {/* Whole and copyable. A truncated wallet address is worse
                      than none: the only thing anybody does with one is paste
                      it, and a prefix cannot be pasted. */}
                  <CopyableId value={wallet.address} label={wallet.network} wrap />
                </dd>
              </Fragment>
            ))}
          </dl>

          {/* Last, and as a warning rather than a footnote: sending on the
              wrong chain is the one mistake here that cannot be undone. */}
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            <div className="u-stack-sm">
              <span>{t('console:about.donate.network')}</span>
              <span className="u-xs u-muted">{t('console:about.donate.evm')}</span>
            </div>
          </Banner>
        </div>
      </Card>
    </div>
  )
}
