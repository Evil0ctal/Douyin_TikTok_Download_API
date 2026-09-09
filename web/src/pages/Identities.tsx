import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertIcon,
  Button,
  Card,
  Checkbox,
  CodeBlock,
  ConfirmDialog,
  CopyableId,
  DataTable,
  ErrorState,
  IdCardIcon,
  Input,
  LockIcon,
  MaskedSecret,
  Modal,
  PageHeader,
  Select,
  Skeleton,
  SlidersIcon,
  StatusBadge,
  Textarea,
  useToast,
  type Column,
} from '@/components'
import { apiDelete, apiPost, apiPut, waitForTask, type ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { MISSING } from '@/lib/format'
import { POLL } from '@/lib/query'
import {
  IDENTITY_SOURCES,
  IDENTITY_STATES,
  PLATFORMS,
  type Identity,
  type IdentitySource,
  type Outcome,
  type Platform,
} from '@/lib/types'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate, useSession } from '@/hooks'

import styles from './identities.module.css'

/* -------------------------------------------------------------------------- */
/* API shapes                                                                  */
/* -------------------------------------------------------------------------- */

/** What the import preview understood. No endpoint ever returns a cookie value. */
interface CookieReport {
  detected_format: string
  cookie_names: string[]
  cookies_masked: Record<string, string>
  authenticated: boolean
  expires_at: string | null
  missing_required: string[]
  warnings: string[]
  usable: boolean
  browser_family: string | null
  browser_major: number | null
}

interface ImportResponse {
  stored: boolean
  identity_id?: string
  report: CookieReport
}

/** One identity probe (mirrors IdentityProbe in src/dtk/ops/probes.py). */
interface IdentityProbeResult {
  ok?: boolean
  outcome?: Outcome | null
  status?: number | null
  latency_ms?: number | null
  rule?: string | null
  detail?: string | null
}

interface ProbeState {
  running: boolean
  result?: IdentityProbeResult
  error?: ApiError
}

interface ProxyOption {
  id: string
  label?: string | null
  url_masked: string | null
  healthy: boolean
}

const IDENTITIES_KEY = ['admin', 'identities'] as const
const PAGE_LIMIT = 200

/**
 * Where a measured score stops reading as good and starts reading as poor. The
 * cell and the row flash share these, so the colour on screen and the change
 * worth animating cannot disagree about where a band ends.
 */
const HEALTH_GOOD = 0.8
const HEALTH_FAIR = 0.4

/**
 * Session verdicts that mean the credential is spent, mirroring
 * `_session_health` in src/dtk/api/routes/admin/identities.py. `missing` holds
 * no session value at all; `too_short` holds the bootstrap value the document
 * sets rather than the one the platform SDK replaces it with. A request
 * carrying either is refused with a perfectly correct signature, so neither can
 * serve traffic, and neither is worth an operator's attention by default.
 */
const DEAD_SESSION: ReadonlySet<string> = new Set(['missing', 'too_short'])

/**
 * Filter over `authenticated`. "Show me my logged-in accounts" is the question
 * asked immediately before pinning a request to one of them.
 */
type LoginFilter = 'all' | 'yes' | 'no'

/**
 * Cookie roles, mirroring REQUIRED / SESSION_MARKERS / USEFUL in
 * src/dtk/identity/importing.py. The preview has to say which of the pasted
 * cookies are load bearing, otherwise a jar that is missing the one that
 * matters looks exactly like a complete one.
 */
const REQUIRED_COOKIES: ReadonlySet<string> = new Set(['ttwid'])
const SESSION_COOKIES: ReadonlySet<string> = new Set([
  'sessionid',
  'sessionid_ss',
  'sid_tt',
  'sid_guard',
  'uid_tt',
  'sid_ucp_v1',
])
const USEFUL_COOKIES: ReadonlySet<string> = new Set([
  'odin_tt',
  's_v_web_id',
  'msToken',
  'passport_csrf_token',
  'tt_csrf_token',
  '__ac_nonce',
])

type CookieRole = 'required' | 'session' | 'useful' | 'other'

function cookieRole(name: string): CookieRole {
  if (REQUIRED_COOKIES.has(name)) return 'required'
  if (SESSION_COOKIES.has(name)) return 'session'
  if (USEFUL_COOKIES.has(name)) return 'useful'
  return 'other'
}

const ROLE_COLOR: Record<CookieRole, string> = {
  required: 'var(--accent)',
  session: 'var(--warning)',
  useful: 'var(--text-secondary)',
  other: 'var(--text-muted)',
}

/* -------------------------------------------------------------------------- */
/* Page                                                                        */
/* -------------------------------------------------------------------------- */

/**
 * Whether this identity still holds a usable session.
 *
 * `too_short` is the interesting one: the platform issued a value, so nothing
 * looks missing, but it is the bootstrap token the document sets rather than
 * the one its SDK replaces it with - and a request carrying it is refused with
 * a perfectly correct signature.
 */
/**
 * What the stored jar looks like - never how the identity's requests went.
 *
 * The verdict is a static check on one cookie: present, and long enough to be a
 * real session rather than the bootstrap value the platform hands a first-time
 * visitor. It touches no network and reads no history, which is exactly why it
 * is worth a column of its own: an identity whose credential is complete and
 * whose requests keep failing has a signing or egress problem, not a jar
 * problem, and that pair is the fastest read on this page.
 *
 * It gets its own vocabulary for the same reason. Both columns used to render
 * the health words, so two different measurements said "healthy" and a
 * disagreement between them read as the page contradicting itself.
 */
function SessionCell({ identity }: { identity: Identity }) {
  const { t } = useTranslation(['console', 'common'])
  const verdict = identity.session?.verdict ?? 'unknown'
  const cookie = identity.session?.cookie
  const title = cookie
    ? t('console:identity.session.checked', {
        cookie,
        verdict: t(`common:state.credential.${verdict}`),
      })
    : undefined

  return (
    <span className="u-row" title={title}>
      <StatusBadge kind="credential" value={verdict} size="sm" flash={false} />
      {cookie ? <span className="u-xs u-muted u-mono">{cookie}</span> : null}
    </span>
  )
}

