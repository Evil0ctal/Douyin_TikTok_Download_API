import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertIcon,
  Banner,
  Button,
  Card,
  CopyableId,
  Skeleton,
  StatusBadge,
  useToast,
} from '@/components'
import { useApiMutation, useCopy, useFormatters, useInvalidate } from '@/hooks'
import { apiGet, apiPost, waitForTask } from '@/lib/api'
import { cn } from '@/lib/cn'
import { paths } from '@/lib/endpoints'

import styles from '../identities.module.css'
import { Fact, usePlatformName } from './shared'
import {
  IDENTITIES_KEY,
  type CookieInventory,
  type IdentityRow,
  type RevealedJar,
  type SessionCheck,
} from './types'

/**
 * The masked jar, and a way past the mask.
 *
 * Masked by default because the drawer opens on every click and a screenshot
 * of an admin panel should not be a credential. Revealed on purpose, through a
 * route of its own that writes an audit line and demands the operator role -
 * so the disclosure is a decision somebody made and a record somebody can
 * read, rather than the shell-and-hand-rolled-decrypt it replaces.
 *
 * Both copy shapes are here because both are what people actually do next: the
 * header goes into curl, the JSON goes into another tool. Keyed by identity at
 * the call site, so a jar revealed for one is never shown under another's id.
 */
function RevealRow({ identity, canManage }: { identity: IdentityRow; canManage: boolean }) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const { copy } = useCopy()
  const [jar, setJar] = useState<RevealedJar | null>(null)

  const reveal = useApiMutation<RevealedJar, void>(
    () => apiGet<RevealedJar>(paths.identities.reveal(identity.id)),
    {
      onSuccess: (result) => {
        setJar(result)
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.contents.revealFailed'))
      },
    },
  )

  if (!jar) {
    return (
      <div className="u-stack-sm">
        {/* Two sentences, because the page says two different things to two
            roles. It used to tell everybody the full values were never sent
            here, directly above the button that sends them. */}
        <p className="u-xs u-muted" style={{ margin: 0 }}>
          {canManage
            ? t('console:identity.contents.maskNote')
            : t('console:identity.contents.maskNoteReadOnly')}
        </p>
        {canManage ? (
          <Button
            size="sm"
            variant="secondary"
            loading={reveal.isPending}
            onClick={() => {
              reveal.mutate()
            }}
          >
            {t('console:identity.contents.reveal')}
          </Button>
        ) : null}
      </div>
    )
  }

  return (
    <div className="u-stack-sm">
      <Banner tone="caution" icon={<AlertIcon size={14} />}>
        {t('console:identity.contents.revealed')}
      </Banner>
      <ul className={styles.cookieList}>
        {Object.entries(jar.cookies).map(([name, value]) => (
          <li key={name} className={styles.cookieRow}>
            <span className={styles.cookieHead}>
              <span className="u-mono">{name}</span>
            </span>
            <CopyableId value={value} wrap />
          </li>
        ))}
      </ul>
      <div className="u-row u-wrap">
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            void copy(jar.header)
            toast.success(t('console:identity.contents.copiedHeader'))
          }}
        >
          {t('console:identity.contents.copyHeader')}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            void copy(JSON.stringify(jar.cookies, null, 2))
            toast.success(t('console:identity.contents.copiedJson'))
          }}
        >
          {t('console:identity.contents.copyJson')}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setJar(null)
          }}
        >
          {t('console:identity.contents.hide')}
        </Button>
      </div>
    </div>
  )
}

/**
 * Ask the platform whether this login is still a login.
 *
 * `authenticated` on an identity says a session cookie was in the paste, which
 * is a fact about the paste. Whether the platform still honours it is a
 * different fact, and the only symptom of the two diverging was logged-in-only
 * data quietly missing from otherwise successful responses.
 *
 * Offered for guest identities too, and it answers "guest" for them. Hiding
 * the button there would mean the one case where somebody imported a login and
 * it silently did not take is also the case with nothing to press.
 *
 * Run on its own right after an import, which is when the answer is worth the
 * most: a login that did not take is far easier to re-copy from the browser it
 * came from while that browser is still open. Keyed by identity at the call
 * site, so one identity's answer is never shown on another.
 */
