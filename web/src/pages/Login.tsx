import { useEffect, useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'wouter'

import {
  AlertIcon,
  Button,
  Card,
  ExternalIcon,
  Input,
  LanguageSwitcher,
  Logo,
  ThemeToggle,
} from '@/components'
import shell from '@/components/shell.module.css'
import { apiPost, isApiError, type ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { SESSION_KEY, useApiMutation, useSession } from '@/hooks'

import styles from './login.module.css'

/** Where the source lives. The same address the About page links to. */
const REPO = 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API'

interface Credentials {
  username: string
  password: string
}

/**
 * Password login, the console's only entry point.
 *
 * Every rejection reads the same. "No such user" and "wrong password" are
 * different facts, and telling them apart hands an attacker a free account
 * enumeration oracle; the only failures phrased differently are the ones the
 * user can act on - a lockout and an unreachable server.
 */
export default function Login() {
  const { t } = useTranslation(['console', 'common'])
  const [, navigate] = useLocation()
  const client = useQueryClient()
  const session = useSession()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const alertId = useId()

  const signIn = useApiMutation<unknown, Credentials>(
    (credentials) =>
      apiPost(paths.auth.login, credentials, { redirectOnUnauthenticated: false }),
    {
      onSuccess: async () => {
        setPassword('')
        await client.invalidateQueries({ queryKey: SESSION_KEY })
        navigate('/')
      },
    },
  )

  // An existing session has no business on this page; the console is one hop away.
  useEffect(() => {
    if (session.data) navigate('/')
  }, [session.data, navigate])

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (!username.trim() || !password) return
    signIn.mutate({ username: username.trim(), password })
  }

  const message = signIn.error ? failureMessage(signIn.error, t) : null

  return (
    <div className={shell.centered}>
      <div className={shell.centeredPanel}>
        <div className="u-stack">
          {/* The mark, because this is the one screen with no sidebar and the
              first one a stranger sees: a bare wordmark over a password field
              says less about what they have reached than the glyph does. */}
          <div className="u-row-between">
            <span className={styles.brand}>
              <Logo size={22} />
              <span className="u-mono u-secondary">{t('common:app.name')}</span>
            </span>
            <span className="u-row">
              <LanguageSwitcher />
              <ThemeToggle />
            </span>
          </div>

          <Card title={t('console:page.login.title')} description={t('console:login.subtitle')}>
            <form className="u-stack" onSubmit={onSubmit} noValidate>
              <Input
                label={t('console:field.username')}
                value={username}
                autoComplete="username"
                autoFocus
                required
                spellCheck={false}
                aria-invalid={message ? true : undefined}
                aria-describedby={message ? alertId : undefined}
                onChange={(event) => {
                  setUsername(event.target.value)
                }}
              />
              <Input
                label={t('console:login.password')}
                type="password"
                value={password}
                autoComplete="current-password"
                required
                aria-invalid={message ? true : undefined}
                aria-describedby={message ? alertId : undefined}
                onChange={(event) => {
                  setPassword(event.target.value)
                }}
              />

              {message ? (
                <p
                  id={alertId}
                  role="alert"
                  className="u-row"
                  style={{ alignItems: 'flex-start', color: 'var(--danger)' }}
                >
                  <AlertIcon size={13} />
                  <span>{message}</span>
                </p>
              ) : null}

              <Button
                type="submit"
                variant="primary"
                size="lg"
                block
                loading={signIn.isPending}
                disabled={!username.trim() || password.length === 0}
              >
                {t('common:action.signIn')}
              </Button>
            </form>
          </Card>

          <p className="u-xs u-muted">{t('console:login.recoveryHint')}</p>

          {/* Where this came from, from the one screen anybody can reach.
              Somebody looking at a login they did not expect should be able to
              find out what it is without having an account for it.

              Both links leave the console on purpose. The About page inside it
              needs a session - it reads /system/status, which is not public -
              so linking it here would land a signed-out reader back on this
              screen, which is the one place they already were. */}
          <div className={styles.footer}>
            <a href={REPO} target="_blank" rel="noreferrer noopener">
              <ExternalIcon size={11} />
              {t('console:login.source')}
            </a>
            <a href={`${REPO}/blob/main/LICENSE`} target="_blank" rel="noreferrer noopener">
              <ExternalIcon size={11} />
              {t('console:about.link.license')}
            </a>
            <span className="u-muted">
              {t('console:about.copyright', { year: new Date().getFullYear() })}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

type Translate = (key: string, options?: Record<string, unknown>) => string

/**
 * One message for every credential rejection, whatever the server said.
 * Rate limiting and transport failures are separated out because the user's
 * next action differs: wait, or check that the API is up.
 */
function failureMessage(error: ApiError, t: Translate): string {
  if (!isApiError(error)) return t('console:login.error.generic')

  if (error.kind === 'network') return t('console:login.error.network')
  if (error.kind === 'timeout') return t('console:login.error.timeout')

  switch (error.code) {
    case 'UNAUTHENTICATED':
    case 'NOT_FOUND':
    case 'INVALID_PARAM':
      return t('console:login.error.invalid')
    case 'RATE_LIMITED':
      return t('console:login.error.rateLimited', {
        seconds: Math.max(1, Math.round(error.retryAfter ?? 60)),
      })
    default:
      return t('console:login.error.generic')
  }
}