/* -------------------------------------------------------------------------- */
/* Automatic refill                                                            */
/* -------------------------------------------------------------------------- */

const POOL_KEY = ['admin', 'identities', 'pool'] as const

interface PoolPlatform {
  platform: string
  usable: number
  live: number
  active: number
  minting: number
  below_minimum: boolean
}

/** One attempt, as the worker recorded it. `reason` is the code it logs. */
interface MintAttempt {
  ts: string
  platform: string
  ok: boolean
  reason: string
  identity_id: string | null
  error: string | null
}

interface MintActivity {
  current: { platform: string; started_at: string } | null
  recent: MintAttempt[]
  backoff: { failures: number; until: string } | null
}

interface PoolLevel {
  min_size: number
  target_size: number
  max_fail_streak: number
  can_mint: boolean
  platforms: PoolPlatform[]
  activity: MintActivity
}

/**
 * What the refill job is doing, on the page where an operator looks for it.
 *
 * The job has always existed - the worker checks every platform once a minute
 * and mints one identity at a time back up to `pool.target_size` - and nothing
 * on this board said so, which made it indistinguishable from not existing.
 *
 * `usable` rather than a row count, because that is the number the job compares
 * against the mark: an identity that fails every request stays live, so a pool
 * counted by rows sits at its target while serving nothing.
 */
function RefillCard() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const invalidate = useInvalidate()
  const session = useSession()

  const query = useApiQuery<PoolLevel>({
    key: POOL_KEY,
    path: paths.identities.pool,
    // A mint takes about five seconds. On the slow beat the panel showed every
    // result and none of the acts, which is the half an operator watches for
    // after retiring a platform's identities.
    poll: POLL.fast,
  })

  // Held as text, not as numbers: an input the operator has emptied on the way
  // to typing "12" is not the number zero, and coercing it would fight them
  // mid-keystroke.
  const [draft, setDraft] = useState<{ minimum: string; target: string } | null>(null)

  const save = useApiMutation<unknown, { minimum: number; target: number }>(
    async ({ minimum, target }) => {
      // Two settings, one button. Only what actually changed is written, so
      // saving one number does not stamp the other with an identical value and
      // move it from "default" to "set by hand" in the settings audit.
      if (minimum !== query.data?.min_size) {
        await apiPut(paths.settings.byKey('pool.min_size'), { value: minimum, confirm: false })
      }
      if (target !== query.data?.target_size) {
        await apiPut(paths.settings.byKey('pool.target_size'), { value: target, confirm: false })
      }
      return null
    },
    {
      onSuccess: () => {
        setDraft(null)
        void invalidate(POOL_KEY)
        // The scheduler page renders the same two settings, so it must not be
        // left showing the numbers this card just replaced.
        void invalidate(['admin', 'settings'])
        toast.success(t('console:identity.refill.saved'))
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.refill.saveFailed'))
      },
    },
  )

  if (query.isLoading) {
    return (
      <Card flush>
        <div className={styles.refill}>
          <Skeleton height={20} width={280} />
        </div>
      </Card>
    )
  }
  if (query.isError || !query.data) return null

  const { min_size: minimum, target_size: target, can_mint: canMint, platforms } = query.data
  // A viewer sees the numbers and cannot change them. With no session payload
  // the server is still the authority, so the controls show and its refusal is
  // what explains itself - the same rule the scheduler page follows.
  const role = session.data?.role ?? null
  const canWrite = role === null || role !== 'viewer'

  const shown = draft ?? { minimum: String(minimum), target: String(target) }
  const parsed = { minimum: Number(shown.minimum), target: Number(shown.target) }
  const valid =
    Number.isInteger(parsed.minimum) &&
    Number.isInteger(parsed.target) &&
    parsed.minimum >= 0 &&
    // Not merely "both are numbers": a target under the mark is a pool the
    // filler would top up to less than it just decided was too few, so the
    // worker clamps it - and a field that silently means something else is
    // worse than one that will not save.
    parsed.target >= parsed.minimum
  const dirty =
    draft !== null && (parsed.minimum !== minimum || parsed.target !== target) && valid

  return (
    <Card flush>
      <div className={styles.refill}>
        <span className={styles.refillTitle}>{t('console:identity.refill.title')}</span>

        <span className={styles.refillPools}>
          {platforms.map((row) => (
            <span
              key={row.platform}
              className={styles.pool}
              data-low={canMint && row.below_minimum}
              title={
                !canMint
                  ? t('console:identity.refill.manualOnly')
                  : row.minting > 0
                    ? t('console:identity.refill.minting', { count: row.minting })
                    : row.below_minimum
                      ? t('console:identity.refill.below', { minimum })
                      : t('console:identity.refill.satisfied', { minimum })
              }
            >
              <span className="u-mono">{row.platform}</span>
              <b>{row.usable}</b>
              {canMint && row.minting > 0 ? (
                <span className="u-xs u-muted">+{row.minting}</span>
              ) : null}
            </span>
          ))}
        </span>

        <span className={styles.refillSpacer} />

        {canMint ? (
          <>
            {(['minimum', 'target'] as const).map((field) => (
              <label key={field} className={styles.refillField}>
                <span className="u-xs u-muted">{t(`console:identity.refill.${field}`)}</span>
                <input
                  type="number"
                  min={0}
                  max={999}
                  inputMode="numeric"
                  className={styles.refillInput}
                  disabled={!canWrite || save.isPending}
                  value={shown[field]}
                  onChange={(event) => {
                    setDraft({ ...shown, [field]: event.target.value })
                  }}
                />
              </label>
            ))}
            <Button
              size="sm"
              variant="primary"
              disabled={!dirty || !canWrite}
              loading={save.isPending}
              onClick={() => {
                save.mutate(parsed)
              }}
            >
              {t('common:action.save')}
            </Button>
          </>
        ) : null}
      </div>

      <MintActivityRow activity={query.data.activity} canMint={canMint} />

      <p className={styles.refillHint}>
        {canMint
          ? t('console:identity.refill.description')
          : t('console:identity.refill.noBrowser')}{' '}
        {t('console:identity.refill.usableHint', { streak: query.data.max_fail_streak })}
      </p>
    </Card>
  )
}

