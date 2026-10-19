import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  ConfirmDialog,
  CopyableId,
  DataTable,
  Input,
  MaskedSecret,
  Modal,
  PageHeader,
  Select,
  SpinnerIcon,
  StatusBadge,
  Textarea,
  useToast,
  type Column,
} from '@/components'
import { apiDelete, apiPost, apiPut, waitForTask, type ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { atLeast } from '@/lib/roles'
import type { Proxy } from '@/lib/types'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate, useSession } from '@/hooks'

import { ProxyDrawer, type ProbeState, type ProxyProbeResult } from './ProxyDrawer'

/* -------------------------------------------------------------------------- */
/* Bulk paste parsing                                                          */
/* -------------------------------------------------------------------------- */

/** Schemes the server accepts. Anything else is rejected before it is sent. */
// Mirrors ALLOWED_SCHEMES in src/dtk/api/routes/admin/proxy_urls.py. socks4 was
// listed here and is not accepted there, so a pasted socks4 line previewed as
// valid and was then refused on import with scheme_not_supported - the preview
// exists precisely to stop that.
const SCHEMES = ['http', 'https', 'socks5', 'socks5h'] as const

const DEFAULT_SCHEME = 'http'

export type ProxyLineProblem = 'ok' | 'unparsable' | 'scheme' | 'port' | 'host'

export interface ParsedProxyLine {
  index: number
  scheme: string
  host: string
  port: number | null
  hasCredentials: boolean
  /** Safe to render: the credentials are replaced, never echoed. */
  masked: string
  problem: ProxyLineProblem
}

function splitHostPort(value: string): [string, string] {
  // Bracketed IPv6 keeps its brackets; the port is whatever follows the last colon.
  if (value.startsWith('[')) {
    const end = value.indexOf(']')
    if (end === -1) return [value, '']
    const port = value.slice(end + 1).replace(/^:/, '')
    return [value.slice(0, end + 1), port]
  }
  const colon = value.lastIndexOf(':')
  if (colon === -1) return [value, '']
  return [value.slice(0, colon), value.slice(colon + 1)]
}

function invalid(index: number, problem: ProxyLineProblem): ParsedProxyLine {
  return { index, scheme: DEFAULT_SCHEME, host: '', port: null, hasCredentials: false, masked: '', problem }
}

/**
 * Reads one pasted line in any of the four shapes users actually hold:
 * host:port, host:port:user:pass, user:pass@host:port and scheme://user:pass@host:port
 * (docs/design/07-frontend.md). The format is detected, never declared - asking
 * which of four shapes someone has is how an import flow loses them.
 */
export function parseProxyLine(raw: string, index: number): ParsedProxyLine {
  let rest = raw.trim()
  let scheme = DEFAULT_SCHEME

  const schemeAt = rest.indexOf('://')
  if (schemeAt > 0) {
    scheme = rest.slice(0, schemeAt).toLowerCase()
    rest = rest.slice(schemeAt + 3)
  }
  if (!(SCHEMES as readonly string[]).includes(scheme)) return invalid(index, 'scheme')

  let hostPort = rest
  let hasCredentials = false

  const at = rest.lastIndexOf('@')
  if (at >= 0) {
    hasCredentials = at > 0
    hostPort = rest.slice(at + 1)
  } else {
    const pieces = rest.split(':')
    if (pieces.length === 4) {
      // host:port:user:pass - the colon-separated form providers hand out.
      hostPort = `${pieces[0]}:${pieces[1]}`
      hasCredentials = true
    } else if (pieces.length !== 2) {
      return invalid(index, 'unparsable')
    }
  }

  const [host, portText] = splitHostPort(hostPort)
  if (!host || /\s/.test(host)) return invalid(index, 'host')

  const port = Number(portText)
  if (!portText || !Number.isInteger(port) || port < 1 || port > 65535) {
    return invalid(index, 'port')
  }

  return {
    index,
    scheme,
    host,
    port,
    hasCredentials,
    masked: `${scheme}://${hasCredentials ? '***:***@' : ''}${host}:${port}`,
    problem: 'ok',
  }
}

