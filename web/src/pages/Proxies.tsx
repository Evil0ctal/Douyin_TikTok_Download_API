import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  CodeBlock,
  ConfirmDialog,
  CopyableId,
  DataTable,
  EmptyState,
  ErrorState,
  GlobeIcon,
  Input,
  MaskedSecret,
  Modal,
  PageHeader,
  Select,
  StatusBadge,
  Textarea,
  useToast,
  type Column,
} from '@/components'
import { apiDelete, apiPost, apiPut, waitForTask, type ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import type { Proxy } from '@/lib/types'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate } from '@/hooks'

/* -------------------------------------------------------------------------- */
/* Bulk paste parsing                                                          */
/* -------------------------------------------------------------------------- */

/** Schemes the server accepts. Anything else is rejected before it is sent. */
const SCHEMES = ['http', 'https', 'socks5', 'socks5h', 'socks4'] as const

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

/** What a proxy probe reports (mirrors ProxyProbe in src/dtk/cli/probes.py). */
interface ProxyProbeResult {
  ok?: boolean
  latency_ms?: number | null
  exit_ip?: string | null
  country?: string | null
  timezone?: string | null
  detail?: string | null
}

interface ProbeState {
  running: boolean
  result?: ProxyProbeResult
  error?: ApiError
}

const PROXIES_KEY = ['admin', 'proxies'] as const

/* -------------------------------------------------------------------------- */
/* Page                                                                        */
/* -------------------------------------------------------------------------- */