/**
 * What the refill job is doing and how the last few attempts went.
 *
 * Part of the refill card rather than a card of its own: it is the same
 * subject, and two adjacent panels both about minting would say the pool level
 * twice. It costs a row only when there is something to say - an instance that
 * has never minted renders nothing here.
 *
 * The attempts come from the worker through Redis, because a failed mint writes
 * no identity row. That asymmetry is the whole reason this exists: a success is
 * already visible as a number going up, and a failure used to be visible only
 * in `docker compose logs`.
 */
function MintActivityRow({
  activity,
  canMint,
}: {
  activity: MintActivity
  canMint: boolean
}) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  const recent = activity.recent
  if (!canMint || (recent.length === 0 && !activity.current && !activity.backoff)) return null

  const failures = recent.filter((entry) => !entry.ok).length

  return (
    <div className={styles.mintRow}>
      {activity.current ? (
        <span className={styles.minting}>
          {/* A pulsing dot rather than a spinner: the row is one line tall and
              this is the only thing on the page that has to read as "right
              now" rather than "recently". */}
          <span className={styles.pulse} aria-hidden="true" />
          {t('console:identity.mintLog.inFlight', { platform: activity.current.platform })}
        </span>
      ) : activity.backoff ? (
        // Louder than a failed attempt: the job is not merely failing, it has
        // stopped trying until this passes.
        <span className={styles.backoff}>
          {t('console:identity.mintLog.backoff', {
            failures: activity.backoff.failures,
            when: format.relative(activity.backoff.until),
          })}
        </span>
      ) : (
        <span className="u-xs u-muted">{t('console:identity.mintLog.idle')}</span>
      )}

      {recent.length > 0 ? (
        <>
          <span className={styles.attempts}>
            {recent.map((entry, index) => (
              <span
                key={`${entry.ts}:${index}`}
                className={styles.attempt}
                data-ok={entry.ok}
                // Everything about one attempt in the tooltip: the row is a
                // shape to scan, and a run of failures sharing one reason is a
                // different story from an alternating one.
                title={[
                  entry.platform,
                  entry.ok
                    ? t('console:identity.mintLog.ok')
                    : t(`console:identity.mintLog.reason.${entry.reason}`, {
                        defaultValue: entry.reason,
                      }),
                  format.dateTime(entry.ts),
                  entry.error ?? '',
                ]
                  .filter(Boolean)
                  .join(' · ')}
              />
            ))}
          </span>
          <span className="u-xs u-muted">
            {failures === 0
              ? t('console:identity.mintLog.allOk', { count: recent.length })
              : t('console:identity.mintLog.someFailed', {
                  count: recent.length,
                  failures,
                })}
          </span>
        </>
      ) : null}
    </div>
  )
}

