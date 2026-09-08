import { useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation } from 'wouter'

import {
  AlertIcon,
  Button,
  Card,
  CheckIcon,
  CodeBlock,
  CopyableId,
  ErrorState,
  Input,
  LanguageSwitcher,
  PageHeader,
  Select,
  StatusBadge,
  Textarea,
  ThemeToggle,
  useToast,
} from '@/components'
import shell from '@/components/shell.module.css'
import { apiPost, apiRequest, isApiError, waitForTask } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { MISSING } from '@/lib/format'
import { PLATFORMS, type Platform } from '@/lib/types'
import {
  SESSION_KEY,
  SETUP_STATUS_KEY,
  useApiMutation,
  useApiQuery,
  useFormatters,
  useSetupStatus,
} from '@/hooks'

import { ProxyPreviewList, parseProxyLines } from './Proxies'

/* -------------------------------------------------------------------------- */
/* Types                                                                       */
/* -------------------------------------------------------------------------- */

const STEPS = ['admin', 'proxy', 'mint', 'smoke'] as const
type StepId = (typeof STEPS)[number]

/** Mirrors USERNAME_PATTERN in src/dtk/api/routes/schemas.py. */
const USERNAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/

/** The wizard asks for more than the server's floor; the copy promises twelve. */
const MIN_PASSWORD_LENGTH = 12

interface ProxyImportReport {
  created: Array<{ id: string; url_masked: string | null; label?: string | null }>
  rejected: Array<{ line: string; error: string }>
  counts: { created: number; rejected: number }
}

interface ProxyProbeResult {
  ok?: boolean
  latency_ms?: number | null
  exit_ip?: string | null
  country?: string | null
  detail?: string | null
}

interface ParsedContent {
  platform?: string
  content_id?: string
  kind?: string
  title?: string
  web_url?: string
  author?: { nickname?: string | null; unique_id?: string | null }
}

/* -------------------------------------------------------------------------- */
/* Page                                                                        */
/* -------------------------------------------------------------------------- */

/**
 * First-run wizard.
 *
 * Four steps, and the fourth is the point: a real end-to-end parse of a live
 * link. A deployment that only proves it can create an account teaches the user
 * nothing about whether the proxy, the identity pool and the signer actually
 * work - they find that out at their first real API call, which is the worst
 * possible moment (docs/design/07-frontend.md).
 */
