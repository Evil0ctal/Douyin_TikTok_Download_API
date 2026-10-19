import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'wouter'

import {
  ActivityIcon,
  AlertIcon,
  Button,
  Card,
  CheckIcon,
  ClockIcon,
  CodeBlock,
  CopyIcon,
  CopyableId,
  CrossIcon,
  ErrorState,
  LockIcon,
  MinusIcon,
  PageHeader,
  Select,
  SpinnerIcon,
  Switch,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useCopy, useFormatters, useSession } from '@/hooks'
import { apiPost, errorCodeOf, type ApiErrorPayload } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { atLeast } from '@/lib/roles'
import { PLATFORMS, type Platform, type TaskState } from '@/lib/types'

/**
 * The six-step self check.
 *
 * "I deployed it but I get no data" is the first issue every self-hosted tool
 * receives, and the cause is in the proxy, the cookie jar, the signing path or
 * the network - four places a user cannot inspect. This page walks all of them
 * and hands back one block of text (docs/design/15-operations.md).
 *
 * The report is redacted at the source: proxy passwords, cookies and API keys
 * never enter it, which is what makes "paste this into the issue" safe advice.
 */

/** Step ids in the order doc 15 defines them; they are identifiers, not labels. */
const STEP_IDS = ['components', 'egress', 'proxies', 'pool', 'signing', 'smoke'] as const
type StepId = (typeof STEP_IDS)[number]

type StepStatus = 'pass' | 'warn' | 'fail' | 'skip'
type DisplayStatus = StepStatus | 'running' | 'pending' | 'idle'

interface ReportStep {
  number: number
  step: string
  status: StepStatus
  reason: string
  action?: string | null
  details?: Record<string, unknown> | null
  duration_ms?: number | null
}

interface DiagnosticReport {
  version: string
  started_at: string
  finished_at: string
  passed: boolean
  /** `pass`, `warn` or `fail`. Absent on reports stored before it existed. */
  verdict?: 'pass' | 'warn' | 'fail' | null
  steps: ReportStep[]
  /** Present when the server renders the plain-text report for us. */
  text?: string | null
}

interface TaskResponse {
  task_id: string
  state: TaskState
  created_at?: string | null
  finished_at?: string | null
  data?: DiagnosticReport | null
  error?: ApiErrorPayload | null
}

/**
 * Where the run being shown is remembered: in the address, so the report can
 * be linked, and in this tab's session storage, so leaving the page and coming
 * back through the menu - which drops the address - still finds it. It was
 * only component state, and the first navigation away threw the report out
 * while the task that produced it was still sitting on the server.
 */
const TASK_PARAM = 'task'
const TASK_STORAGE_KEY = 'dtk.diagnose.task'

function readStoredTask(): string | null {
  try {
    return window.sessionStorage.getItem(TASK_STORAGE_KEY)
  } catch {
    // Storage refused (private window, blocked site data): the address alone
    // still carries the run.
    return null
  }
}

function writeStoredTask(taskId: string | null): void {
  try {
    if (taskId) window.sessionStorage.setItem(TASK_STORAGE_KEY, taskId)
    else window.sessionStorage.removeItem(TASK_STORAGE_KEY)
  } catch {
    // As above: the address is enough on its own.
  }
}

const TONE: Record<DisplayStatus, string> = {
  pass: 'var(--success)',
  warn: 'var(--caution)',
  fail: 'var(--danger)',
  skip: 'var(--text-muted)',
  running: 'var(--accent)',
  pending: 'var(--neutral)',
  idle: 'var(--neutral)',
}

function StatusIcon({ status }: { status: DisplayStatus }) {
  switch (status) {
    case 'pass':
      return <CheckIcon size={12} />
    case 'warn':
      return <AlertIcon size={12} />
    case 'fail':
      return <CrossIcon size={12} />
    case 'skip':
      return <MinusIcon size={12} />
    case 'running':
      return <SpinnerIcon size={12} />
    default:
      return <ClockIcon size={12} />
  }
}