export default function Identities() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const invalidate = useInvalidate()

  const [platform, setPlatform] = useState<Platform | 'all'>('all')
  const [state, setState] = useState<string>('all')
  const [source, setSource] = useState<IdentitySource | 'all'>('all')
  const [login, setLogin] = useState<LoginFilter>('all')
  const [search, setSearch] = useState('')
  // Off by default: the page opens on the identities that can still serve a
  // request. The notice above the table says what that is holding back.
  const [showAll, setShowAll] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [probes, setProbes] = useState<Record<string, ProbeState>>({})
  const [probeDetail, setProbeDetail] = useState<{ identity: Identity; state: ProbeState } | null>(null)
  const [mintOpen, setMintOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [retiring, setRetiring] = useState<Identity[] | null>(null)

  const query = useApiQuery<Identity[]>({
    key: [...IDENTITIES_KEY, platform, state],
    path: paths.identities.list,
    params: {
      platform: platform === 'all' ? undefined : platform,
      state: state === 'all' ? undefined : state,
      limit: PAGE_LIMIT,
    },
    poll: POLL.fast,
  })

  const proxies = useApiQuery<ProxyOption[]>({
    key: ['admin', 'proxies', 'options'],
    path: paths.proxies.list,
    poll: POLL.slow,
  })

  const refresh = (): Promise<void> => invalidate(IDENTITIES_KEY)

  /**
   * Search, source and login run over the page the API already returned rather
   * than as query parameters: the fetch is one unpaginated page of at most
   * PAGE_LIMIT rows, so filtering here makes every count the page quotes exact
   * and instant instead of an estimate of what a next page might hold.
   */
  const matched = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (query.data ?? []).filter((row) => {
      if (source !== 'all' && row.source !== source) return false
      if (login !== 'all' && row.authenticated !== (login === 'yes')) return false
      if (!needle) return true
      // Wire values, not their translated labels. An operator typing "cooling"
      // or "imported" is typing what the API prints, and matching the localized
      // label would make the same query behave differently in each language.
      return [row.id, row.platform, row.state, row.source, row.proxy_id, row.proxy_label].some(
        (field) => field != null && String(field).toLowerCase().includes(needle),
      )
    })
  }, [query.data, search, source, login])

  /**
   * What the default view keeps back, and how much of it.
   *
   * Retired identities are husks - the credential is wiped, the row survives
   * for its statistics - and an identity whose session verdict is dead holds a
   * credential the platform refuses. Cooling and degraded stay: cooling is a
   * timer that runs out on its own, and hiding a recovering pool is how a
   * console reports "no identities" to an operator who has forty.
   */
  const view = useMemo(() => {
    // An explicit "state: retired" asks for exactly the rows the default view
    // drops, and retiring wipes the jar, so those rows also read as a dead
    // session. Suppressing either here would answer the operator's question
    // with an empty table.
    if (state === 'retired') {
      return { visible: matched, retired: 0, deadSession: 0, unusable: 0 }
    }
    const visible: Identity[] = []
    let retired = 0
    let deadSession = 0
    for (const row of matched) {
      if (row.state === 'retired') {
        retired += 1
        continue
      }
      if (DEAD_SESSION.has(row.session?.verdict ?? 'unknown')) {
        deadSession += 1
        continue
      }
      visible.push(row)
    }
    return { visible, retired, deadSession, unusable: retired + deadSession }
  }, [matched, state])

  const rows = showAll ? matched : view.visible

  const runProbe = async (identity: Identity): Promise<void> => {
    setProbes((current) => ({ ...current, [identity.id]: { running: true } }))
    try {
      const queued = await apiPost<{ task_id: string }>(paths.identities.test(identity.id))
      const result = await waitForTask<IdentityProbeResult>(queued.task_id, { timeoutMs: 120_000 })
      const next: ProbeState = { running: false, result }
      setProbes((current) => ({ ...current, [identity.id]: next }))
      setProbeDetail({ identity, state: next })
      await refresh()
    } catch (error) {
      const next: ProbeState = { running: false, error: error as ApiError }
      setProbes((current) => ({ ...current, [identity.id]: next }))
      setProbeDetail({ identity, state: next })
    }
  }

  const retire = useApiMutation<unknown, Identity[]>(
    async (targets) => {
      for (const identity of targets) {
        await apiDelete(paths.identities.byId(identity.id), {
          body: { reason: 'retired from the console' },
        })
      }
      return null
    },
    {
      onSuccess: async (_data, targets) => {
        toast.success(t('console:identity.retired', { count: targets.length }))
        setRetiring(null)
        setSelected(new Set())
        await refresh()
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const columns: Array<Column<Identity>> = [
    {
      id: 'state',
      header: t('console:field.state'),
      width: '120px',
      sortValue: (row) => row.state,
      cell: (row) => <StatusBadge kind="identity" value={row.state} />,
    },
    {
      id: 'health',
      header: (
        <span title={t('console:identity.columnHint.health')}>
          {t('console:identity.column.health')}
        </span>
      ),
      width: '155px',
      // The measured score only. A 0..1 success ratio and a count of failures
      // are different quantities, and folding the second in as a negative
      // number sorted an identity with no traffic and no failures above one
      // measured at 100%. Unmeasured rows return null, which the table sorts as
      // unmeasured rather than as either end of the scale.
      sortValue: (row) => row.health ?? null,
      cell: (row) => <HealthCell identity={row} />,
    },
    {
      id: 'platform',
      header: t('console:field.platform'),
      mono: true,
      width: '90px',
      sortValue: (row) => row.platform,
      cell: (row) => row.platform,
    },
    {
      // The column that tells "retire this identity" from "the signer is
      // broken". Both look like an empty response from the outside.
      id: 'session',
      header: (
        <span title={t('console:identity.columnHint.session')}>
          {t('console:identity.column.session')}
        </span>
      ),
      width: '165px',
      sortValue: (row) => row.session?.verdict ?? 'unknown',
      cell: (row) => <SessionCell identity={row} />,
    },
    {
      id: 'id',
      header: t('console:field.identityId'),
      mono: true,
      // Not hideable. Pinning a request names one identity by its id, and this
      // cell is where that id gets copied from; a column preference persisted
      // in localStorage could otherwise leave the page permanently unable to
      // answer the question it is now most often opened for.
      hideable: false,
      sortValue: (row) => row.id,
      cell: (row) => <CopyableId value={row.id} middle length={14} />,
    },
    {
      id: 'proxy',
      header: t('console:field.proxy'),
      // Wide enough for the "no proxy" sentence. Without it the column
      // collapses to the width of one character and renders that phrase
      // vertically, one glyph per line.
      width: '150px',
      sortValue: (row) => row.proxy_label ?? row.proxy_id ?? '',
      // The name someone gave the egress when there is one, the id when there
      // is not: the serializer sends `proxy_label: null` for an unnamed proxy,
      // for an identity with no proxy, and for a proxy row that went away
      // underneath the page. The id stays reachable on hover either way,
      // because the label is what an operator recognises and the id is what
      // they have to quote.
      cell: (row) =>
        row.proxy_id ? (
          row.proxy_label ? (
            <span className="u-truncate" title={row.proxy_id}>
              {row.proxy_label}
            </span>
          ) : (
            <CopyableId value={row.proxy_id} middle length={12} />
          )
        ) : (
          <span className="u-muted u-truncate">{t('console:identity.noProxy')}</span>
        ),
    },
    {
      id: 'cooldown',
      header: t('console:identity.column.cooldown'),
      align: 'right',
      mono: true,
      sortValue: (row) => remainingMs(row.cooldown_until) ?? -1,
      cell: (row) => {
        const remaining = remainingMs(row.cooldown_until)
        return remaining === null ? <span className="u-muted">{MISSING}</span> : format.duration(remaining)
      },
    },
    {
      id: 'lastUsed',
      header: t('console:field.lastUsedAt'),
      sortValue: (row) => row.last_used_at ?? '',
      cell: (row) =>
        row.last_used_at ? (
          <span title={format.timestamp(row.last_used_at)}>{format.relative(row.last_used_at)}</span>
        ) : (
          <span className="u-muted">{t('common:time.never')}</span>
        ),
    },
    {
      id: 'source',
      header: t('console:field.source'),
      width: '110px',
      sortValue: (row) => row.source,
      cell: (row) => <span>{t(`console:identity.source.${row.source}`)}</span>,
    },
    {
      id: 'authenticated',
      header: t('console:field.authenticated'),
      width: '120px',
      sortValue: (row) => (row.authenticated ? 1 : 0),
      cell: (row) =>
        row.authenticated ? (
          <span className="u-row" style={{ color: 'var(--warning)' }}>
            <LockIcon size={12} />
            {t('common:value.yes')}
          </span>
        ) : (
          <span className="u-muted">{t('common:value.no')}</span>
        ),
    },
    {
      id: 'fails',
      header: t('console:field.consecutiveFails'),
      align: 'right',
      mono: true,
      defaultHidden: true,
      sortValue: (row) => row.consecutive_fails,
      cell: (row) => format.number(row.consecutive_fails),
    },
    {
      id: 'minted',
      header: t('console:identity.column.minted'),
      defaultHidden: true,
      sortValue: (row) => row.minted_at,
      cell: (row) => <span title={format.timestamp(row.minted_at)}>{format.relative(row.minted_at)}</span>,
    },
    {
      id: 'actions',
      header: <span className="u-sr-only">{t('common:action.more')}</span>,
      hideable: false,
      width: '160px',
      cell: (row) => (
        <span className="u-row">
          <Button
            size="sm"
            variant="secondary"
            loading={probes[row.id]?.running ?? false}
            onClick={(event) => {
              event.stopPropagation()
              void runProbe(row)
            }}
          >
            {t('common:action.test')}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={row.state === 'retired'}
            onClick={(event) => {
              event.stopPropagation()
              setRetiring([row])
            }}
          >
            {t('console:identity.retire')}
          </Button>
        </span>
      ),
    },
  ]

  const selectedRows = rows.filter((row) => selected.has(row.id))
  // The pool is not empty, this view is. Offering "mint your first identity"
  // here would answer a question nobody asked and hide the one that matters:
  // which filter to widen.
  const filteredToNothing = rows.length === 0 && (query.data?.length ?? 0) > 0

  return (
    <div className="u-page">
      <PageHeader
        title={t('console:page.identities.title')}
        description={t('console:page.identities.description')}
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setImportOpen(true)
              }}
            >
              {t('console:identity.import.action')}
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setMintOpen(true)
              }}
            >
              {t('console:identity.mint.action')}
            </Button>
          </>
        }
      />

      <RefillCard />

      {view.unusable > 0 || showAll ? (
        <SuppressionNotice
          retired={view.retired}
          deadSession={view.deadSession}
          showAll={showAll}
          onToggle={() => {
            setShowAll((current) => !current)
          }}
        />
      ) : null}

      {/* The one sentence that stops the two columns reading as a
          contradiction. Muted and inline rather than a banner: it is a legend,
          not a warning, and the page already carries one notice. */}
      <p className="u-xs u-muted" style={{ margin: 0 }}>
        {t('console:identity.columnsDiffer')}
      </p>

      <Card flush>
        <DataTable
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          loading={query.isLoading}
          error={query.error}
          onRetry={() => {
            void query.refetch()
          }}
          storageKey="identities"
          defaultSort={{ columnId: 'state', direction: 'asc' }}
          selectedIds={selected}
          onSelectionChange={setSelected}
          // What an operator watches a 5s poll for: the state, whether the
          // session is still live, the failure streak, and which health band
          // the score landed in. The raw score is deliberately not in here - it
          // drifts with every bucket, and a table that flashes every row on
          // every poll teaches an operator to stop reading the flash.
          flashValue={(row) =>
            [
              row.state,
              row.session?.verdict ?? 'unknown',
              row.consecutive_fails,
              healthBand(row),
            ].join('|')
          }
          caption={t('console:page.identities.title')}
          onRowClick={(row) => {
            const probe = probes[row.id]
            if (probe?.result || probe?.error) setProbeDetail({ identity: row, state: probe })
          }}
          emptyTitle={
            filteredToNothing
              ? t('console:identity.empty.filteredTitle')
              : t('console:identity.empty.title')
          }
          emptyDescription={
            filteredToNothing
              ? t('console:identity.empty.filteredDescription')
              : t('console:identity.empty.description')
          }
          emptyAction={
            filteredToNothing ? null : (
              <Button
                variant="primary"
                onClick={() => {
                  setMintOpen(true)
                }}
              >
                {t('console:identity.mint.action')}
              </Button>
            )
          }
          toolbar={
            <>
              <FilterSlot width="200px">
                <Input
                  value={search}
                  placeholder={t('common:field.searchPlaceholder')}
                  aria-label={t('common:action.search')}
                  onChange={(event) => {
                    setSearch(event.target.value)
                  }}
                />
              </FilterSlot>
              <FilterSlot>
                <Select
                  value={platform}
                  aria-label={t('console:field.platform')}
                  onChange={(event) => {
                    setPlatform(event.target.value as Platform | 'all')
                  }}
                  options={[
                    { value: 'all', label: t('console:identity.filter.allPlatforms') },
                    ...PLATFORMS.map((value) => ({ value, label: value })),
                  ]}
                />
              </FilterSlot>
              <FilterSlot>
                <Select
                  value={state}
                  aria-label={t('console:field.state')}
                  onChange={(event) => {
                    setState(event.target.value)
                  }}
                  options={[
                    { value: 'all', label: t('console:identity.filter.allStates') },
                    ...IDENTITY_STATES.map((value) => ({
                      value,
                      label: t(`common:state.identity.${value}`),
                    })),
                  ]}
                />
              </FilterSlot>
              <FilterSlot width="150px">
                <Select
                  value={source}
                  aria-label={t('console:field.source')}
                  onChange={(event) => {
                    setSource(event.target.value as IdentitySource | 'all')
                  }}
                  options={[
                    { value: 'all', label: t('console:identity.filter.allSources') },
                    ...IDENTITY_SOURCES.map((value) => ({
                      value,
                      label: t(`console:identity.source.${value}`),
                    })),
                  ]}
                />
              </FilterSlot>
              <FilterSlot width="180px">
                <Select
                  value={login}
                  aria-label={t('console:field.authenticated')}
                  onChange={(event) => {
                    setLogin(event.target.value as LoginFilter)
                  }}
                  options={[
                    { value: 'all', label: t('console:identity.filter.anyLogin') },
                    { value: 'yes', label: t('console:identity.filter.loggedInOnly') },
                    { value: 'no', label: t('console:identity.filter.guestOnly') },
                  ]}
                />
              </FilterSlot>
              {selected.size > 0 ? (
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => {
                    setRetiring(selectedRows.filter((row) => row.state !== 'retired'))
                  }}
                >
                  {t('console:identity.bulkRetire')}
                </Button>
              ) : null}
            </>
          }
        />
      </Card>

      <MintDialog
        open={mintOpen}
        proxies={proxies.data ?? []}
        onClose={() => {
          setMintOpen(false)
        }}
        onDone={refresh}
      />

      <ImportDialog
        open={importOpen}
        proxies={proxies.data ?? []}
        onClose={() => {
          setImportOpen(false)
        }}
        onDone={refresh}
      />

      <ConfirmDialog
        open={retiring !== null}
        danger
        loading={retire.isPending}
        title={t('console:identity.retireDialog.title', { count: retiring?.length ?? 0 })}
        description={t('console:identity.retireDialog.description')}
        confirmLabel={t('console:identity.retire')}
        onCancel={() => {
          setRetiring(null)
        }}
        onConfirm={() => {
          retire.mutate(retiring ?? [])
        }}
      />

      <ProbeDialog
        open={probeDetail !== null}
        identity={probeDetail?.identity ?? null}
        state={probeDetail?.state ?? null}
        onClose={() => {
          setProbeDetail(null)
        }}
      />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Cells                                                                       */
