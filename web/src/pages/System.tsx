import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertIcon,
  Button,
  Card,
  CheckIcon,
  CopyableId,
  DataTable,
  ErrorState,
  ExternalIcon,
  GlobeIcon,
  MetricTile,
  PageHeader,
  RefreshIcon,
  ServerIcon,
  Skeleton,
  StatusBadge,
  Switch,
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate, useSession } from '@/hooks'
import { apiPut } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'

/**
 * Instance status.
 *
 * Two things here are not decoration. The Chromium major and the wreq profile
 * major sit side by side because a drift between them is the failure mode doc 04
 * warns about: signatures keep being produced, they just stop matching what the
 * platform expects, and nothing else on the console would show it.
 *
 * The update check is opt-in and says out loud that it leaves the machine. Some
 * deployments have no egress at all, and quietly calling home from an operations
 * console is not a decision to make on the user's behalf (doc 15).
 */

const STATUS_KEY = ['system', 'status'] as const
const SETTINGS_KEY = ['admin', 'settings'] as const

const CHECK_UPDATES_KEY = 'system.check_updates'

const RELEASES_API = 'https://api.github.com/repos/Evil0ctal/Douyin_TikTok_Download_API/releases/latest'
const RELEASES_PAGE = 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases'

interface RawComponent {
  ok?: boolean | null
  configured?: boolean | null
  latency_ms?: number | null
  error?: string | null
  detail?: string | null
  warm_contexts?: number | null
  chromium_major?: number | null
  wreq_profile_major?: number | null
}

interface SystemStatusResponse {
  version: string
  commit?: string | null
  uptime_seconds: number
  settings_version?: number | null
  components: Record<string, RawComponent>
  pool?: Record<string, unknown> | null
  storage?: Record<string, unknown> | null
}

interface SettingRow {
  key: string
  value: unknown
  source: string
}

interface SettingsResponse {
  version: number
  settings: SettingRow[]
}

interface ComponentRow {
  name: string
  health: 'healthy' | 'unhealthy' | 'unknown'
  latencyMs: number | null
  /** Whatever the server said, verbatim; it is already in the request's language. */
  detail: string | null
  /** Absent rather than broken, and the only part of the cell the console phrases. */
  notConfigured: boolean
  warmContexts: number | null
}

interface StorageRow {
  table: string
  rows: number
}

interface ReleaseInfo {
  tag: string
  publishedAt: string | null
  url: string
}

/**
 * A failed update check is kept as its copy key and arguments rather than as the
 * sentence it rendered to. The banner outlives a language switch, and a frozen
 * string would be the one line on the page that stays in the old language.
 */
interface ReleaseError {
  key: string
  args?: Record<string, number | string>
}

/** "v5.0.0" and "5.0.0" are the same release; anything else is treated as newer. */
function sameVersion(tag: string, version: string | undefined): boolean {
  if (!version) return false
  return tag.replace(/^v/i, '').trim() === version.replace(/^v/i, '').trim()
}

function toComponentRows(components: Record<string, RawComponent>): ComponentRow[] {
  return Object.entries(components).map(([name, raw]) => ({
    name,
    health:
      raw.ok === true
        ? 'healthy'
        : raw.ok === false
          ? 'unhealthy'
          : // A component that reports "configured: false" is absent, not broken.
            'unknown',
    latencyMs: typeof raw.latency_ms === 'number' ? raw.latency_ms : null,
    detail: raw.error ?? raw.detail ?? null,
    notConfigured: raw.configured === false,
    warmContexts: typeof raw.warm_contexts === 'number' ? raw.warm_contexts : null,
  }))
}

function toStorageRows(storage: Record<string, unknown> | null | undefined): StorageRow[] {
  if (!storage) return []
  const found: StorageRow[] = []
  const nested = storage['rows']
  if (nested && typeof nested === 'object') {
    for (const [table, value] of Object.entries(nested as Record<string, unknown>)) {
      if (typeof value === 'number') found.push({ table, rows: value })
    }
  }
  // The flat shape from doc 15's example: request_log_rows beside db_size_bytes.
  for (const [key, value] of Object.entries(storage)) {
    if (key.endsWith('_rows') && typeof value === 'number') {
      found.push({ table: key.replace(/_rows$/, ''), rows: value })
    }
  }
  if (typeof storage['identities'] === 'number') {
    found.push({ table: 'identities', rows: storage['identities'] })
  }
  const unique = new Map<string, StorageRow>()
  for (const row of found) unique.set(row.table, row)
  return [...unique.values()].sort((a, b) => b.rows - a.rows)
}