/** Colour, icon and word together: a screenshot in an issue keeps all three. */
function StepStatusTag({ status }: { status: DisplayStatus }) {
  const { t } = useTranslation('console')
  return (
    <span
      className="u-nowrap"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
        padding: '1px var(--space-2)',
        border: `1px solid ${TONE[status]}`,
        borderRadius: 'var(--radius-full)',
        color: TONE[status],
        fontSize: 'var(--text-xs)',
        lineHeight: 'var(--leading-xs)',
      }}
    >
      <StatusIcon status={status} />
      {t(`diagnose.status.${status}`)}
    </span>
  )
}

function Banner({ tone, icon, children }: { tone: 'accent' | 'caution'; icon: ReactNode; children: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 'var(--space-2)',
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius)',
        border: `1px solid var(--${tone === 'accent' ? 'border' : 'caution'})`,
        background: tone === 'accent' ? 'var(--accent-subtle)' : 'transparent',
        color: 'var(--text-secondary)',
        fontSize: 'var(--text-sm)',
        lineHeight: 'var(--leading-sm)',
      }}
    >
      <span style={{ color: `var(--${tone})`, flex: '0 0 auto', marginTop: '2px' }}>{icon}</span>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  )
}

/** The worst status any step reached, for reports that predate the server sending it. */
function verdictOf(report: DiagnosticReport): 'pass' | 'warn' | 'fail' {
  if (report.verdict) return report.verdict
  if (report.steps.some((step) => step.status === 'fail')) return 'fail'
  return report.steps.some((step) => step.status === 'warn') ? 'warn' : 'pass'
}