/* -------------------------------------------------------------------------- */

/** A form control keeps its own width in a wrapping toolbar rather than collapsing. */
function FilterSlot({ children, width = '160px' }: { children: ReactNode; width?: string }) {
  return <div style={{ width, flex: '0 0 auto' }}>{children}</div>
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
 * of five filters is the wrong place to explain why a row is missing.
 */
function SuppressionNotice({ retired, deadSession, showAll, onToggle }: SuppressionNoticeProps) {
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

/**
 * The band HealthCell paints, not the score behind it. The flash key uses this
 * so a drift from 0.94 to 0.93 between two polls is not reported as an event.
 */
function healthBand(identity: Identity): string {
  if (identity.health === null || identity.health === undefined) return 'none'
  if (identity.health >= HEALTH_GOOD) return 'good'
  return identity.health >= HEALTH_FAIR ? 'fair' : 'poor'
}

function remainingMs(until: string | null | undefined): number | null {
  if (!until) return null
  const value = new Date(until).getTime() - Date.now()
  return Number.isFinite(value) && value > 0 ? value : null
}

/**
 * Health as the API reports it when it does, and as consecutive failures when
 * it does not. Both are colour plus icon plus words, never colour alone.
 *
 * `health` is null exactly when the aggregate has no traffic for this identity,
 * which is not a pass. A freshly minted identity that has never served a
 * request used to draw the same unqualified "Healthy" badge as one measured at
 * 100% over the last hour - collapsing "nothing is known" into "it works",
 * which is the one distinction this column exists to make.
 */
function HealthCell({ identity }: { identity: Identity }) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  if (identity.health !== null && identity.health !== undefined) {
    const tone =
      identity.health >= HEALTH_GOOD
        ? 'healthy'
        : identity.health >= HEALTH_FAIR
          ? 'unknown'
          : 'unhealthy'
    return (
      <span className="u-row">
        <StatusBadge kind="health" value={tone} size="sm" flash={false} />
        <span className="u-mono u-xs">{format.percent(identity.health, 0)}</span>
      </span>
    )
  }

  if (identity.consecutive_fails === 0) {
    return (
      <span className="u-row" title={t('console:identity.health.noTrafficHint')}>
        <StatusBadge kind="health" value="unknown" size="sm" flash={false} />
        <span className="u-xs u-muted">{t('console:identity.health.noTraffic')}</span>
      </span>
    )
  }
  return (
    <span className="u-row">
      <StatusBadge
        kind="health"
        value={identity.consecutive_fails >= 3 ? 'unhealthy' : 'unknown'}
        size="sm"
        flash={false}
      />
      <span className="u-mono u-xs u-muted">
        {t('console:identity.failCount', { count: identity.consecutive_fails })}
      </span>
    </span>
  )
}

/* -------------------------------------------------------------------------- */
/* Mint                                                                        */
/* -------------------------------------------------------------------------- */

interface MintDialogProps {
  open: boolean
  proxies: ProxyOption[]
  onClose: () => void
  onDone: () => Promise<void>
}

function MintDialog({ open, proxies, onClose, onDone }: MintDialogProps) {
  const { t } = useTranslation(['console', 'common', 'setup'])
  const toast = useToast()

  const [platform, setPlatform] = useState<Platform>('douyin')
  const [count, setCount] = useState(1)
  const [proxyId, setProxyId] = useState('')
  const [pending, setPending] = useState(0)

  const mint = useApiMutation<{ task_ids: string[]; count: number }, void>(
    () =>
      apiPost<{ task_ids: string[]; count: number }>(paths.identities.mint, {
        platform,
        count,
        proxy_id: proxyId || null,
      }),
    {
      onSuccess: async (queued) => {
        // Minting drives a real browser, so it is queued; wait it out here so
        // the wizard and the pool page both report a fact, not a submission.
        setPending(queued.task_ids.length)
        let done = 0
        for (const taskId of queued.task_ids) {
          try {
            await waitForTask(taskId, { timeoutMs: 180_000 })
            done += 1
          } catch (error) {
            toast.apiError(error, t('console:identity.mint.failed'))
          }
          setPending(queued.task_ids.length - done)
        }
        setPending(0)
        if (done > 0) toast.success(t('console:identity.mint.done', { count: done }))
        await onDone()
        onClose()
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.mint.failed'))
      },
    },
  )

  const busy = mint.isPending || pending > 0

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('console:identity.mint.title')}
      description={t('console:identity.mint.description')}
      closeOnBackdrop={!busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common:action.cancel')}
          </Button>
          <Button
            variant="primary"
            loading={busy}
            onClick={() => {
              mint.mutate()
            }}
          >
            {t('setup:mint.submit')}
          </Button>
        </>
      }
    >
      <div className="u-stack">
        <Select
          label={t('console:field.platform')}
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
          max={10}
          mono
          value={count}
          onChange={(event) => {
            const next = Number(event.target.value)
            setCount(Number.isFinite(next) ? Math.min(10, Math.max(1, Math.trunc(next))) : 1)
          }}
        />
        <Select
          label={t('console:field.proxy')}
          description={t('console:identity.mint.proxyHint')}
          showOptional
          value={proxyId}
          onChange={(event) => {
            setProxyId(event.target.value)
          }}
          options={[
            { value: '', label: t('console:identity.mint.anyProxy') },
            ...proxies.map((proxy) => ({
              value: proxy.id,
              label: proxy.label ?? proxy.url_masked ?? proxy.id,
              disabled: !proxy.healthy,
            })),
          ]}
        />
        {busy ? <p className="u-xs u-muted">{t('setup:mint.running')}</p> : null}
      </div>
    </Modal>
  )
}