function activeIdentities(pool: Record<string, unknown> | null | undefined): number | null {
  if (!pool) return null
  const total = pool['total_active']
  if (typeof total === 'number') return total
  const active = pool['active']
  if (typeof active === 'number') return active
  let sum: number | null = null
  for (const value of Object.values(pool)) {
    if (value && typeof value === 'object') {
      const perState = (value as Record<string, unknown>)['active']
      if (typeof perState === 'number') sum = (sum ?? 0) + perState
    }
  }
  return sum
}

function majorOf(components: Record<string, RawComponent>, field: keyof RawComponent): number | null {
  for (const component of Object.values(components)) {
    const value = component[field]
    if (typeof value === 'number') return value
  }
  return null
}

function Banner({
  tone,
  icon,
  children,
}: {
  tone: 'accent' | 'caution' | 'success'
  icon: ReactNode
  children: ReactNode
}) {
  const border = tone === 'accent' ? 'var(--border)' : `var(--${tone})`
  return (
    <div
      style={{
        display: 'flex',
        gap: 'var(--space-2)',
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius)',
        border: `1px solid ${border}`,
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

function VersionCell({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <div
      style={{
        flex: '1 1 160px',
        minWidth: 0,
        padding: 'var(--space-3)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 'var(--radius)',
        background: 'var(--bg-inset)',
      }}
    >
      <div className="u-xs u-muted">{label}</div>
      <div
        className="u-mono"
        style={{ fontSize: 'var(--text-xl)', lineHeight: 'var(--leading-xl)', color: 'var(--text)' }}
      >
        {value}
      </div>
    </div>
  )
}

export default function System() {
  const { t } = useTranslation(['console', 'common'])
  const formatters = useFormatters()
  const toast = useToast()
  const invalidate = useInvalidate()
  const session = useSession()

  const status = useApiQuery<SystemStatusResponse>({
    key: STATUS_KEY,
    path: paths.system.status,
    poll: POLL.normal,
  })

  const settings = useApiQuery<SettingsResponse>({
    key: SETTINGS_KEY,
    path: paths.settings.list,
    poll: POLL.slow,
  })

  const [release, setRelease] = useState<ReleaseInfo | null>(null)
  const [releaseError, setReleaseError] = useState<ReleaseError | null>(null)
  const [checking, setChecking] = useState(false)

  const role = session.data?.role ?? null
  const canWrite = role === null || role !== 'viewer'

  const components = useMemo(
    () => toComponentRows(status.data?.components ?? {}),
    [status.data],
  )
  const storageRows = useMemo(() => toStorageRows(status.data?.storage), [status.data])

  const chromiumMajor = majorOf(status.data?.components ?? {}, 'chromium_major')
  const wreqMajor = majorOf(status.data?.components ?? {}, 'wreq_profile_major')
  const drift = chromiumMajor !== null && wreqMajor !== null && chromiumMajor !== wreqMajor

  const checkUpdates =
    settings.data?.settings.find((row) => row.key === CHECK_UPDATES_KEY)?.value === true

  const saveCheckUpdates = useApiMutation<unknown, boolean>(
    (value) => apiPut(paths.settings.byKey(CHECK_UPDATES_KEY), { value, confirm: false }),
    {
      onSuccess: () => {
        void invalidate(SETTINGS_KEY)
        toast.success(t('system.updates.saved'))
      },
      onError: (error) => {
        toast.apiError(error, t('system.updates.saveFailed'))
      },
    },
  )

  /**
   * The one outbound request the console can make, and only on a click. It goes
   * from this browser to github.com, never from the server.
   */
  const runUpdateCheck = async (): Promise<void> => {
    setChecking(true)
    setReleaseError(null)
    try {
      const response = await fetch(RELEASES_API, { headers: { Accept: 'application/vnd.github+json' } })
      if (!response.ok) {
        setReleaseError({ key: 'system.updates.httpError', args: { status: response.status } })
        return
      }
      const payload = (await response.json()) as {
        tag_name?: string
        published_at?: string
        html_url?: string
      }
      setRelease({
        tag: payload.tag_name ?? '',
        publishedAt: payload.published_at ?? null,
        url: payload.html_url ?? RELEASES_PAGE,
      })
    } catch {
      setReleaseError({ key: 'system.updates.networkError' })
    } finally {
      setChecking(false)
    }
  }

  const componentColumns: Array<Column<ComponentRow>> = [
    {
      id: 'name',
      header: t('system.column.component'),
      mono: true,
      cell: (row) => row.name,
      sortValue: (row) => row.name,
    },
    {
      id: 'health',
      header: t('system.column.health'),
      cell: (row) => <StatusBadge kind="health" value={row.health} />,
      sortValue: (row) => row.health,
      width: '150px',
    },
    {
      id: 'latency',
      header: t('system.column.latency'),
      align: 'right',
      mono: true,
      cell: (row) => (row.latencyMs === null ? <span className="u-muted">—</span> : formatters.latency(row.latencyMs)),
      sortValue: (row) => row.latencyMs,
      width: '130px',
    },
    {
      id: 'detail',
      header: t('system.column.detail'),
      cell: (row) =>
        row.warmContexts !== null ? (
          <span className="u-secondary">{t('system.warmContexts', { count: row.warmContexts })}</span>
        ) : row.detail ? (
          <span className="u-mono u-muted">{row.detail}</span>
        ) : row.notConfigured ? (
          <span className="u-muted">{t('system.notConfigured')}</span>
        ) : (
          <span className="u-muted">—</span>
        ),
    },
  ]

  const storageColumns: Array<Column<StorageRow>> = [
    {
      id: 'table',
      header: t('system.column.table'),
      mono: true,
      cell: (row) => row.table,
      sortValue: (row) => row.table,
    },
    {
      id: 'rows',
      header: t('system.column.rows'),
      align: 'right',
      mono: true,
      cell: (row) => formatters.number(row.rows),
      sortValue: (row) => row.rows,
      width: '160px',
    },
    {
      id: 'compact',
      header: t('system.column.rowsCompact'),
      align: 'right',
      mono: true,
      cell: (row) => formatters.compact(row.rows),
      width: '140px',
      hideOnMobile: true,
    },
  ]

  const dbSize = (() => {
    const value = status.data?.storage?.['db_size_bytes']
    return typeof value === 'number' ? value : null
  })()

  return (
    <div className="u-page">
      <PageHeader
        title={t('page.system.title')}
        description={t('page.system.description')}
        badge={
          status.data ? (
            <span className="u-row u-xs u-muted" style={{ gap: 'var(--space-2)' }}>
              <span className="u-mono">{status.data.version}</span>
              {status.data.commit ? <CopyableId value={status.data.commit} length={10} /> : null}
            </span>
          ) : null
        }
        actions={
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshIcon />}
            loading={status.isFetching && !status.isLoading}
            onClick={() => {
              void invalidate(STATUS_KEY)
            }}
          >
            {t('common:action.refresh')}
          </Button>
        }
      />

      {status.isError ? (
        <Card>
          <ErrorState
            error={status.error}
            onRetry={() => {
              void status.refetch()
            }}
          />
        </Card>
      ) : (
        <>
          <div className="u-grid-metrics">
            <MetricTile
              label={t('system.metric.version')}
              value={status.isLoading ? '' : status.data?.version}
              loading={status.isLoading}
              footer={
                <span className="u-xs u-muted">
                  {status.data?.settings_version === null || status.data?.settings_version === undefined
                    ? null
                    : t('system.metric.settingsVersion', { version: status.data.settings_version })}
                </span>
              }
            />
            <MetricTile
              label={t('system.metric.uptime')}
              value={status.isLoading ? '' : formatters.duration((status.data?.uptime_seconds ?? 0) * 1000)}
              loading={status.isLoading}
            />
            <MetricTile
              label={t('system.metric.activeIdentities')}
              value={status.isLoading ? '' : formatters.number(activeIdentities(status.data?.pool))}
              loading={status.isLoading}
            />
            <MetricTile
              label={t('system.metric.dbSize')}
              value={status.isLoading ? '' : formatters.bytes(dbSize)}
              loading={status.isLoading}
            />
          </div>

          <Card
            title={t('system.components.title')}
            description={t('system.components.description')}
            flush
          >
            <DataTable
              columns={componentColumns}
              rows={components}
              getRowId={(row) => row.name}
              loading={status.isLoading}
              storageKey="system-components"
              flashValue={(row) => row.health}
              emptyTitle={t('system.components.emptyTitle')}
              emptyDescription={t('system.components.emptyDescription')}
              caption={t('system.components.title')}
            />
          </Card>

          <Card title={t('system.drift.title')} description={t('system.drift.description')}>
            <div className="u-stack">
              <div className="u-row u-wrap" style={{ gap: 'var(--space-3)', alignItems: 'stretch' }}>
                <VersionCell
                  label={t('system.drift.chromium')}
                  value={
                    status.isLoading ? (
                      <Skeleton width={48} height={22} />
                    ) : chromiumMajor === null ? (
                      <span className="u-muted">—</span>
                    ) : (
                      chromiumMajor
                    )
                  }
                />
                <VersionCell
                  label={t('system.drift.wreq')}
                  value={
                    status.isLoading ? (
                      <Skeleton width={48} height={22} />
                    ) : wreqMajor === null ? (
                      <span className="u-muted">—</span>
                    ) : (
                      wreqMajor
                    )
                  }
                />
              </div>
              {status.isLoading ? null : drift ? (
                <Banner tone="caution" icon={<AlertIcon size={14} />}>
                  {t('system.drift.warning', { chromium: chromiumMajor, wreq: wreqMajor })}
                </Banner>
              ) : chromiumMajor === null || wreqMajor === null ? (
                <Banner tone="accent" icon={<ServerIcon size={14} />}>
                  {t('system.drift.unknown')}
                </Banner>
              ) : (
                <Banner tone="success" icon={<CheckIcon size={14} />}>
                  {t('system.drift.aligned', { major: chromiumMajor })}
                </Banner>
              )}
            </div>
          </Card>

          <Card
            title={t('system.storage.title')}
            description={t('system.storage.description', { size: formatters.bytes(dbSize) })}
            flush
          >
            <DataTable
              columns={storageColumns}
              rows={storageRows}
              getRowId={(row) => row.table}
              loading={status.isLoading}
              storageKey="system-storage"
              defaultSort={{ columnId: 'rows', direction: 'desc' }}
              emptyTitle={t('system.storage.emptyTitle')}
              emptyDescription={t('system.storage.emptyDescription')}
              caption={t('system.storage.title')}
            />
          </Card>

          <Card title={t('system.updates.title')} description={t('system.updates.description')}>
            <div className="u-stack">
              <Banner tone="accent" icon={<GlobeIcon size={14} />}>
                {t('system.updates.outbound')}
              </Banner>
              {settings.isError ? (
                <ErrorState
                  compact
                  error={settings.error}
                  onRetry={() => {
                    void settings.refetch()
                  }}
                />
              ) : (
                <Switch
                  checked={checkUpdates}
                  disabled={!canWrite || settings.isLoading || saveCheckUpdates.isPending}
                  label={t('system.updates.toggle')}
                  hint={t('system.updates.toggleHint')}
                  onChange={(event) => {
                    saveCheckUpdates.mutate(event.target.checked)
                  }}
                />
              )}
              <div className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<ExternalIcon />}
                  disabled={!checkUpdates || checking}
                  loading={checking}
                  onClick={() => {
                    void runUpdateCheck()
                  }}
                >
                  {t('system.updates.check')}
                </Button>
                <span className="u-xs u-muted">
                  <span className="u-mono">{CHECK_UPDATES_KEY}</span>
                </span>
              </div>
              {releaseError ? (
                <Banner tone="caution" icon={<AlertIcon size={14} />}>
                  {t(releaseError.key, releaseError.args)}
                </Banner>
              ) : release ? (
                <Banner
                  tone={sameVersion(release.tag, status.data?.version) ? 'success' : 'caution'}
                  icon={
                    sameVersion(release.tag, status.data?.version) ? (
                      <CheckIcon size={14} />
                    ) : (
                      <AlertIcon size={14} />
                    )
                  }
                >
                  <div className="u-stack-sm">
                    <span>
                      {sameVersion(release.tag, status.data?.version)
                        ? t('system.updates.upToDate', { tag: release.tag })
                        : t('system.updates.latest', { tag: release.tag })}
                      {release.publishedAt ? ` · ${formatters.date(release.publishedAt)}` : null}
                    </span>
                    <a href={release.url} target="_blank" rel="noreferrer noopener">
                      {t('system.updates.openReleases')}
                    </a>
                  </div>
                </Banner>
              ) : null}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