/** Fallback renderer, byte-compatible with DiagnosticReport.render_text on the server. */
function renderReport(report: DiagnosticReport): string {
  const lines = [
    `dtk diagnostics ${report.version}`,
    `started  ${report.started_at}`,
    `finished ${report.finished_at}`,
    `verdict  ${verdictOf(report).toUpperCase()}`,
    '',
  ]
  for (const step of report.steps) {
    lines.push(`[${step.status.toUpperCase().padEnd(4)}] ${step.number}. ${step.step}`)
    lines.push(`       ${step.reason}`)
    if (step.action) lines.push(`       action: ${step.action}`)
    for (const [key, value] of Object.entries(step.details ?? {})) {
      lines.push(`       ${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
    }
    lines.push('')
  }
  return `${lines.join('\n').trimEnd()}\n`
}

interface StepViewProps {
  id: StepId
  index: number
  status: DisplayStatus
  result: ReportStep | undefined
}

function StepView({ id, index, status, result }: StepViewProps) {
  const { t } = useTranslation('console')
  const formatters = useFormatters()

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 'var(--space-3)',
        padding: 'var(--space-3) 0',
        borderTop: index === 0 ? undefined : '1px solid var(--border-subtle)',
      }}
    >
      <div style={{ flex: '1 1 260px', minWidth: 0 }} className="u-stack-sm">
        <div className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
          <span className="u-mono u-muted u-xs">{index + 1}</span>
          <span className="u-mono" style={{ color: 'var(--text)' }}>
            {id}
          </span>
          <StepStatusTag status={status} />
          {result?.duration_ms ? (
            <span className="u-xs u-muted u-mono">{formatters.latency(result.duration_ms)}</span>
          ) : null}
        </div>
        <p className="u-xs u-secondary" style={{ margin: 0 }}>
          {t(`diagnose.step.${id}`)}
        </p>
      </div>

      <div style={{ flex: '2 1 320px', minWidth: 0 }} className="u-stack-sm">
        {result ? (
          <>
            <p style={{ margin: 0, fontSize: 'var(--text-sm)', lineHeight: 'var(--leading-sm)' }}>
              {result.reason}
            </p>
            {result.action ? (
              <p className="u-xs" style={{ margin: 0, color: 'var(--caution)' }}>
                {t('diagnose.suggestedAction')} {result.action}
              </p>
            ) : null}
            {result.details && Object.keys(result.details).length > 0 ? (
              <div className="u-xs u-mono u-muted u-scroll-x">
                {Object.entries(result.details).map(([key, value]) => (
                  <div key={key} className="u-nowrap">
                    {key}: {typeof value === 'string' ? value : JSON.stringify(value)}
                  </div>
                ))}
              </div>
            ) : null}
          </>
        ) : (
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {status === 'running' ? t('diagnose.stepRunning') : t('diagnose.stepPending')}
          </p>
        )}
      </div>
    </div>
  )
}

export default function Diagnose() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const { copied, copy } = useCopy()

  const session = useSession()
  // POST /admin/diagnose is manage_pool: an operator or better. A viewer
  // reaches this page from the menu, so it says why there is no Run button
  // instead of offering one that answers 403.
  const canRun = atLeast(session.data?.role ?? null, 'operator')

  const [includeSmoke, setIncludeSmoke] = useState(true)
  // Empty is "let the server choose": the platform with the most identities
  // in rotation, which on a Bilibili-only box is Bilibili rather than a Douyin
  // link it has nothing to fetch with.
  const [platform, setPlatform] = useState<Platform | ''>('')
  const [searchParams, setSearchParams] = useSearchParams()
  const [storedTask, setStoredTask] = useState<string | null>(readStoredTask)
  const taskId = searchParams.get(TASK_PARAM) || storedTask

  const remember = (id: string): void => {
    writeStoredTask(id)
    setStoredTask(id)
    const next = new URLSearchParams(searchParams)
    next.set(TASK_PARAM, id)
    setSearchParams(next, { replace: true })
  }

  const start = useApiMutation<{ task_id: string }, void>(
    () =>
      apiPost<{ task_id: string }>(
        paths.diagnose,
        {
          include_smoke_test: includeSmoke,
          ...(includeSmoke && platform ? { platform } : {}),
        },
        { awaitTask: false },
      ),
    {
      onSuccess: (accepted) => {
        remember(accepted.task_id)
      },
      onError: (error) => {
        toast.apiError(error, t('diagnose.toast.startFailed'))
      },
    },
  )

  const run = useApiQuery<TaskResponse>({
    key: ['diagnose', 'task', taskId],
    path: taskId ? paths.tasks.byId(taskId) : paths.diagnose,
    // Reading a diagnose task back takes the role that starts one (role_for_task
    // in src/dtk/api/routes/operations.py), so below it a remembered id is not
    // asked for at all rather than answered with a 403 every five seconds.
    enabled: taskId !== null && canRun,
    // An expired result is gone for good; retrying it only repeats the answer.
    retry: (failures, error) => errorCodeOf(error) !== 'TASK_NOT_FOUND' && failures < 2,
    // Foreground work: poll at the fast cadence until the task settles, then
    // stop - and stop on an error too. A remembered id outlives its result, and
    // an errored query has no state to settle on, so it polled forever.
    poll: POLL.fast,
    refetchInterval: (query) => {
      if (query.state.status === 'error') return false
      const state = query.state.data?.state
      return state === 'done' || state === 'failed' ? false : POLL.fast
    },
  })

  const expired = run.isError && errorCodeOf(run.error) === 'TASK_NOT_FOUND'
  useEffect(() => {
    // Forget it, so the next visit through the menu does not ask again. The
    // address keeps it until the next run replaces it, which is harmless: the
    // banner below is all it produces.
    if (expired) writeStoredTask(null)
  }, [expired])

  const state: TaskState | null = run.data?.state ?? null
  const report = run.data?.data ?? null
  const running = taskId !== null && (state === 'queued' || state === 'running' || run.isLoading)

  const resultsById = useMemo(() => {
    const map = new Map<string, ReportStep>()
    for (const step of report?.steps ?? []) map.set(step.step, step)
    return map
  }, [report])

  const displayStatus = (id: StepId, index: number): DisplayStatus => {
    const result = resultsById.get(id)
    if (result) return result.status
    if (!running) return 'idle'
    // The first step without a result is the one currently under way.
    const reported = STEP_IDS.filter((step) => resultsById.has(step)).length
    if (state === 'running' && index === reported) return 'running'
    return 'pending'
  }

  const reportText = report ? (report.text ?? renderReport(report)) : ''
  const failed = state === 'failed'

  return (
    <div className="u-page">
      <PageHeader
        title={t('page.diagnose.title')}
        description={t('page.diagnose.description')}
        badge={
          taskId && canRun ? (
            <span className="u-row u-xs u-muted" style={{ gap: 'var(--space-2)' }}>
              <span>{t('field.taskId')}</span>
              <CopyableId value={taskId} />
            </span>
          ) : null
        }
        actions={
          canRun ? (
            <Button
              variant="primary"
              icon={<ActivityIcon />}
              loading={start.isPending || running}
              disabled={start.isPending || running}
              onClick={() => {
                start.mutate()
              }}
            >
              {taskId ? t('diagnose.action.rerun') : t('diagnose.action.run')}
            </Button>
          ) : undefined
        }
      />

      <Banner tone="accent" icon={<LockIcon size={14} />}>
        {t('diagnose.redacted')}
      </Banner>

      {canRun ? (
        <Card
          title={t('diagnose.options.title')}
          description={t('diagnose.options.description')}
        >
          <div className="u-stack">
            <Switch
              checked={includeSmoke}
              disabled={running}
              label={t('diagnose.options.smoke')}
              hint={t('diagnose.options.smokeHint')}
              onChange={(event) => {
                setIncludeSmoke(event.target.checked)
              }}
            />
            <div style={{ maxWidth: '320px' }}>
              <Select
                label={t('diagnose.options.platform')}
                description={t('diagnose.options.platformHint')}
                className="u-mono"
                value={platform}
                disabled={running || !includeSmoke}
                options={[
                  { value: '', label: t('diagnose.options.platformAuto') },
                  ...PLATFORMS.map((value) => ({ value, label: value })),
                ]}
                onChange={(event) => {
                  setPlatform(event.target.value as Platform | '')
                }}
              />
            </div>
          </div>
        </Card>
      ) : session.data ? (
        <Banner tone="caution" icon={<LockIcon size={14} />}>
          {t('diagnose.readOnly')}
        </Banner>
      ) : null}

      {start.isError ? (
        <Card>
          <ErrorState
            error={start.error}
            onRetry={() => {
              start.mutate()
            }}
          />
        </Card>
      ) : null}

      {run.isError ? (
        expired ? (
          // A remembered run outlives its result: results are kept for a
          // limited time. That is the end of a report, not a fault.
          <Banner tone="accent" icon={<ClockIcon size={14} />}>
            {t('diagnose.expired')}
          </Banner>
        ) : (
          <Card>
            <ErrorState
              error={run.error}
              onRetry={() => {
                void run.refetch()
              }}
            />
          </Card>
        )
      ) : null}

      {failed && run.data?.error ? (
        <Card>
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            <div className="u-stack-sm">
              <span className="u-mono">{run.data.error.code}</span>
              <span>{run.data.error.message}</span>
            </div>
          </Banner>
        </Card>
      ) : null}

      <Card
        title={t('diagnose.steps.title')}
        description={
          report
            ? t('diagnose.steps.finished', {
                verdict: t(`diagnose.verdict.${verdictOf(report)}`),
                when: formatters.dateTime(report.finished_at),
              })
            : running
              ? t('diagnose.steps.running')
              : t('diagnose.steps.idle')
        }
      >
        <div>
          {STEP_IDS.map((id, index) => (
            <StepView
              key={id}
              id={id}
              index={index}
              status={displayStatus(id, index)}
              result={resultsById.get(id)}
            />
          ))}
        </div>
      </Card>

      <Card
        title={t('diagnose.report.title')}
        description={t('diagnose.report.description')}
        actions={
          <Button
            variant="secondary"
            size="sm"
            icon={copied ? <CheckIcon /> : <CopyIcon />}
            disabled={!report}
            onClick={() => {
              void copy(reportText)
              toast.success(t('diagnose.toast.copied'))
            }}
          >
            {copied ? t('common:action.copied') : t('diagnose.report.copy')}
          </Button>
        }
      >
        {report ? (
          <CodeBlock code={reportText} language="text" title={t('diagnose.report.title')} copyable={false} />
        ) : (
          <p className="u-secondary" style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
            {running ? t('diagnose.report.waiting') : t('diagnose.report.empty')}
          </p>
        )}
      </Card>
    </div>
  )
}