/* -------------------------------------------------------------------------- */
/* Import                                                                      */
/* -------------------------------------------------------------------------- */

interface ImportDialogProps {
  open: boolean
  proxies: ProxyOption[]
  onClose: () => void
  onDone: () => Promise<void>
}

/**
 * Cookie import.
 *
 * Two things this dialog must do, both from docs/design/07-frontend.md: accept
 * all four paste formats without asking which one it is, and say plainly that
 * the pasted value is the account itself. V4 had users commit cookies to
 * config.yaml in git; the way not to repeat that is to make the stakes visible
 * at the moment of pasting.
 */
function ImportDialog({ open, proxies, onClose, onDone }: ImportDialogProps) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()

  const [platform, setPlatform] = useState<Platform>('douyin')
  const [cookies, setCookies] = useState('')
  const [userAgent, setUserAgent] = useState('')
  const [proxyId, setProxyId] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [preview, setPreview] = useState<{
    loading: boolean
    report?: CookieReport
    error?: ApiError
  }>({ loading: false })

  // Live preview: the server is the authority on what the paste means, so the
  // dry run is what gets rendered. Debounced, because it runs on every keystroke.
  useEffect(() => {
    const text = cookies.trim()
    if (!open || text.length < 8) {
      setPreview({ loading: false })
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setPreview({ loading: true })
      apiPost<ImportResponse>(
        paths.identities.import,
        {
          platform,
          cookies: text,
          user_agent: userAgent.trim() || null,
          dry_run: true,
        },
        { signal: controller.signal },
      )
        .then((response) => {
          setPreview({ loading: false, report: response.report })
        })
        .catch((error: ApiError) => {
          if (error.kind !== 'aborted') setPreview({ loading: false, error })
        })
    }, 600)

    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [open, cookies, platform, userAgent])

  const store = useApiMutation<ImportResponse, void>(
    () =>
      apiPost<ImportResponse>(paths.identities.import, {
        platform,
        cookies: cookies.trim(),
        user_agent: userAgent.trim() || null,
        proxy_id: proxyId || null,
        dry_run: false,
      }),
    {
      onSuccess: async () => {
        toast.success(t('console:identity.import.stored'))
        close()
        await onDone()
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.import.failed'))
      },
    },
  )

  function close(): void {
    // The paste is a credential; it does not linger in component state.
    setCookies('')
    setUserAgent('')
    setAcknowledged(false)
    setPreview({ loading: false })
    onClose()
  }

  const report = preview.report
  const canStore = Boolean(report?.usable) && acknowledged && !preview.loading

  return (
    <Modal
      open={open}
      onClose={close}
      size="lg"
      title={t('console:identity.import.title')}
      description={t('console:identity.import.description')}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            {t('common:action.cancel')}
          </Button>
          <Button
            variant="primary"
            loading={store.isPending}
            disabled={!canStore}
            onClick={() => {
              store.mutate()
            }}
          >
            {t('console:identity.import.submit')}
          </Button>
        </>
      }
    >
      <div className="u-stack">
        <div
          className="u-stack-sm"
          style={{
            padding: 'var(--space-3)',
            border: '1px solid var(--border)',
            borderLeft: '3px solid var(--warning)',
            borderRadius: 'var(--radius)',
            background: 'var(--bg-inset)',
          }}
        >
          <span className="u-row" style={{ color: 'var(--warning)' }}>
            <AlertIcon size={13} />
            <strong>{t('console:identity.import.risk.title')}</strong>
          </span>
          <p className="u-xs u-secondary">{t('console:identity.import.risk.equivalence')}</p>
          <p className="u-xs u-secondary">{t('console:identity.import.risk.storage')}</p>
          <p className="u-xs u-secondary">{t('console:identity.import.risk.dedicated')}</p>
        </div>

        <Select
          label={t('console:field.platform')}
          value={platform}
          onChange={(event) => {
            setPlatform(event.target.value as Platform)
          }}
          options={PLATFORMS.map((value) => ({ value, label: value }))}
        />

        <Textarea
          label={t('console:identity.import.cookies')}
          description={t('console:identity.import.formats')}
          rows={7}
          required
          value={cookies}
          placeholder="ttwid=...; odin_tt=...; sessionid=..."
          onChange={(event) => {
            setCookies(event.target.value)
          }}
        />

        <Input
          label={t('console:identity.import.userAgent')}
          description={t('console:identity.import.userAgentHint')}
          showOptional
          mono
          value={userAgent}
          onChange={(event) => {
            setUserAgent(event.target.value)
          }}
        />

        <Select
          label={t('console:field.proxy')}
          description={t('console:identity.import.proxyHint')}
          showOptional
          value={proxyId}
          onChange={(event) => {
            setProxyId(event.target.value)
          }}
          options={[
            { value: '', label: t('console:identity.mint.anyProxy') },
            ...proxies.map((proxy) => ({
              value: proxy.id,
              label: proxy.label ?? proxy.url_masked ?? proxy.id,
              disabled: !proxy.healthy,
            })),
          ]}
        />

        {preview.loading ? <Skeleton height={120} radius="var(--radius)" /> : null}
        {preview.error ? <ErrorState error={preview.error} compact /> : null}
        {report && !preview.loading ? <CookieReportView report={report} /> : null}

        <Checkbox
          checked={acknowledged}
          onChange={(event) => {
            setAcknowledged(event.target.checked)
          }}
          label={t('console:identity.import.acknowledge')}
        />
        {report?.expires_at ? (
          <p className="u-xs u-muted">
            {t('console:identity.import.expiryNotice', {
              date: format.date(report.expires_at),
            })}
          </p>
        ) : null}
      </div>
    </Modal>
  )
}