export default function Proxies() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const invalidate = useInvalidate()

  const list = useApiQuery<ProxyRow[]>({
    key: PROXIES_KEY,
    path: paths.proxies.list,
    poll: POLL.slow,
  })

  const [search, setSearch] = useState('')
  const [healthFilter, setHealthFilter] = useState<'all' | 'healthy' | 'unhealthy'>('all')
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [probes, setProbes] = useState<Record<string, ProbeState>>({})
  const [addOpen, setAddOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [editing, setEditing] = useState<ProxyRow | null>(null)
  const [deleting, setDeleting] = useState<ProxyRow[] | null>(null)
  const [probeDetail, setProbeDetail] = useState<{ proxy: ProxyRow; state: ProbeState } | null>(null)

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

  const setProbe = (id: string, state: ProbeState): void => {
    setProbes((current) => ({ ...current, [id]: state }))
  }

  /** Queue a probe, wait for the task, then keep the answer beside the row. */
  const runProbe = async (id: string): Promise<void> => {
    setProbe(id, { running: true })
    try {
      const queued = await apiPost<{ task_id: string }>(paths.proxies.test(id))
      const result = await waitForTask<ProxyProbeResult>(queued.task_id, { timeoutMs: 90_000 })
      setProbe(id, { running: false, result })
      await refresh()
    } catch (error) {
      setProbe(id, { running: false, error: error as ApiError })
      toast.apiError(error, t('console:proxy.test.failed'))
    }
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
        await refresh()
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const columns: Array<Column<ProxyRow>> = [
    {
      id: 'health',
      header: t('console:field.state'),
      width: '130px',
      sortValue: (row) => (row.healthy ? 1 : 0),
      cell: (row) => <StatusBadge kind="health" value={row.healthy ? 'healthy' : 'unhealthy'} />,
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
      mono: true,
      defaultHidden: true,
      cell: (row) => <CopyableId value={row.id} middle length={12} />,
    },
    {
      id: 'created',
      header: t('console:field.createdAt'),
      defaultHidden: true,
      sortValue: (row) => row.created_at,
      cell: (row) => <span title={format.timestamp(row.created_at)}>{format.date(row.created_at)}</span>,
    },
    {
      id: 'actions',
      header: <span className="u-sr-only">{t('common:action.more')}</span>,
      hideable: false,
      width: '210px',
      cell: (row) => (
        <span className="u-row">
          <Button
            size="sm"
            variant="secondary"
            loading={probes[row.id]?.running ?? false}
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
    },
  ]

  const selectedRows = rows.filter((row) => selected.has(row.id))

  return (
    <div className="u-stack-lg">
      <PageHeader
        title={t('console:page.proxies.title')}
        description={t('console:page.proxies.description')}
        actions={
          <>
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
          selectedIds={selected}
          onSelectionChange={setSelected}
          flashValue={(row) => (row.healthy ? 'healthy' : 'unhealthy')}
          caption={t('console:page.proxies.title')}
          onRowClick={(row) => {
            const state = probes[row.id]
            if (state?.result || state?.error) setProbeDetail({ proxy: row, state })
          }}
          emptyTitle={t('console:proxy.empty.title')}
          emptyDescription={t('console:proxy.empty.description')}
          emptyAction={
            <Button
              variant="primary"
              onClick={() => {
                setImportOpen(true)
              }}
            >
              {t('console:proxy.import.action')}
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
              {selected.size > 0 ? (
                <>
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
          for (const proxy of created) await runProbe(proxy.id)
        }}
      />

      <ConfirmDialog
        open={deleting !== null}
        danger
        loading={remove.isPending}
        title={t('console:proxy.delete.title', { count: deleting?.length ?? 0 })}
        description={t('console:proxy.delete.description')}
        confirmLabel={t('common:action.delete')}
        onCancel={() => {
          setDeleting(null)
        }}
        onConfirm={() => {
          remove.mutate((deleting ?? []).map((row) => row.id))
        }}
      >
        <ul className="u-stack-sm u-mono u-xs" style={{ paddingInlineStart: 'var(--space-4)' }}>
          {(deleting ?? []).slice(0, 8).map((row) => (
            <li key={row.id}>{row.url_masked ?? row.id}</li>
          ))}
        </ul>
      </ConfirmDialog>

      <ProbeResultDialog
        open={probeDetail !== null}
        proxy={probeDetail?.proxy ?? null}
        state={probeDetail?.state ?? null}
        onClose={() => {
          setProbeDetail(null)
        }}
      />
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
        <ul className="u-stack-sm u-xs u-mono" style={{ paddingInlineStart: 'var(--space-4)' }}>
          {report.rejected.map((entry, index) => (
            <li key={`${entry.line}-${String(index)}`} style={{ color: 'var(--caution)' }}>
              {entry.line} · {entry.error}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Probe result                                                                */
/* -------------------------------------------------------------------------- */

interface ProbeResultDialogProps {
  open: boolean
  proxy: ProxyRow | null
  state: ProbeState | null
  onClose: () => void
}

function ProbeResultDialog({ open, proxy, state, onClose }: ProbeResultDialogProps) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  if (!proxy) return null

  const result = state?.result

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('console:proxy.test.title')}
      description={proxy.url_masked ?? proxy.id}
      footer={
        <Button variant="secondary" onClick={onClose}>
          {t('common:action.close')}
        </Button>
      }
    >
      {state?.error ? <ErrorState error={state.error} /> : null}
      {result ? (
        <div className="u-stack">
          <StatusBadge kind="health" value={result.ok ? 'healthy' : 'unhealthy'} />
          <dl className="u-stack-sm">
            <DetailRow label={t('console:field.latency')} value={format.latency(result.latency_ms)} />
            <DetailRow label={t('console:proxy.exitIp')} value={result.exit_ip ?? format.number(null)} mono />
            <DetailRow label={t('console:field.country')} value={result.country ?? format.number(null)} mono />
            <DetailRow
              label={t('console:proxy.column.timezone')}
              value={result.timezone ?? format.number(null)}
              mono
            />
          </dl>
          {result.detail ? <CodeBlock code={result.detail} language="text" /> : null}
        </div>
      ) : null}
      {!result && !state?.error ? <EmptyState icon={<GlobeIcon size={16} />} /> : null}
    </Modal>
  )
}

function DetailRow({ label, value, mono }: { label: ReactNode; value: ReactNode; mono?: boolean }) {
  return (
    <div className="u-row-between">
      <dt className="u-muted u-xs">{label}</dt>
      <dd className={mono ? 'u-mono' : undefined} style={{ margin: 0 }}>
        {value}
      </dd>
    </div>
  )
}
