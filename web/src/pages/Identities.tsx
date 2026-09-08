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
  StatusBadge,
  Textarea,
  useToast,
  type Column,
} from '@/components'
import { apiDelete, apiPost, waitForTask, type ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { MISSING } from '@/lib/format'
import { POLL } from '@/lib/query'
import { IDENTITY_STATES, PLATFORMS, type Identity, type Outcome, type Platform } from '@/lib/types'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate } from '@/hooks'

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

export default function Identities() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const invalidate = useInvalidate()

  const [platform, setPlatform] = useState<Platform | 'all'>('all')
  const [state, setState] = useState<string>('all')
  const [search, setSearch] = useState('')
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

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return query.data ?? []
    return (query.data ?? []).filter((row) =>
      [row.id, row.proxy_id, row.proxy_label]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(needle)),
    )
  }, [query.data, search])

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
      header: t('console:identity.column.health'),
      width: '130px',
      sortValue: (row) => row.health ?? -row.consecutive_fails,
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
      id: 'id',
      header: t('console:field.identityId'),
      mono: true,
      cell: (row) => <CopyableId value={row.id} middle length={14} />,
    },
    {
      id: 'proxy',
      header: t('console:field.proxy'),
      sortValue: (row) => row.proxy_label ?? row.proxy_id ?? '',
      cell: (row) =>
        row.proxy_id ? (
          row.proxy_label ? (
            <span className="u-truncate">{row.proxy_label}</span>
          ) : (
            <CopyableId value={row.proxy_id} middle length={12} />
          )
        ) : (
          <span className="u-muted">{t('console:identity.noProxy')}</span>
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

  return (
    <div className="u-stack-lg">
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
          flashValue={(row) => row.state}
          caption={t('console:page.identities.title')}
          onRowClick={(row) => {
            const probe = probes[row.id]
            if (probe?.result || probe?.error) setProbeDetail({ identity: row, state: probe })
          }}
          emptyTitle={t('console:identity.empty.title')}
          emptyDescription={t('console:identity.empty.description')}
          emptyAction={
            <Button
              variant="primary"
              onClick={() => {
                setMintOpen(true)
              }}
            >
              {t('console:identity.mint.action')}
            </Button>
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

function remainingMs(until: string | null | undefined): number | null {
  if (!until) return null
  const value = new Date(until).getTime() - Date.now()
  return Number.isFinite(value) && value > 0 ? value : null
}

/**
 * Health as the API reports it when it does, and as consecutive failures when
 * it does not. Both are colour plus icon plus words, never colour alone.
 */
function HealthCell({ identity }: { identity: Identity }) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  if (identity.health !== null && identity.health !== undefined) {
    const tone =
      identity.health >= 0.8 ? 'healthy' : identity.health >= 0.4 ? 'unknown' : 'unhealthy'
    return (
      <span className="u-row">
        <StatusBadge kind="health" value={tone} size="sm" flash={false} />
        <span className="u-mono u-xs">{format.percent(identity.health, 0)}</span>
      </span>
    )
  }

  if (identity.consecutive_fails === 0) {
    return <StatusBadge kind="health" value="healthy" size="sm" flash={false} />
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