function CookieReportView({ report }: { report: CookieReport }) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  const names = report.cookie_names.length > 0 ? report.cookie_names : Object.keys(report.cookies_masked)

  return (
    <div
      className="u-stack"
      style={{
        padding: 'var(--space-3)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        background: 'var(--bg-raised)',
      }}
    >
      <div className="u-row u-wrap">
        <StatusBadge
          kind="health"
          value={report.usable ? 'healthy' : 'unhealthy'}
          flash={false}
          title={t('console:identity.import.usableHint')}
        />
        <Fact label={t('console:identity.import.format')} value={report.detected_format} mono />
        <Fact
          label={t('console:identity.import.browser')}
          value={
            report.browser_family
              ? `${report.browser_family}${report.browser_major ? ` ${String(report.browser_major)}` : ''}`
              : MISSING
          }
          mono
        />
        <Fact
          label={t('console:identity.import.loggedIn')}
          value={report.authenticated ? t('common:value.yes') : t('common:value.no')}
        />
        <Fact
          label={t('console:field.expiresAt')}
          value={
            report.expires_at
              ? `${format.date(report.expires_at)} · ${format.relative(report.expires_at)}`
              : MISSING
          }
        />
        <Fact label={t('console:identity.import.count')} value={format.number(names.length)} mono />
      </div>

      {report.missing_required.length > 0 ? (
        <p className="u-row u-xs" style={{ color: 'var(--danger)', alignItems: 'flex-start' }}>
          <AlertIcon size={12} />
          <span className="u-mono">
            {t('console:identity.import.missing')} {report.missing_required.join(', ')}
          </span>
        </p>
      ) : null}

      {report.warnings.map((warning) => (
        <p key={warning} className="u-xs u-mono" style={{ color: 'var(--caution)' }}>
          {warning}
        </p>
      ))}

      <ul
        className="u-stack-sm"
        style={{
          listStyle: 'none',
          margin: 0,
          padding: 0,
          maxHeight: '200px',
          overflowY: 'auto',
        }}
      >
        {names.map((name) => {
          const role = cookieRole(name)
          return (
            <li key={name} className="u-row-between u-nowrap">
              <span className="u-row">
                <span className="u-mono">{name}</span>
                <span className="u-xs" style={{ color: ROLE_COLOR[role] }}>
                  {t(`console:identity.import.role.${role}`)}
                </span>
              </span>
              <MaskedSecret value={report.cookies_masked[name] ?? null} />
            </li>
          )
        })}
      </ul>
      <p className="u-xs u-muted">{t('console:identity.import.maskNote')}</p>
    </div>
  )
}