export interface ProxyPastePreview {
  lines: ParsedProxyLine[]
  valid: number
  invalidCount: number
  duplicates: number
}

/** Parses a whole paste. Blank lines and # comments are ignored, not reported. */
export function parseProxyLines(text: string): ProxyPastePreview {
  const lines: ParsedProxyLine[] = []
  const seen = new Set<string>()
  let duplicates = 0

  text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith('#'))
    .forEach((line, index) => {
      const parsed = parseProxyLine(line, index)
      if (parsed.problem === 'ok') {
        const key = `${parsed.host}:${String(parsed.port)}`
        if (seen.has(key)) duplicates += 1
        seen.add(key)
      }
      lines.push(parsed)
    })

  return {
    lines,
    valid: lines.filter((line) => line.problem === 'ok').length,
    invalidCount: lines.filter((line) => line.problem !== 'ok').length,
    duplicates,
  }
}

/* -------------------------------------------------------------------------- */
/* API shapes                                                                  */
/* -------------------------------------------------------------------------- */

/** One list row. url_masked is null when the ciphertext no longer decrypts. */
interface ProxyRow extends Omit<Proxy, 'url_masked'> {
  url_masked: string | null
  decryptable?: boolean
}

interface ImportReport {
  created: ProxyRow[]
  rejected: Array<{ line: string; error: string }>
  counts: { created: number; rejected: number }
}

/**
 * Why the server's parser refused a line, as wire codes
 * (src/dtk/api/routes/admin/proxy_urls.py). ProxyLineProblem above is this
 * page's own reading of a line; these arrive from the server and are the ones
 * that count. A code this build has no copy for is shown exactly as it came:
 * the paste still has to be correctable from what is on screen.
 */
const IMPORT_REJECTIONS: ReadonlySet<string> = new Set([
  'unparsable',
  'empty',
  'scheme_not_supported',
  'malformed_authority',
  'missing_host',
  'missing_port',
  'port_not_a_number',
  'port_out_of_range',
])

const PROXIES_KEY = ['admin', 'proxies'] as const

/**
 * Probes run at once, at most. Each one is a worker task with its own 90 s
 * budget; one at a time, an import of twenty dead proxies took half an hour to
 * report, and all of them at once would queue twenty tasks behind whatever the
 * worker was already doing.
 */
const PROBE_CONCURRENCY = 4
const PROBE_TIMEOUT_MS = 90_000

/** Which button started a bulk probe, so only that one shows its progress. */
type TestScope = 'all' | 'selected'

interface TestRun {
  done: number
  total: number
  scope: TestScope
}

/** The host part of a masked URL - what an operator recognises a proxy by. */
function hostOf(masked: string | null | undefined): string | null {
  if (!masked) return null
  const afterScheme = masked.includes('://') ? masked.slice(masked.indexOf('://') + 3) : masked
  const authority = afterScheme.slice(afterScheme.lastIndexOf('@') + 1).split('/')[0] ?? ''
  const [host] = splitHostPort(authority)
  return host || null
}

/** What a single-proxy delete asks to be typed: the name it was given, else its host. */
function confirmNameOf(proxy: ProxyRow): string {
  return proxy.label?.trim() || hostOf(proxy.url_masked) || proxy.id
}

/* -------------------------------------------------------------------------- */
/* Page                                                                        */
/* -------------------------------------------------------------------------- */