export default function Setup() {
  const { t } = useTranslation(['setup', 'console', 'common'])
  const status = useSetupStatus()

  const [step, setStep] = useState<StepId>('admin')
  const [finished, setFinished] = useState(false)
  const [claimed, setClaimed] = useState(false)

  const alreadyDone = (status.data?.initialized ?? false) && !claimed

  const index = STEPS.indexOf(step)
  const goTo = (next: StepId): void => {
    setStep(next)
  }
  const advance = (): void => {
    const next = STEPS[index + 1]
    if (next) goTo(next)
    else setFinished(true)
  }

  return (
    <div className={shell.centered}>
      <div className={shell.centeredPanel} style={{ maxWidth: '760px' }}>
        <div className="u-stack-lg">
          <div className="u-row-between">
            <span className="u-mono u-secondary">{t('common:app.name')}</span>
            <span className="u-row">
              <LanguageSwitcher />
              <ThemeToggle />
            </span>
          </div>

          {alreadyDone ? (
            <AlreadyInitialized />
          ) : finished ? (
            <Finished />
          ) : (
            <>
              <PageHeader title={t('setup:title')} description={t('setup:subtitle')} />
              <Stepper current={index} />

              {step === 'admin' ? (
                <AdminStep
                  onDone={() => {
                    setClaimed(true)
                    advance()
                  }}
                />
              ) : null}
              {step === 'proxy' ? <ProxyStep onNext={advance} /> : null}
              {step === 'mint' ? <MintStep onNext={advance} /> : null}
              {step === 'smoke' ? (
                <SmokeStep
                  onNext={() => {
                    setFinished(true)
                  }}
                />
              ) : null}

              {index > 0 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    const previous = STEPS[index - 1]
                    if (previous) goTo(previous)
                  }}
                >
                  {t('common:action.back')}
                </Button>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Chrome                                                                      */
/* -------------------------------------------------------------------------- */

function Stepper({ current }: { current: number }) {
  const { t } = useTranslation(['setup', 'console'])

  return (
    <ol
      className="u-row u-wrap"
      style={{ listStyle: 'none', margin: 0, padding: 0, gap: 'var(--space-3)' }}
      aria-label={t('console:setupWizard.stepsLabel')}
    >
      {STEPS.map((id, index) => {
        const done = index < current
        const active = index === current
        return (
          <li key={id} className="u-row" aria-current={active ? 'step' : undefined}>
            <span
              aria-hidden="true"
              className="u-mono u-xs"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: '20px',
                height: '20px',
                borderRadius: 'var(--radius-full)',
                border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
                background: done ? 'var(--accent-subtle)' : 'transparent',
                color: active ? 'var(--accent)' : 'var(--text-muted)',
              }}
            >
              {done ? <CheckIcon size={11} /> : index + 1}
            </span>
            <span
              className={active ? undefined : 'u-muted'}
              style={active ? { color: 'var(--text)' } : undefined}
            >
              {t(`setup:step.${id}.title`)}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

function AlreadyInitialized() {
  const { t } = useTranslation(['setup', 'common'])
  const [, navigate] = useLocation()

  return (
    <Card
      title={t('setup:alreadyInitialized.title')}
      description={t('setup:alreadyInitialized.description')}
    >
      <Button
        variant="primary"
        onClick={() => {
          navigate('/login')
        }}
      >
        {t('setup:alreadyInitialized.action')}
      </Button>
    </Card>
  )
}

function Finished() {
  const { t } = useTranslation(['setup', 'common'])
  const [, navigate] = useLocation()

  return (
    <Card title={t('setup:done.title')} description={t('setup:done.description')}>
      <Button
        variant="primary"
        size="lg"
        onClick={() => {
          navigate('/')
        }}
      >
        {t('setup:done.action')}
      </Button>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Step 1: administrator                                                       */
/* -------------------------------------------------------------------------- */

function tokenFromUrl(): string {
  if (typeof window === 'undefined') return ''
  return new URLSearchParams(window.location.search).get('token') ?? ''
}

function AdminStep({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation(['setup', 'console', 'common'])
  const client = useQueryClient()
  const toast = useToast()

  const [prefilled] = useState(() => tokenFromUrl())
  const [token, setToken] = useState(prefilled)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [submitted, setSubmitted] = useState(false)

  const usernameError =
    submitted && !USERNAME_PATTERN.test(username) ? t('console:setupWizard.admin.usernameRule') : undefined
  const passwordError =
    submitted && password.length < MIN_PASSWORD_LENGTH
      ? t('console:setupWizard.admin.passwordRule', { length: MIN_PASSWORD_LENGTH })
      : undefined
  const confirmError = submitted && confirm !== password ? t('setup:admin.mismatch') : undefined
  const tokenError = submitted && token.trim().length < 8 ? t('console:setupWizard.admin.tokenRule') : undefined

  const create = useApiMutation<unknown, void>(
    async () => {
      await apiPost(paths.setup.init, {
        token: token.trim(),
        username: username.trim(),
        password,
      })
      // init creates the account but issues no session; the wizard's remaining
      // steps call admin routes, so it signs in with what was just entered.
      await apiPost(paths.auth.login, { username: username.trim(), password }, {
        redirectOnUnauthenticated: false,
      })
      return null
    },
    {
      onSuccess: async () => {
        setPassword('')
        setConfirm('')
        await client.invalidateQueries({ queryKey: SETUP_STATUS_KEY })
        await client.invalidateQueries({ queryKey: SESSION_KEY })
        onDone()
      },
      onError: (error) => {
        toast.apiError(error, t('console:setupWizard.admin.failed'))
      },
    },
  )

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setSubmitted(true)
    const valid =
      USERNAME_PATTERN.test(username) &&
      password.length >= MIN_PASSWORD_LENGTH &&
      confirm === password &&
      token.trim().length >= 8
    if (valid) create.mutate()
  }

  const attemptsLeft = attemptsRemaining(create.error)

  return (
    <Card title={t('setup:step.admin.title')} description={t('setup:step.admin.description')}>
      <form className="u-stack" onSubmit={onSubmit} noValidate>
        <Input
          label={t('setup:tokenBanner.label')}
          description={prefilled ? t('console:setupWizard.tokenFromUrl') : t('setup:tokenBanner.description')}
          error={tokenError}
          mono
          required
          autoComplete="off"
          spellCheck={false}
          value={token}
          onChange={(event) => {
            setToken(event.target.value)
          }}
        />
        <p className="u-xs u-muted">{t('setup:tokenBanner.hint')}</p>

        <Input
          label={t('setup:admin.username')}
          error={usernameError}
          required
          autoComplete="username"
          spellCheck={false}
          value={username}
          onChange={(event) => {
            setUsername(event.target.value)
          }}
        />
        <Input
          label={t('setup:admin.password')}
          description={t('setup:admin.passwordHint')}
          error={passwordError}
          type="password"
          required
          autoComplete="new-password"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value)
          }}
        />
        <Input
          label={t('setup:admin.confirmPassword')}
          error={confirmError}
          type="password"
          required
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => {
            setConfirm(event.target.value)
          }}
        />

        {create.error ? (
          <div className="u-stack-sm">
            <ErrorState error={create.error} compact />
            {attemptsLeft !== null ? (
              <p className="u-xs" style={{ color: 'var(--caution)' }}>
                {t('console:setupWizard.admin.attemptsLeft', { count: attemptsLeft })}
              </p>
            ) : null}
          </div>
        ) : null}

        <Button type="submit" variant="primary" size="lg" loading={create.isPending}>
          {t('setup:admin.submit')}
        </Button>
      </form>
    </Card>
  )
}

/** The server reports how many token attempts are left before it burns the token. */
function attemptsRemaining(error: unknown): number | null {
  if (!isApiError(error) || error.code !== 'SETUP_TOKEN_INVALID') return null
  const value = error.details.attempts_remaining
  return typeof value === 'number' ? value : null
}

/* -------------------------------------------------------------------------- */
/* Step 2: proxies                                                             */
/* -------------------------------------------------------------------------- */

function ProxyStep({ onNext }: { onNext: () => void }) {
  const { t } = useTranslation(['setup', 'console', 'common'])
  const format = useFormatters()
  const toast = useToast()

  const [text, setText] = useState('')
  const [report, setReport] = useState<ProxyImportReport | null>(null)
  const [probes, setProbes] = useState<Record<string, ProxyProbeResult | null>>({})
  const [probing, setProbing] = useState(false)

  const preview = useMemo(() => parseProxyLines(text), [text])

  const submit = useApiMutation<ProxyImportReport, void>(
    () => apiPost<ProxyImportReport>(paths.proxies.import, { text }),
    {
      onSuccess: async (result) => {
        setReport(result)
        setProbing(true)
        // Import and probe are one action here: a proxy list that was accepted
        // but never reached is exactly the failure this step exists to catch.
        for (const proxy of result.created) {
          try {
            const queued = await apiPost<{ task_id: string }>(paths.proxies.test(proxy.id))
            const probe = await waitForTask<ProxyProbeResult>(queued.task_id, { timeoutMs: 90_000 })
            setProbes((current) => ({ ...current, [proxy.id]: probe }))
          } catch {
            // A probe that cannot even be queued is reported as a failed probe;
            // the import itself already succeeded and must not be undone.
            setProbes((current) => ({ ...current, [proxy.id]: null }))
          }
        }
        setProbing(false)
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  return (
    <Card title={t('setup:step.proxy.title')} description={t('setup:step.proxy.description')}>
      <div className="u-stack">
        <Textarea
          label={t('setup:proxy.label')}
          description={t('setup:proxy.formats')}
          rows={7}
          value={text}
          placeholder={t('setup:proxy.placeholder')}
          onChange={(event) => {
            setText(event.target.value)
          }}
        />

        <ProxyPreviewList preview={preview} />

        {report ? (
          <div className="u-stack-sm">
            <p className="u-xs">
              {t('console:proxy.import.result', {
                created: report.counts.created,
                rejected: report.counts.rejected,
              })}
            </p>
            <ul className="u-stack-sm" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {report.created.map((proxy) => {
                const probe = probes[proxy.id]
                const known = proxy.id in probes
                return (
                  <li key={proxy.id} className="u-row-between u-nowrap">
                    <span className="u-mono u-xs u-truncate">{proxy.url_masked ?? proxy.id}</span>
                    <span className="u-row">
                      {known ? (
                        <>
                          <StatusBadge
                            kind="health"
                            value={probe?.ok ? 'healthy' : 'unhealthy'}
                            size="sm"
                            flash={false}
                          />
                          <span className="u-mono u-xs u-muted">
                            {probe?.country ?? MISSING} · {format.latency(probe?.latency_ms)}
                          </span>
                        </>
                      ) : (
                        <StatusBadge kind="task" value="running" size="sm" flash={false} />
                      )}
                    </span>
                  </li>
                )
              })}
            </ul>
            {report.rejected.length > 0 ? (
              <p className="u-xs" style={{ color: 'var(--caution)' }}>
                {t('console:proxy.import.unparsed', { count: report.rejected.length })}
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="u-row">
          <Button
            variant="primary"
            loading={submit.isPending || probing}
            disabled={preview.valid === 0}
            onClick={() => {
              submit.mutate()
            }}
          >
            {t('setup:proxy.submit')}
          </Button>
          <Button variant="ghost" onClick={onNext} disabled={probing}>
            {report ? t('common:action.next') : t('setup:proxy.skip')}
          </Button>
        </div>
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Step 3: first mint                                                          */
/* -------------------------------------------------------------------------- */

/** The half of GET /system/status this step needs. */
interface BrowserRpcStatus {
  components?: { browser_rpc?: { configured?: boolean } }
}

function MintStep({ onNext }: { onNext: () => void }) {
  const { t } = useTranslation(['setup', 'console', 'common'])
  const toast = useToast()

  // Minting drives a real browser, and the browser is an optional container -
  // docker/compose.yml keeps it behind the `browser` profile because a
  // deployment that imports its cookies by hand does not need the heaviest
  // image in the stack. That is a supported way to run this, but the step used
  // to offer Mint anyway, so the documented default configuration answered the
  // first thing a new operator does with NOT_CONFIGURED.
  const status = useApiQuery<BrowserRpcStatus>({
    key: ['setup', 'browser-rpc'],
    path: paths.system.status,
  })
  const configured = status.data?.components?.browser_rpc?.configured
  const canMint = configured !== false

  const [platform, setPlatform] = useState<Platform>('douyin')
  const [count, setCount] = useState(1)
  const [minted, setMinted] = useState(0)
  const [waiting, setWaiting] = useState(false)

  const mint = useApiMutation<{ task_ids: string[] }, void>(
    () => apiPost<{ task_ids: string[] }>(paths.identities.mint, { platform, count }),
    {
      onSuccess: async (queued) => {
        setWaiting(true)
        let done = 0
        for (const taskId of queued.task_ids) {
          try {
            await waitForTask(taskId, { timeoutMs: 180_000 })
            done += 1
            setMinted(done)
          } catch (error) {
            toast.apiError(error, t('console:identity.mint.failed'))
          }
        }
        setWaiting(false)
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.mint.failed'))
      },
    },
  )

  const busy = mint.isPending || waiting

  if (!canMint) {
    return (
      <Card title={t('setup:step.mint.title')} description={t('setup:step.mint.unavailable')}>
        <div className="u-stack">
          <CodeBlock
            language="text"
            code="docker compose -p dtk -f docker/compose.yml --profile browser up -d"
          />
          <p className="u-xs u-muted">{t('setup:mint.unavailableHint')}</p>
          <div className="u-row">
            <Button variant="primary" onClick={onNext}>
              {t('setup:mint.continueWithoutMinting')}
            </Button>
          </div>
        </div>
      </Card>
    )
  }

  return (
    <Card title={t('setup:step.mint.title')} description={t('setup:step.mint.description')}>
      <div className="u-stack">
        <div className="u-grid">
          <Select
            label={t('setup:mint.platform')}
            value={platform}
            onChange={(event) => {
              setPlatform(event.target.value as Platform)
            }}
            options={PLATFORMS.map((value) => ({ value, label: value }))}
          />
          <Input
            label={t('console:identity.mint.count')}
            type="number"
            min={1}
            max={5}
            mono
            value={count}
            onChange={(event) => {
              const next = Number(event.target.value)
              setCount(Number.isFinite(next) ? Math.min(5, Math.max(1, Math.trunc(next))) : 1)
            }}
          />
        </div>

        {busy ? <p className="u-xs u-muted">{t('setup:mint.running')}</p> : null}
        {minted > 0 && !busy ? (
          <p className="u-row" style={{ color: 'var(--success)' }}>
            <CheckIcon size={13} />
            {t('setup:mint.success')} · {t('console:identity.mint.done', { count: minted })}
          </p>
        ) : null}
        {mint.error ? <ErrorState error={mint.error} compact /> : null}

        <div className="u-row">
          <Button
            variant="primary"
            loading={busy}
            onClick={() => {
              mint.mutate()
            }}
          >
            {t('setup:mint.submit')}
          </Button>
          <Button variant="ghost" onClick={onNext} disabled={busy}>
            {minted > 0 ? t('common:action.next') : t('setup:mint.skip')}
          </Button>
        </div>
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Step 4: smoke test                                                          */
/* -------------------------------------------------------------------------- */

function SmokeStep({ onNext }: { onNext: () => void }) {
  const { t } = useTranslation(['setup', 'console', 'common'])
  const format = useFormatters()

  const [url, setUrl] = useState('')
  const [result, setResult] = useState<{
    content: ParsedContent
    requestId: string | null
    durationMs: number | null
  } | null>(null)

  const run = useApiMutation<void, void>(
    async () => {
      const response = await apiRequest<ParsedContent>(paths.parse, {
        method: 'POST',
        body: { url: url.trim() },
        // The console holds the connection briefly, then falls back to polling
        // the task - the same path any client takes (docs/design/06).
        params: { wait: 20 },
        taskTimeoutMs: 120_000,
      })
      setResult({
        content: response.data,
        requestId: response.meta.request_id ?? null,
        durationMs: response.meta.duration_ms ?? null,
      })
    },
    {
      onMutate: () => {
        setResult(null)
      },
    },
  )

  return (
    <Card title={t('setup:step.smoke.title')} description={t('setup:step.smoke.description')}>
      <div className="u-stack">
        <Input
          label={t('setup:smoke.url')}
          description={t('setup:smoke.urlHint')}
          mono
          value={url}
          placeholder="https://www.douyin.com/video/..."
          onChange={(event) => {
            setUrl(event.target.value)
          }}
        />

        {run.isPending ? <p className="u-xs u-muted">{t('setup:smoke.running')}</p> : null}

        {run.error ? (
          <div className="u-stack-sm">
            <ErrorState error={run.error} />
            <p className="u-xs u-muted">{t('setup:smoke.failed')}</p>
          </div>
        ) : null}

        {result ? (
          <div className="u-stack-sm">
            <p className="u-row" style={{ color: 'var(--success)' }}>
              <CheckIcon size={13} />
              {t('setup:smoke.success')}
            </p>
            <div className="u-row u-wrap">
              <SmokeFact label={t('console:field.platform')} value={result.content.platform ?? MISSING} mono />
              <SmokeFact
                label={t('console:setupWizard.smoke.contentId')}
                value={<CopyableId value={result.content.content_id} length={19} />}
              />
              <SmokeFact
                label={t('console:field.duration')}
                value={format.latency(result.durationMs)}
                mono
              />
              <SmokeFact
                label={t('console:field.requestId')}
                value={<CopyableId value={result.requestId} length={12} middle />}
              />
            </div>
            {result.content.title ? <p className="u-truncate">{result.content.title}</p> : null}
            <CodeBlock json={result.content} defaultCollapsed />
          </div>
        ) : null}

        <div className="u-row">
          <Button
            variant="primary"
            loading={run.isPending}
            disabled={url.trim().length === 0}
            onClick={() => {
              run.mutate()
            }}
          >
            {t('setup:smoke.submit')}
          </Button>
          <Button variant="ghost" onClick={onNext} disabled={run.isPending}>
            {result ? t('common:action.done') : t('console:setupWizard.smoke.skip')}
          </Button>
        </div>

        {run.error ? (
          <p className="u-row u-xs u-muted" style={{ alignItems: 'flex-start' }}>
            <AlertIcon size={12} />
            <span>{t('console:setupWizard.smoke.diagnoseHint')}</span>
          </p>
        ) : null}
      </div>
    </Card>
  )
}

function SmokeFact({ label, value, mono }: { label: ReactNode; value: ReactNode; mono?: boolean }) {
  return (
    <span className="u-stack-sm">
      <span className="u-xs u-muted">{label}</span>
      <span className={mono ? 'u-mono' : undefined}>{value}</span>
    </span>
  )
}