function Fact({ label, value, mono }: { label: ReactNode; value: ReactNode; mono?: boolean }) {
  return (
    <span className="u-stack-sm">
      <span className="u-xs u-muted">{label}</span>
      <span className={mono ? 'u-mono' : undefined}>{value}</span>
    </span>
  )
}

/* -------------------------------------------------------------------------- */
/* Probe result                                                                */
/* -------------------------------------------------------------------------- */

interface ProbeDialogProps {
  open: boolean
  identity: Identity | null
  state: ProbeState | null
  onClose: () => void
}

function ProbeDialog({ open, identity, state, onClose }: ProbeDialogProps) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  if (!identity) return null
  const result = state?.result

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('console:identity.test.title')}
      description={identity.id}
      footer={
        <Button variant="secondary" onClick={onClose}>
          {t('common:action.close')}
        </Button>
      }
    >
      <div className="u-stack">
        {state?.running ? <Skeleton height={80} radius="var(--radius)" /> : null}
        {state?.error ? <ErrorState error={state.error} /> : null}
        {result ? (
          <>
            <div className="u-row u-wrap">
              <StatusBadge kind="health" value={result.ok ? 'healthy' : 'unhealthy'} flash={false} />
              {result.outcome ? <StatusBadge kind="outcome" value={result.outcome} flash={false} /> : null}
            </div>
            <div className="u-row u-wrap">
              <Fact label={t('console:field.httpStatus')} value={result.status ?? MISSING} mono />
              <Fact label={t('console:field.latency')} value={format.latency(result.latency_ms)} mono />
              <Fact label={t('console:identity.test.rule')} value={result.rule ?? MISSING} mono />
            </div>
            {result.detail ? <CodeBlock code={result.detail} language="text" /> : null}
            <p className="u-xs u-muted">{t('console:identity.test.businessErrorNote')}</p>
          </>
        ) : null}
        {!state?.running && !result && !state?.error ? (
          <p className="u-row u-muted">
            <IdCardIcon size={14} />
            {t('console:identity.test.idle')}
          </p>
        ) : null}
      </div>
    </Modal>
  )
}