export default function Proxies() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const invalidate = useInvalidate()
  const session = useSession()
  // Every write here is manage_pool on the server. The controls are hidden
  // below that rather than shown and answered with a 403; the server is still
  // the gate.
  const canManage = atLeast(session.data?.role ?? null, 'operator')

  const list = useApiQuery<ProxyRow[]>({
    key: PROXIES_KEY,
    path: paths.proxies.list,
    poll: POLL.slow,
  })

  const [search, setSearch] = useState('')
  const [healthFilter, setHealthFilter] = useState<'all' | 'healthy' | 'unhealthy'>('all')
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [probes, setProbes] = useState<Record<string, ProbeState>>({})
  const [testing, setTesting] = useState<TestRun | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [editing, setEditing] = useState<ProxyRow | null>(null)
  const [deleting, setDeleting] = useState<ProxyRow[] | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)

  const refresh = (): Promise<void> => invalidate(PROXIES_KEY)

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (list.data ?? []).filter((row) => {
      if (healthFilter === 'healthy' && !row.healthy) return false
      if (healthFilter === 'unhealthy' && row.healthy) return false
      if (!needle) return true
      return [row.label, row.url_masked, row.country, row.id]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(needle))
    })
  }, [list.data, search, healthFilter])

  const openProxy = (list.data ?? []).find((row) => row.id === openId) ?? null

  const setProbe = (id: string, state: ProbeState): void => {
    setProbes((current) => ({ ...current, [id]: state }))
  }

  /** Queue one probe and wait for it; the answer is also kept beside the row. */
  const probeOne = async (id: string): Promise<ProbeState> => {
    setProbe(id, { running: true })
    let settled: ProbeState
    try {
      const queued = await apiPost<{ task_id: string }>(paths.proxies.test(id))
      const result = await waitForTask<ProxyProbeResult>(queued.task_id, {
        timeoutMs: PROBE_TIMEOUT_MS,
      })
      settled = { running: false, result }
    } catch (error) {
      settled = { running: false, error: error as ApiError }
    }
    setProbe(id, settled)
    return settled
  }

  /** One proxy, from its row or its drawer. Its failure is worth a toast of its own. */
  const runProbe = async (id: string): Promise<void> => {
    const settled = await probeOne(id)
    await refresh()
    // The task failing carries a code and a request id worth keeping; a probe
    // that ran and found the proxy dead is said in the row and the drawer.
    if (settled.error) toast.apiError(settled.error, t('console:proxy.test.failed'))
  }

  /**
   * Many proxies, a few at a time, and one sentence at the end.
   *
   * After an import every failure used to raise its own toast, so pasting a
   * provider list with a dozen dead lines buried the page in a dozen error
   * toasts that stay until dismissed. The rows carry the per-proxy answer;
   * the summary is the only thing worth interrupting for.
   */
  const testMany = async (ids: readonly string[], scope: TestScope): Promise<void> => {
    if (ids.length === 0 || testing !== null) return
    const queue = [...ids]
    let ok = 0
    let failed = 0
    setTesting({ done: 0, total: queue.length, scope })
    const worker = async (): Promise<void> => {
      for (let id = queue.shift(); id !== undefined; id = queue.shift()) {
        if ((await probeOne(id)).result?.ok === true) ok += 1
        else failed += 1
        setTesting((current) => (current ? { ...current, done: current.done + 1 } : current))
      }
    }
    try {
      await Promise.all(
        Array.from({ length: Math.min(PROBE_CONCURRENCY, queue.length) }, () => worker()),
      )
    } finally {
      setTesting(null)
      await refresh()
    }
    const summary = t('console:proxy.test.summary', { ok, failed })
    if (failed > 0) toast.warning(summary)
    else toast.success(summary)
  }

  const setHealthy = useApiMutation<unknown, { ids: string[]; healthy: boolean }>(
    async ({ ids, healthy }) => {
      for (const id of ids) await apiPut(paths.proxies.byId(id), { healthy })
      return null
    },
    {
      onSuccess: async (_data, variables) => {
        toast.success(
          variables.healthy
            ? t('console:proxy.bulk.enabled', { count: variables.ids.length })
            : t('console:proxy.bulk.disabled', { count: variables.ids.length }),
        )
        setSelected(new Set())
        await refresh()
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const remove = useApiMutation<unknown, string[]>(
    async (ids) => {
      for (const id of ids) await apiDelete(paths.proxies.byId(id))
      return null
    },
    {
      onSuccess: async (_data, ids) => {
        toast.success(t('console:proxy.deleted', { count: ids.length }))
        setDeleting(null)
        setSelected(new Set())
        if (openId && ids.includes(openId)) setOpenId(null)
        await refresh()
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  // What a delete takes with it. The list already counts the identities each
  // proxy carries, so the dialog can say how many go rather than one generic
  // sentence about identities in general.
  const retiring = (deleting ?? []).reduce((sum, row) => sum + (row.identity_count ?? 0), 0)
  const deletePhrase =
    retiring === 0 || deleting === null
      ? undefined
      : deleting.length === 1 && deleting[0]
        ? confirmNameOf(deleting[0])
        : // Several proxies have no one name to type; the number of identities
          // about to go is the thing worth having read.
          String(retiring)

  const columns: Array<Column<ProxyRow>> = [
    {
      id: 'health',
      header: t('console:field.state'),
      // Wide enough for two lines of the probe error below the badge.
      width: '180px',
      sortValue: (row) => (row.healthy ? 1 : 0),
      // The reason under the badge, because "unhealthy" alone sent people to
      // the logs for the one line that says what to fix - a proxy answering
      // CONNECT with 400 is a subscription URL, not a dead server (issue 781).
      cell: (row) =>
        !row.healthy && row.last_error ? (
          <div className="u-stack-sm" style={{ alignItems: 'flex-start' }}>
            <StatusBadge kind="health" value="unhealthy" />
            <span
              className="u-xs u-muted u-clamp-2"
              style={{ maxWidth: '32ch' }}
              title={row.last_error}
            >
              {row.last_error}
            </span>
          </div>
        ) : (
          <StatusBadge kind="health" value={row.healthy ? 'healthy' : 'unhealthy'} />
        ),
    },
    {
      id: 'label',
      header: t('console:proxy.column.label'),
      sortValue: (row) => row.label ?? '',
      cell: (row) => row.label ?? <span className="u-muted">{format.number(null)}</span>,
    },
    {
      id: 'address',
      header: t('console:proxy.column.address'),
      mono: true,
      cell: (row) =>
        row.url_masked ? (
          <MaskedSecret value={row.url_masked} />
        ) : (
          <span style={{ color: 'var(--danger)' }}>{t('console:proxy.undecryptable')}</span>
        ),
    },
    {
      id: 'geo',
      header: t('console:proxy.column.geo'),
      sortValue: (row) => row.country ?? '',
      cell: (row) => (
        <span className="u-stack-sm">
          <span className="u-mono">{row.country ?? format.number(null)}</span>
          {row.timezone ? <span className="u-xs u-muted u-mono">{row.timezone}</span> : null}
        </span>
      ),
    },
    {
      id: 'latency',
      header: t('console:field.latency'),
      align: 'right',
      mono: true,
      sortValue: (row) => probes[row.id]?.result?.latency_ms ?? row.latency_ms ?? null,
      cell: (row) => format.latency(probes[row.id]?.result?.latency_ms ?? row.latency_ms),
    },
    {
      id: 'identities',
      header: t('console:proxy.column.identities'),
      align: 'right',
      mono: true,
      sortValue: (row) => row.identity_count ?? null,
      cell: (row) => format.number(row.identity_count),
    },
    {
      id: 'lastCheck',
      header: t('console:field.lastCheckAt'),
      sortValue: (row) => row.last_check_at ?? '',
      cell: (row) =>
        row.last_check_at ? (
          <span title={format.timestamp(row.last_check_at)}>{format.relative(row.last_check_at)}</span>
        ) : (
          <span className="u-muted">{t('common:time.never')}</span>
        ),
    },
    {
      id: 'id',
      header: t('console:proxy.column.id'),
      // A uuid is 36 characters and every one of them carries; the last few
      // are where two ids issued in the same second differ. A floor rather
      // than a width, because auto layout overrides a width the moment the
      // columns want more room than the container has - measured at this
      // font, 292px is the whole id plus the copy affordance.
      minWidth: '292px',
      mono: true,
      defaultHidden: true,
      cell: (row) => <CopyableId value={row.id} />,
    },
    {
      id: 'created',
      header: t('console:field.createdAt'),
      defaultHidden: true,
      sortValue: (row) => row.created_at,
      cell: (row) => <span title={format.timestamp(row.created_at)}>{format.date(row.created_at)}</span>,
    },
    ...(canManage
      ? [
          {
            id: 'actions',
            header: <span className="u-sr-only">{t('common:action.more')}</span>,
            hideable: false,
            width: '210px',
            cell: (row: ProxyRow) => (
              <span className="u-row">
                <Button
                  size="sm"
                  variant="secondary"
                  loading={probes[row.id]?.running ?? false}
                  disabled={testing !== null}
                  onClick={(event) => {
                    event.stopPropagation()
                    void runProbe(row.id)
                  }}
                >
                  {t('common:action.test')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(event) => {
                    event.stopPropagation()
                    setEditing(row)
                  }}
                >
                  {t('common:action.edit')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(event) => {
                    event.stopPropagation()
                    setDeleting([row])
                  }}
                >
                  {t('common:action.delete')}
                </Button>
              </span>
            ),
          } satisfies Column<ProxyRow>,
        ]
      : []),
  ]

  const selectedRows = rows.filter((row) => selected.has(row.id))
  // Progress in the label rather than a spinner over it: `loading` hides the
  // label, and "4 of 15" is what somebody waiting on a provider list wants.
  const progressOf = (scope: TestScope): string | null =>
    testing?.scope === scope
      ? t('console:proxy.test.progress', { done: testing.done, total: testing.total })
      : null

  return (
    <div className="u-page">
      <PageHeader
        title={t('console:page.proxies.title')}
        description={t('console:page.proxies.description')}
        actions={
          canManage ? (
            <>
              <Button
                variant="ghost"
                icon={testing?.scope === 'all' ? <SpinnerIcon /> : undefined}
                disabled={testing !== null || (list.data ?? []).length === 0}
                onClick={() => {
                  void testMany(
                    (list.data ?? []).map((row) => row.id),
                    'all',
                  )
                }}
              >
                {progressOf('all') ?? t('console:proxy.test.all')}
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setImportOpen(true)
                }}
              >
                {t('console:proxy.import.action')}
              </Button>
              <Button
                variant="primary"
                onClick={() => {
                  setAddOpen(true)
                }}
              >
                {t('console:proxy.add.action')}
              </Button>
            </>
          ) : undefined
        }
      />

      <Card flush>
        <DataTable
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          loading={list.isLoading}
          error={list.error}
          onRetry={() => {
            void list.refetch()
          }}
          storageKey="proxies"
          defaultSort={{ columnId: 'health', direction: 'asc' }}
          // Selection exists for the bulk actions, and every one of them is a
          // write; a viewer would be ticking boxes that lead nowhere.
          selectedIds={canManage ? selected : undefined}
          onSelectionChange={canManage ? setSelected : undefined}
          flashValue={(row) => (row.healthy ? 'healthy' : 'unhealthy')}
          caption={t('console:page.proxies.title')}
          onRowClick={(row) => {
            setOpenId(row.id)
          }}
          emptyTitle={t('console:proxy.empty.title')}
          emptyDescription={t('console:proxy.empty.description')}
          emptyAction={
            canManage ? (
              <Button
                variant="primary"
                onClick={() => {
                  setImportOpen(true)
                }}
              >
                {t('console:proxy.import.action')}
              </Button>
            ) : undefined
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
                  value={healthFilter}
                  aria-label={t('console:field.state')}
                  onChange={(event) => {
                    setHealthFilter(event.target.value as 'all' | 'healthy' | 'unhealthy')
                  }}
                  options={[
                    { value: 'all', label: t('common:value.all') },
                    { value: 'healthy', label: t('common:state.health.healthy') },
                    { value: 'unhealthy', label: t('common:state.health.unhealthy') },
                  ]}
                />
              </FilterSlot>
              {canManage && selected.size > 0 ? (
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    icon={testing?.scope === 'selected' ? <SpinnerIcon /> : undefined}
                    disabled={testing !== null || selectedRows.length === 0}
                    onClick={() => {
                      void testMany(
                        selectedRows.map((row) => row.id),
                        'selected',
                      )
                    }}
                  >
                    {progressOf('selected') ??
                      t('console:proxy.test.selected', { count: selectedRows.length })}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={setHealthy.isPending}
                    onClick={() => {
                      setHealthy.mutate({ ids: [...selected], healthy: true })
                    }}
                  >
                    {t('console:proxy.bulk.enable')}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={setHealthy.isPending}
                    onClick={() => {
                      setHealthy.mutate({ ids: [...selected], healthy: false })
                    }}
                  >
                    {t('console:proxy.bulk.disable')}
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => {
                      setDeleting(selectedRows)
                    }}
                  >
                    {t('console:proxy.bulk.delete')}
                  </Button>
                </>
              ) : null}
            </>
          }
        />
      </Card>

      <ProxyDrawer
        proxy={openProxy}
        probe={openProxy ? probes[openProxy.id] : undefined}
        canManage={canManage}
        onClose={() => {
          setOpenId(null)
        }}
        onTest={(proxy) => {
          void runProbe(proxy.id)
        }}
        onEdit={(proxy) => {
          const row = (list.data ?? []).find((entry) => entry.id === proxy.id)
          setOpenId(null)
          if (row) setEditing(row)
        }}
        onDelete={(proxy) => {
          const row = (list.data ?? []).find((entry) => entry.id === proxy.id)
          setOpenId(null)
          if (row) setDeleting([row])
        }}
      />

      <ProxyFormDialog
        key={editing?.id ?? 'new'}
        open={addOpen || editing !== null}
        proxy={editing}
        onClose={() => {
          setAddOpen(false)
          setEditing(null)
        }}
        onSaved={async () => {
          setAddOpen(false)
          setEditing(null)
          await refresh()
        }}
      />

      <ProxyImportDialog
        open={importOpen}
        onClose={() => {
          setImportOpen(false)
        }}
        onImported={async (created) => {
          await refresh()
          // Not awaited: the dialog's submit stayed spinning until the last
          // probe of the batch came back, which on a list of dead proxies was
          // minutes. The report is ready now; the probes report on the rows.
          void testMany(
            created.map((proxy) => proxy.id),
            'all',
          )
        }}
      />

      <ConfirmDialog
        open={deleting !== null}
        danger
        loading={remove.isPending}
        title={t('console:proxy.delete.title', { count: deleting?.length ?? 0 })}
        description={t('console:proxy.delete.description')}
        confirmLabel={t('common:action.delete')}
        confirmPhrase={deletePhrase}
        onCancel={() => {
          setDeleting(null)
        }}
        onConfirm={() => {
          remove.mutate((deleting ?? []).map((row) => row.id))
        }}
      >
        <div className="u-stack-sm">
          <p
            className="u-xs"
            style={{ margin: 0, color: retiring > 0 ? 'var(--danger)' : undefined }}
          >
            {retiring > 0
              ? t('console:proxy.delete.retires', { count: retiring })
              : t('console:proxy.delete.retiresNone')}
          </p>
          <ul className="u-stack-sm u-mono u-xs" style={{ paddingInlineStart: 'var(--space-4)' }}>
            {(deleting ?? []).slice(0, 8).map((row) => (
              <li key={row.id}>
                {row.url_masked ?? row.id}
                {row.identity_count ? (
                  <span className="u-muted">
                    {' · '}
                    {t('console:proxy.delete.rowIdentities', { count: row.identity_count })}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      </ConfirmDialog>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Create and edit                                                             */
/* -------------------------------------------------------------------------- */

/** A form control keeps its own width in a wrapping toolbar rather than collapsing. */
function FilterSlot({ children, width = '160px' }: { children: ReactNode; width?: string }) {
  return <div style={{ width, flex: '0 0 auto' }}>{children}</div>
}

interface ProxyFormDialogProps {
  open: boolean
  proxy: ProxyRow | null
  onClose: () => void
  onSaved: () => Promise<void>
}

function ProxyFormDialog({ open, proxy, onClose, onSaved }: ProxyFormDialogProps) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()

  // Mounted under a key derived from the row, so opening a different proxy
  // gets a fresh form rather than the previous row's values.
  const [url, setUrl] = useState('')
  const [label, setLabel] = useState(proxy?.label ?? '')
  const [country, setCountry] = useState(proxy?.country ?? '')
  const [timezone, setTimezone] = useState(proxy?.timezone ?? '')
  const [touched, setTouched] = useState(false)

  const parsed = url.trim() ? parseProxyLine(url, 0) : null
  const urlError = touched && parsed && parsed.problem !== 'ok' ? t(`console:proxy.line.${parsed.problem}`) : undefined

  const save = useApiMutation<unknown, void>(
    async () => {
      const body = {
        url: url.trim() || undefined,
        label: label.trim() || null,
        country: country.trim() || null,
        timezone: timezone.trim() || null,
      }
      if (proxy) return apiPut(paths.proxies.byId(proxy.id), body)
      return apiPost(paths.proxies.list, { ...body, url: url.trim() })
    },
    {
      onSuccess: async () => {
        toast.success(proxy ? t('console:proxy.updated') : t('console:proxy.created'))
        await onSaved()
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const canSave = proxy ? true : parsed?.problem === 'ok'

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={proxy ? t('console:proxy.edit.title') : t('console:proxy.add.title')}
      description={t('console:proxy.add.description')}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common:action.cancel')}
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={!canSave}
            onClick={() => {
              setTouched(true)
              if (canSave) save.mutate()
            }}
          >
            {t('common:action.save')}
          </Button>
        </>
      }
    >
      <div className="u-stack">
        <Input
          label={t('console:proxy.column.address')}
          description={t('setup:proxy.formats')}
          error={urlError}
          mono
          required={!proxy}
          value={url}
          placeholder="scheme://user:pass@host:port"
          onChange={(event) => {
            setUrl(event.target.value)
            setTouched(true)
          }}
        />
        {parsed?.problem === 'ok' ? (
          <p className="u-xs u-muted u-mono">{parsed.masked}</p>
        ) : null}
        {proxy ? <p className="u-xs u-muted">{t('console:proxy.edit.urlOptional')}</p> : null}

        <Input
          label={t('console:proxy.column.label')}
          showOptional
          value={label}
          onChange={(event) => {
            setLabel(event.target.value)
          }}
        />
        <div className="u-grid">
          <Input
            label={t('console:field.country')}
            description={t('console:proxy.geoHint')}
            showOptional
            mono
            value={country}
            onChange={(event) => {
              setCountry(event.target.value)
            }}
          />
          <Input
            label={t('console:proxy.column.timezone')}
            showOptional
            mono
            value={timezone}
            placeholder="Asia/Shanghai"
            onChange={(event) => {
              setTimezone(event.target.value)
            }}
          />
        </div>
      </div>
    </Modal>
  )
}

/* -------------------------------------------------------------------------- */
/* Bulk import                                                                 */
/* -------------------------------------------------------------------------- */

interface ProxyImportDialogProps {
  open: boolean
  onClose: () => void
  onImported: (created: ProxyRow[]) => Promise<void>
}

function ProxyImportDialog({ open, onClose, onImported }: ProxyImportDialogProps) {
  const { t } = useTranslation(['console', 'common', 'setup'])
  const toast = useToast()

  const [text, setText] = useState('')
  const [label, setLabel] = useState('')
  const [report, setReport] = useState<ImportReport | null>(null)

  const preview = useMemo(() => parseProxyLines(text), [text])

  const submit = useApiMutation<ImportReport, void>(
    () => apiPost<ImportReport>(paths.proxies.import, { text, label: label.trim() || null }),
    {
      onSuccess: async (result) => {
        setReport(result)
        toast.success(t('console:proxy.import.done', { count: result.counts.created }))
        await onImported(result.created)
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const close = (): void => {
    setText('')
    setLabel('')
    setReport(null)
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={close}
      size="lg"
      title={t('console:proxy.import.title')}
      description={t('console:proxy.import.description')}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            {report ? t('common:action.close') : t('common:action.cancel')}
          </Button>
          <Button
            variant="primary"
            loading={submit.isPending}
            disabled={preview.valid === 0}
            onClick={() => {
              submit.mutate()
            }}
          >
            {t('setup:proxy.submit')}
          </Button>
        </>
      }
    >
      <div className="u-stack">
        <Textarea
          label={t('setup:proxy.label')}
          description={t('setup:proxy.formats')}
          rows={8}
          value={text}
          placeholder={t('setup:proxy.placeholder')}
          onChange={(event) => {
            setText(event.target.value)
          }}
        />
        <Input
          label={t('console:proxy.import.labelField')}
          description={t('console:proxy.import.labelHint')}
          showOptional
          value={label}
          onChange={(event) => {
            setLabel(event.target.value)
          }}
        />

        <ProxyPreviewList preview={preview} />

        {report ? <ImportReportView report={report} /> : null}
      </div>
    </Modal>
  )
}

/** Shared with the setup wizard, which offers the same bulk paste on step two. */
export function ProxyPreviewList({ preview }: { preview: ProxyPastePreview }) {
  const { t } = useTranslation(['console', 'common'])

  if (preview.lines.length === 0) {
    return <p className="u-xs u-muted">{t('console:proxy.import.pasteHint')}</p>
  }

  return (
    <div className="u-stack-sm">
      <div className="u-row u-wrap u-xs">
        <span style={{ color: 'var(--success)' }}>
          {t('console:proxy.import.parsed', { count: preview.valid })}
        </span>
        {preview.invalidCount > 0 ? (
          <span style={{ color: 'var(--caution)' }}>
            {t('console:proxy.import.unparsed', { count: preview.invalidCount })}
          </span>
        ) : null}
        {preview.duplicates > 0 ? (
          <span className="u-muted">
            {t('console:proxy.import.duplicates', { count: preview.duplicates })}
          </span>
        ) : null}
      </div>
      <ul
        className="u-stack-sm u-scroll-x"
        style={{
          listStyle: 'none',
          margin: 0,
          padding: 'var(--space-2)',
          maxHeight: '180px',
          overflowY: 'auto',
          background: 'var(--bg-inset)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius)',
        }}
      >
        {preview.lines.map((line) => (
          <li key={line.index} className="u-row u-xs u-nowrap">
            {line.problem === 'ok' ? (
              <>
                <span className="u-mono">{line.masked}</span>
                {line.hasCredentials ? (
                  <span className="u-muted">{t('console:proxy.import.withCredentials')}</span>
                ) : null}
              </>
            ) : (
              <span style={{ color: 'var(--caution)' }}>{t(`console:proxy.line.${line.problem}`)}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function ImportReportView({ report }: { report: ImportReport }) {
  const { t } = useTranslation(['console', 'common'])

  return (
    <div className="u-stack-sm">
      <p className="u-xs">
        {t('console:proxy.import.result', {
          created: report.counts.created,
          rejected: report.counts.rejected,
        })}
      </p>
      {report.rejected.length > 0 ? (
        <ul className="u-stack-sm u-xs" style={{ paddingInlineStart: 'var(--space-4)' }}>
          {report.rejected.map((entry, index) => (
            <li key={`${entry.line}-${String(index)}`} style={{ color: 'var(--caution)' }}>
              <span className="u-mono">{entry.line}</span>{' · '}
              {IMPORT_REJECTIONS.has(entry.error)
                ? t(`console:proxy.import.reason.${entry.error}`)
                : entry.error}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
