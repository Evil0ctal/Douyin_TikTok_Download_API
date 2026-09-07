import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertIcon,
  ArchiveIcon,
  Button,
  Card,
  CheckIcon,
  ConfirmDialog,
  DataTable,
  ErrorState,
  IdCardIcon,
  KeyIcon,
  LockIcon,
  MinusIcon,
  PageHeader,
  RefreshIcon,
  Switch,
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate, useSession } from '@/hooks'
import { apiPost, isApiError, waitForTask } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'

/**
 * Backup and restore.
 *
 * Two properties of the archive drive this whole page. Credentials are exported
 * as ciphertext and the master key is not in the archive, so a restore onto an
 * instance with a different DTK_SECRET_KEY cannot decrypt anything - and that
 * has to be said in words, not surfaced as a generic failure. And identities are
 * excluded by default because they are bound to a proxy and an egress address;
 * reviving them behind a new exit IP is exactly the thing the pool design
 * forbids (docs/design/15-operations.md, docs/design/02-identity-pool.md).
 */

const BACKUP_KEY = ['admin', 'backups'] as const

interface Manifest {
  schema_version: number
  created_at: string
  dtk_version: string
  include_identities: boolean
  key_check: string
  contents?: Record<string, number> | null
}

interface BackupEntry {
  path: string
  size_bytes: number
  manifest?: Manifest | null
  error?: string | null
}

/** The list endpoint may answer with a bare array or an envelope around one. */
type BackupList = BackupEntry[] | { backups?: BackupEntry[]; items?: BackupEntry[] }

interface CreateResult {
  task_id?: string
  path?: string
  size_bytes?: number
  rows?: number
  note?: string
}

function toEntries(payload: BackupList | undefined): BackupEntry[] {
  if (!payload) return []
  if (Array.isArray(payload)) return payload
  return payload.backups ?? payload.items ?? []
}

function basename(path: string): string {
  const parts = path.split('/')
  return parts[parts.length - 1] ?? path
}

function totalRows(manifest: Manifest | null | undefined): number | null {
  if (!manifest?.contents) return null
  return Object.values(manifest.contents).reduce((sum, value) => sum + value, 0)
}

/**
 * A restore refused because of the master key is not a generic bad request. The
 * server raises SecretKeyMismatch as INVALID_PARAM and names the archive's
 * creation time in the details; either signal is enough to explain it properly.
 */
function looksLikeKeyMismatch(error: unknown): boolean {
  if (!isApiError(error)) return false
  if (error.code !== 'INVALID_PARAM') return false
  if (typeof error.details['created_at'] === 'string') return true
  return /secret[_ ]?key/i.test(error.message)
}

function Banner({
  tone,
  icon,
  children,
}: {
  tone: 'accent' | 'caution' | 'danger'
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

function IdentityTag({ included }: { included: boolean }) {
  const { t } = useTranslation('console')
  return (
    <span
      className="u-nowrap"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
        color: included ? 'var(--caution)' : 'var(--text-muted)',
        fontSize: 'var(--text-xs)',
        lineHeight: 'var(--leading-xs)',
      }}
    >
      {included ? <IdCardIcon size={11} /> : <MinusIcon size={11} />}
      {included ? t('backup.identities.included') : t('backup.identities.excluded')}
    </span>
  )
}