function SessionCheckButton({
  identity,
  autoRun,
  onAutoRan,
}: {
  identity: IdentityRow
  autoRun: boolean
  onAutoRan: () => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const invalidate = useInvalidate()
  const [result, setResult] = useState<SessionCheck | null>(null)

  const check = useApiMutation<SessionCheck, void>(
    async () => {
      const { task_id } = await apiPost<{ task_id: string }>(
        paths.identities.session(identity.id),
        {},
        { awaitTask: false },
      )
      return await waitForTask<SessionCheck>(task_id)
    },
    {
      onSuccess: async (answer) => {
        setResult(answer)
        // The check stores the account it named; the drawer shows it beside
        // the note, and should not wait for the next poll to.
        if (answer.account_id) await invalidate(IDENTITIES_KEY)
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.session.checkFailed'))
      },
    },
  )

  // A latch rather than the parent clearing the flag alone: StrictMode runs
  // this effect twice in development, and each run was a request to the
  // platform as this account.
  const ran = useRef(false)
  const { mutate } = check
  useEffect(() => {
    if (!autoRun || ran.current) return
    ran.current = true
    onAutoRan()
    mutate()
  }, [autoRun, onAutoRan, mutate])

  // In the card's body, not its header: the answer can be a three-line
  // sentence, and the header's action slot does not shrink, so it pushed the
  // title out of a drawer that then scrolled sideways.
  return (
    <div className="u-stack-sm" style={{ alignItems: 'flex-start' }}>
      <Button
        size="sm"
        variant="secondary"
        loading={check.isPending}
        onClick={() => {
          check.mutate()
        }}
      >
        {t('console:identity.session.check')}
      </Button>
      {result ? (
        <span className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
          <StatusBadge
            kind="health"
            value={result.logged_in ? 'healthy' : 'unhealthy'}
            flash={false}
          />
          <span className="u-xs u-secondary">
            {t(`console:identity.session.reason.${result.reason}`, {
              defaultValue: result.reason,
            })}
          </span>
          {result.account_id ? <CopyableId value={result.account_id} /> : null}
        </span>
      ) : null}
    </div>
  )
}

/**
 * What this identity is and what it is made of.
 *
 * The drawer used to open on a request history, which answers "what has this
 * been doing" and never "what is this". When an identity stops working the
 * second question is the one people have, and answering it meant opening a
 * shell and decrypting the column by hand.
 *
 * Values are masked here. What is shown is what makes a jar diagnosable: which
 * cookies are in it, which of them this build needs, how long each value is -
 * a truncated token and a wrong token look identical from outside, and the
 * length is what tells them apart - and the first and last four characters,
 * which is enough to say whether two identities hold the same jar.
 */
export function IdentityContents({
  identity,
  jar,
  loading,
  canManage,
  autoCheck,
  onAutoChecked,
}: {
  identity: IdentityRow
  jar: CookieInventory | undefined
  loading: boolean
  canManage: boolean
  autoCheck: boolean
  onAutoChecked: () => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const platformName = usePlatformName()

  const fingerprint = identity.fingerprint ?? {}
  const browser = [fingerprint.browser_family, fingerprint.browser_major]
    .filter(Boolean)
    .join(' ')

  return (
    <Card
      title={t('console:identity.contents.title')}
      description={t('console:identity.contents.hint')}
    >
      <div className="u-stack">
        <div className="u-row u-wrap">
          <Fact label={t('console:field.platform')} value={platformName(identity.platform)} />
          <Fact
            label={t('console:field.source')}
            value={t(`console:identity.source.${identity.source}`, {
              defaultValue: identity.source,
            })}
          />
          <Fact
            label={t('console:field.authenticated')}
            value={identity.authenticated ? t('common:value.yes') : t('common:value.no')}
          />
          <Fact
            label={t('console:identity.column.minted')}
            value={format.relative(identity.minted_at)}
          />
          {browser ? (
            <Fact label={t('console:identity.contents.browser')} value={browser} mono />
          ) : null}
          {fingerprint.timezone ? (
            <Fact
              label={t('console:identity.contents.timezone')}
              value={String(fingerprint.timezone)}
              mono
            />
          ) : null}
        </div>

        {canManage && identity.state !== 'retired' ? (
          <SessionCheckButton
            key={identity.id}
            identity={identity}
            autoRun={autoCheck}
            onAutoRan={onAutoChecked}
          />
        ) : null}

        {loading ? (
          <Skeleton height={18} />
        ) : !jar ? (
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('console:identity.contents.unavailable')}
          </p>
        ) : !jar.readable ? (
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            {t('console:identity.contents.unreadable')}
          </Banner>
        ) : jar.cookies.length === 0 ? (
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {jar.retired
              ? t('console:identity.contents.wiped')
              : t('console:identity.contents.empty')}
          </p>
        ) : (
          <>
            {jar.missing_required.length > 0 ? (
              <Banner tone="caution" icon={<AlertIcon size={14} />}>
                {t('console:identity.contents.missing', {
                  names: jar.missing_required.join(', '),
                })}
              </Banner>
            ) : null}

            <ul className={styles.cookieList}>
              {jar.cookies.map((cookie) => (
                <li key={cookie.name} className={styles.cookieRow}>
                  <span className={styles.cookieHead}>
                    <span className="u-mono">{cookie.name}</span>
                    <span className={cn(styles.cookieRole, styles[`role_${cookie.role}`])}>
                      {t(`console:identity.cookieRole.${cookie.role}`)}
                    </span>
                    <span className="u-xs u-muted u-mono">
                      {t('console:identity.contents.chars', { count: cookie.length })}
                    </span>
                  </span>
                  {/* What each one is for. Keyed by cookie name with the role's
                      own sentence as the fallback, because the platforms add
                      and retire these constantly and a name this build has
                      never heard of still deserves an answer. */}
                  <span className={styles.cookieAbout}>
                    {t(`console:identity.cookieAbout.${cookie.name}`, {
                      defaultValue: t(`console:identity.cookieRoleAbout.${cookie.role}`),
                    })}
                  </span>
                  <span className="u-mono u-xs u-muted">{cookie.masked}</span>
                </li>
              ))}
            </ul>

            <RevealRow key={identity.id} identity={identity} canManage={canManage} />
          </>
        )}
      </div>
    </Card>
  )
}