export default function Backup() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()
  const session = useSession()

  const [includeIdentities, setIncludeIdentities] = useState(false)
  const [restoring, setRestoring] = useState<BackupEntry | null>(null)
  const [restoreError, setRestoreError] = useState<unknown>(null)
  const [lastRestore, setLastRestore] = useState<string | null>(null)

  const role = session.data?.role ?? null
  // Backup and restore are administrator work: the archive carries every table.
  const canManage = role === null || role === 'admin'

  const query = useApiQuery<BackupList>({
    key: BACKUP_KEY,
    path: paths.backup.list,
    poll: POLL.slow,
  })

  const entries = useMemo(() => toEntries(query.data), [query.data])

  const create = useApiMutation<CreateResult, boolean>(
    async (withIdentities) => {
      const accepted = await apiPost<CreateResult>(
        paths.backup.create,
        { include_identities: withIdentities },
        { awaitTask: false },
      )
      if (accepted?.task_id) {
        const finished = await waitForTask<CreateResult | null>(accepted.task_id, {
          timeoutMs: 300_000,
        })
        return finished ?? accepted
      }
      return accepted
    },
    {
      onSuccess: (result) => {
        void invalidate(BACKUP_KEY)
        toast.success(t('backup.toast.created'), {
          description: result?.path ? basename(result.path) : undefined,
        })
      },
      onError: (error) => {
        toast.apiError(error, t('backup.toast.createFailed'))
      },
    },
  )

  const restore = useApiMutation<unknown, string>(
    async (path) => {
      const accepted = await apiPost<{ task_id?: string }>(
        paths.backup.restore,
        { path },
        { awaitTask: false },
      )
      if (accepted?.task_id) return waitForTask(accepted.task_id, { timeoutMs: 300_000 })
      return accepted
    },
    {
      onSuccess: (_result, path) => {
        setRestoring(null)
        setRestoreError(null)
        setLastRestore(basename(path))
        void invalidate(BACKUP_KEY)
        toast.success(t('backup.toast.restored', { name: basename(path) }))
      },
      onError: (error) => {
        setRestoring(null)
        setRestoreError(error)
        if (!looksLikeKeyMismatch(error)) toast.apiError(error, t('backup.toast.restoreFailed'))
      },
    },
  )

  const columns: Array<Column<BackupEntry>> = [
    {
      id: 'name',
      header: t('backup.column.file'),
      mono: true,
      cell: (row) => (
        <span className="u-truncate" title={row.path}>
          {basename(row.path)}
        </span>
      ),
      sortValue: (row) => basename(row.path),
    },
    {
      id: 'created',
      header: t('backup.column.created'),
      mono: true,
      cell: (row) =>
        row.manifest ? formatters.dateTime(row.manifest.created_at) : <span className="u-muted">—</span>,
      sortValue: (row) => row.manifest?.created_at ?? '',
      width: '190px',
    },
    {
      id: 'version',
      header: t('backup.column.version'),
      mono: true,
      cell: (row) =>
        row.manifest ? (
          `${row.manifest.dtk_version} · schema ${row.manifest.schema_version}`
        ) : (
          <span className="u-muted">—</span>
        ),
      width: '180px',
      hideOnMobile: true,
    },
    {
      id: 'identities',
      header: t('backup.column.identities'),
      cell: (row) => <IdentityTag included={row.manifest?.include_identities ?? false} />,
      sortValue: (row) => String(row.manifest?.include_identities ?? false),
      width: '150px',
    },
    {
      id: 'rows',
      header: t('backup.column.rows'),
      align: 'right',
      mono: true,
      cell: (row) => {
        const rows = totalRows(row.manifest)
        return rows === null ? <span className="u-muted">—</span> : formatters.number(rows)
      },
      sortValue: (row) => totalRows(row.manifest),
      width: '120px',
    },
    {
      id: 'size',
      header: t('backup.column.size'),
      align: 'right',
      mono: true,
      cell: (row) => formatters.bytes(row.size_bytes),
      sortValue: (row) => row.size_bytes,
      width: '120px',
    },
    {
      id: 'key',
      header: t('backup.column.keyCheck'),
      mono: true,
      cell: (row) =>
        row.manifest ? (
          <span className="u-muted" title={row.manifest.key_check}>
            {row.manifest.key_check.slice(0, 8)}
          </span>
        ) : (
          <span className="u-muted">—</span>
        ),
      width: '120px',
      defaultHidden: true,
    },
    {
      id: 'actions',
      header: <span className="u-sr-only">{t('common:action.more')}</span>,
      hideable: false,
      align: 'right',
      cell: (row) =>
        row.error ? (
          <span className="u-xs" style={{ color: 'var(--danger)' }}>
            {row.error}
          </span>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            disabled={!canManage || restore.isPending}
            onClick={() => {
              setRestoreError(null)
              setRestoring(row)
            }}
          >
            {t('backup.action.restore')}
          </Button>
        ),
      width: '130px',
    },
  ]

  return (
    <div className="u-stack-lg">
      <PageHeader
        title={t('page.backup.title')}
        description={t('page.backup.description')}
        actions={
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshIcon />}
            loading={query.isFetching && !query.isLoading}
            onClick={() => {
              void invalidate(BACKUP_KEY)
            }}
          >
            {t('common:action.refresh')}
          </Button>
        }
      />

      <Banner tone="accent" icon={<KeyIcon size={14} />}>
        {t('backup.keyExplainer')}
      </Banner>

      <Card title={t('backup.create.title')} description={t('backup.create.description')}>
        <div className="u-stack">
          <Switch
            checked={includeIdentities}
            disabled={!canManage || create.isPending}
            label={t('backup.create.includeIdentities')}
            hint={t('backup.create.includeIdentitiesHint')}
            onChange={(event) => {
              setIncludeIdentities(event.target.checked)
            }}
          />
          {includeIdentities ? (
            <Banner tone="caution" icon={<AlertIcon size={14} />}>
              {t('backup.create.includeIdentitiesWarning')}
            </Banner>
          ) : null}
          <div className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
            <Button
              variant="primary"
              icon={<ArchiveIcon />}
              disabled={!canManage}
              loading={create.isPending}
              onClick={() => {
                create.mutate(includeIdentities)
              }}
            >
              {t('backup.action.create')}
            </Button>
            {!canManage ? (
              <span className="u-xs u-muted">{t('backup.adminOnly')}</span>
            ) : (
              <span className="u-xs u-muted">{t('backup.create.hint')}</span>
            )}
          </div>
          {create.isError ? <ErrorState compact error={create.error} /> : null}
        </div>
      </Card>

      {restoreError ? (
        <Card>
          {looksLikeKeyMismatch(restoreError) ? (
            <Banner tone="danger" icon={<LockIcon size={14} />}>
              <div className="u-stack-sm">
                <strong style={{ color: 'var(--danger)' }}>{t('backup.keyMismatch.title')}</strong>
                <span>{t('backup.keyMismatch.body')}</span>
                <span className="u-xs u-muted">{t('backup.keyMismatch.action')}</span>
              </div>
            </Banner>
          ) : (
            <ErrorState
              error={restoreError}
              title={t('backup.toast.restoreFailed')}
            />
          )}
        </Card>
      ) : null}

      {lastRestore ? (
        <Banner tone="accent" icon={<CheckIcon size={14} />}>
          {t('backup.restoreDone', { name: lastRestore })}
        </Banner>
      ) : null}

      <Card title={t('backup.list.title')} description={t('backup.list.description')} flush>
        <DataTable
          columns={columns}
          rows={entries}
          getRowId={(row) => row.path}
          loading={query.isLoading}
          error={query.isError ? query.error : undefined}
          onRetry={() => {
            void query.refetch()
          }}
          storageKey="backups"
          defaultSort={{ columnId: 'created', direction: 'desc' }}
          emptyTitle={t('backup.empty.title')}
          emptyDescription={t('backup.empty.description')}
          emptyAction={
            <Button
              variant="primary"
              size="sm"
              icon={<ArchiveIcon />}
              disabled={!canManage}
              loading={create.isPending}
              onClick={() => {
                create.mutate(includeIdentities)
              }}
            >
              {t('backup.action.create')}
            </Button>
          }
          caption={t('backup.list.title')}
        />
      </Card>

      <ConfirmDialog
        open={restoring !== null}
        danger
        loading={restore.isPending}
        confirmPhrase={restoring ? basename(restoring.path) : undefined}
        title={t('backup.confirm.title')}
        description={t('backup.confirm.description')}
        confirmLabel={t('backup.action.restore')}
        onCancel={() => {
          setRestoring(null)
        }}
        onConfirm={() => {
          if (restoring) restore.mutate(restoring.path)
        }}
      >
        <div className="u-stack-sm">
          <Banner tone="danger" icon={<AlertIcon size={14} />}>
            {t('backup.confirm.warning')}
          </Banner>
          {restoring?.manifest ? (
            <p className="u-xs u-muted u-mono" style={{ margin: 0 }}>
              {restoring.manifest.dtk_version} · schema {restoring.manifest.schema_version} ·{' '}
              {formatters.dateTime(restoring.manifest.created_at)}
            </p>
          ) : null}
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('backup.confirm.keyReminder')}
          </p>
        </div>
      </ConfirmDialog>
    </div>
  )
}
