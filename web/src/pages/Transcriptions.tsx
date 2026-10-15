import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  CompactProgress,
  ConfirmDialog,
  CopyableId,
  DataTable,
  Drawer,
  PageHeader,
  Select,
  StatusBadge,
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useCopy, useFormatters, useInvalidate } from '@/hooks'
import { apiDelete, apiPost } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { MISSING } from '@/lib/format'
import { POLL } from '@/lib/query'
import {
  PLATFORMS,
  TRANSCRIPTION_STATES,
  type Platform,
  type TranscriptionState,
} from '@/lib/types'

import styles from './Transcriptions.module.css'

interface Normalization {
  requested: boolean
  applied: boolean
  engine: string | null
  failure_policy: string | null
  error: string | null
}

interface Source {
  name: string
  sha256: string
  bytes: number
}

interface Progress {
  state: string
  stage: string
  revision: number
  processed_ms: number
  duration_ms: number | null
  progress_percent: number | null
  partial_text: string
}

interface TranscriptionRow {
  transcription_id: string
  download_id: string | null
  task_id: string | null
  platform: string
  content_id: string
  state: TranscriptionState
  text_preview?: string
  text?: string | null
  duration_ms: number | null
  model: string
  normalization: Normalization
  source: Source
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  post?: { title: string | null; author: string | null; duration_ms: number | null } | null
  progress?: Progress | null
}

interface TranscriptionList {
  items: TranscriptionRow[]
  total: number
  limit: number
  offset: number
}

type Confirmation = { kind: 'cancel' | 'delete'; ids: string[] } | null
const LIST_KEY = ['transcriptions'] as const
const LIVE = new Set<TranscriptionState>(['queued', 'running'])

function formatMediaDuration(milliseconds: number | null | undefined): string {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) {
    return MISSING
  }
  const totalSeconds = Math.round(Math.max(0, milliseconds) / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function selectedFromUrl(): string | null {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('id')
}

function saveText(row: TranscriptionRow): void {
  const href = URL.createObjectURL(new Blob([row.text ?? ''], { type: 'text/plain;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = `transcription-${row.platform}-${row.content_id}.txt`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(href), 1_000)
}

export default function Transcriptions() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()
  const copy = useCopy()
  const [stateFilter, setStateFilter] = useState<TranscriptionState | ''>('')
  const [platformFilter, setPlatformFilter] = useState<Platform | ''>('')
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [inspectingId, setInspectingId] = useState<string | null>(selectedFromUrl)
  const [confirming, setConfirming] = useState<Confirmation>(null)

  const list = useApiQuery<TranscriptionList>({
    key: [...LIST_KEY, stateFilter, platformFilter],
    path: paths.transcriptions.list,
    params: {
      ...(stateFilter ? { state: stateFilter } : {}),
      ...(platformFilter ? { platform: platformFilter } : {}),
    },
    poll: POLL.fast,
  })

  const inspecting = useApiQuery<TranscriptionRow>({
    key: ['transcriptions', 'detail', inspectingId],
    path: inspectingId ? paths.transcriptions.byId(inspectingId) : paths.transcriptions.list,
    enabled: Boolean(inspectingId),
    poll: inspectingId ? POLL.fast : false,
  })

  const refresh = (): void => {
    void invalidate(LIST_KEY)
    if (inspectingId) void invalidate(['transcriptions', 'detail', inspectingId])
  }

  const openDetail = (id: string): void => {
    setInspectingId(id)
    const url = new URL(window.location.href)
    url.searchParams.set('id', id)
    window.history.replaceState(null, '', url)
  }

  const closeDetail = (): void => {
    setInspectingId(null)
    const url = new URL(window.location.href)
    url.searchParams.delete('id')
    window.history.replaceState(null, '', url)
  }

  const cancel = useApiMutation<TranscriptionRow, string>(
    (id) => apiDelete(paths.transcriptions.byId(id)),
    {
      onSuccess: () => {
        setConfirming(null)
        refresh()
        toast.success(t('transcriptions.toast.cancelled'))
      },
      onError: (error) => toast.apiError(error, t('transcriptions.toast.cancelFailed')),
    },
  )

  const remove = useApiMutation<{ deleted: number }, string[]>(
    (ids) => apiPost(paths.transcriptions.delete, { ids }),
    {
      onSuccess: (result) => {
        const ids = confirming?.ids ?? []
        if (inspectingId && ids.includes(inspectingId)) closeDetail()
        setSelected(new Set())
        setConfirming(null)
        refresh()
        toast.success(t('transcriptions.toast.deleted', { count: result.deleted }))
      },
      onError: (error) => toast.apiError(error, t('transcriptions.toast.deleteFailed')),
    },
  )

  const rows = list.data?.items ?? []
  const selectedRows = rows.filter((row) => selected.has(row.transcription_id))
  const selectedHasLive = selectedRows.some((row) => LIVE.has(row.state))

  const columns = useMemo<Array<Column<TranscriptionRow>>>(
    () => [
      {
        id: 'content',
        header: t('transcriptions.column.content'),
        width: '360px',
        cell: (row) => (
          <div className={styles.content}>
            <span className="u-truncate" title={row.post?.title ?? row.content_id}>
              {row.post?.title || row.content_id}
            </span>
            <span
              className={`u-truncate ${styles.sub}`}
              title={row.post?.author ? `${row.post.author} · ${row.platform}` : row.platform}
            >
              {row.post?.author ? `${row.post.author} · ` : ''}{row.platform}
            </span>
          </div>
        ),
        sortValue: (row) => row.post?.title || row.content_id,
      },
      {
        id: 'state',
        header: t('transcriptions.column.state'),
        width: '190px',
        cell: (row) => (
          <div className="u-stack-sm" style={{ minWidth: 0 }}>
            <StatusBadge kind="transcription" value={row.state} />
            {row.progress?.progress_percent !== null && row.progress?.progress_percent !== undefined ? (
              <CompactProgress
                value={row.progress.progress_percent}
                max={100}
                label={`${formatters.number(row.progress.progress_percent, 0)}%`}
              />
            ) : null}
          </div>
        ),
        sortValue: (row) => row.state,
      },
      {
        id: 'preview',
        header: t('transcriptions.column.preview'),
        width: '360px',
        cell: (row) => (
          <span className={styles.preview} title={row.text_preview || row.error || ''}>
            {row.text_preview || row.error || '—'}
          </span>
        ),
      },
      {
        id: 'duration',
        header: t('transcriptions.column.duration'),
        width: '110px',
        mono: true,
        cell: (row) => formatMediaDuration(row.duration_ms),
        sortValue: (row) => row.duration_ms,
      },
      {
        id: 'model',
        header: t('transcriptions.column.model'),
        width: '210px',
        mono: true,
        defaultHidden: true,
        cell: (row) => row.model,
        sortValue: (row) => row.model,
      },
      {
        id: 'normalization',
        header: t('transcriptions.column.normalization'),
        width: '130px',
        cell: (row) =>
          row.normalization.requested
            ? row.normalization.applied
              ? t('transcriptions.normalization.applied')
              : t('transcriptions.normalization.requested')
            : t('transcriptions.normalization.off'),
      },
      {
        id: 'created',
        header: t('transcriptions.column.created'),
        width: '180px',
        mono: true,
        cell: (row) => formatters.dateTime(row.created_at),
        sortValue: (row) => row.created_at,
      },
      {
        id: 'finished',
        header: t('transcriptions.column.finished'),
        width: '180px',
        mono: true,
        cell: (row) => formatters.dateTime(row.finished_at),
        sortValue: (row) => row.finished_at,
      },
    ],
    [formatters, t],
  )

  const detail = inspecting.data
  const confirmIds = confirming?.ids ?? []

  return (
    <div className="u-page">
      <PageHeader
        title={t('transcriptions.title')}
        description={t('transcriptions.description')}
        actions={<Button variant="secondary" onClick={refresh}>{t('common:action.refresh')}</Button>}
      />

      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(row) => row.transcription_id}
        loading={list.isLoading}
        error={list.error}
        onRetry={() => void list.refetch()}
        storageKey="transcriptions"
        selectedIds={selected}
        onSelectionChange={setSelected}
        onRowClick={(row) => openDetail(row.transcription_id)}
        defaultSort={{ columnId: 'created', direction: 'desc' }}
        flashValue={(row) => row.state}
        emptyTitle={t('transcriptions.empty.title')}
        emptyDescription={t('transcriptions.empty.description')}
        toolbar={
          <>
            <Select
              aria-label={t('transcriptions.column.state')}
              value={stateFilter}
              onChange={(event) => setStateFilter(event.target.value as TranscriptionState | '')}
            >
              <option value="">{t('transcriptions.filter.allStates')}</option>
              {TRANSCRIPTION_STATES.map((state) => (
                <option key={state} value={state}>{t(`common:state.transcription.${state}`)}</option>
              ))}
            </Select>
            <Select
              aria-label={t('transcriptions.column.platform')}
              value={platformFilter}
              onChange={(event) => setPlatformFilter(event.target.value as Platform | '')}
            >
              <option value="">{t('transcriptions.filter.allPlatforms')}</option>
              {PLATFORMS.map((platform) => <option key={platform} value={platform}>{platform}</option>)}
            </Select>
            {selected.size > 0 ? (
              <Button
                size="sm"
                variant="danger"
                disabled={selectedHasLive}
                title={selectedHasLive ? t('transcriptions.delete.liveHint') : undefined}
                onClick={() => setConfirming({ kind: 'delete', ids: [...selected] })}
              >
                {t('transcriptions.delete.selected', { count: selected.size })}
              </Button>
            ) : null}
          </>
        }
      />

      <Drawer
        open={Boolean(inspectingId)}
        onClose={closeDetail}
        title={detail?.post?.title || detail?.content_id || t('transcriptions.detail.title')}
        description={detail ? `${detail.platform} · ${detail.transcription_id}` : undefined}
        footer={detail ? (
          <>
            {LIVE.has(detail.state) ? (
              <Button variant="danger" onClick={() => setConfirming({ kind: 'cancel', ids: [detail.transcription_id] })}>
                {t('transcriptions.cancel.action')}
              </Button>
            ) : (
              <Button variant="danger" onClick={() => setConfirming({ kind: 'delete', ids: [detail.transcription_id] })}>
                {t('common:action.delete')}
              </Button>
            )}
          </>
        ) : undefined}
      >
        {inspecting.isLoading ? <p className="u-muted">{t('transcriptions.detail.loading')}</p> : null}
        {inspecting.error ? <p className="u-muted">{inspecting.error.message}</p> : null}
        {detail ? (
          <div className="u-stack">
            <div className="u-row-between">
              <StatusBadge kind="transcription" value={detail.state} />
              <span className="u-mono u-xs u-muted">{formatMediaDuration(detail.duration_ms)}</span>
            </div>
            {detail.progress ? (
              <p className="u-xs u-muted">
                {t('transcriptions.detail.progress', {
                  stage: detail.progress.stage,
                  percent: detail.progress.progress_percent ?? 0,
                })}
              </p>
            ) : null}
            {detail.error ? <p className={styles.error}>{detail.error}</p> : null}
            <div className={styles.textActions}>
              <Button size="sm" variant="secondary" disabled={!detail.text} onClick={() => void copy.copy(detail.text ?? '')}>
                {copy.copied ? t('common:action.copied') : t('common:action.copy')}
              </Button>
              <Button size="sm" variant="secondary" disabled={!detail.text} onClick={() => saveText(detail)}>
                {t('transcriptions.detail.export')}
              </Button>
            </div>
            <div className={styles.transcript}>{detail.text || t('transcriptions.detail.noText')}</div>
            <dl className={styles.metadata}>
              <dt>{t('transcriptions.detail.model')}</dt><dd>{detail.model}</dd>
              <dt>{t('transcriptions.detail.source')}</dt><dd>{detail.source.name}</dd>
              <dt>{t('transcriptions.detail.sourceSize')}</dt><dd>{formatters.bytes(detail.source.bytes)}</dd>
              <dt>{t('transcriptions.detail.created')}</dt><dd>{formatters.timestamp(detail.created_at)}</dd>
              <dt>{t('transcriptions.detail.started')}</dt><dd>{formatters.timestamp(detail.started_at)}</dd>
              <dt>{t('transcriptions.detail.finished')}</dt><dd>{formatters.timestamp(detail.finished_at)}</dd>
            </dl>
            <CopyableId value={detail.source.sha256} label={t('transcriptions.detail.sha256')} wrap />
            {detail.download_id ? <CopyableId value={detail.download_id} label={t('transcriptions.detail.downloadId')} wrap /> : null}
            {detail.task_id ? <CopyableId value={detail.task_id} label={t('transcriptions.detail.taskId')} wrap /> : null}
          </div>
        ) : null}
      </Drawer>

      <ConfirmDialog
        open={confirming !== null}
        danger
        loading={cancel.isPending || remove.isPending}
        title={confirming?.kind === 'cancel' ? t('transcriptions.cancel.title') : t('transcriptions.delete.title', { count: confirmIds.length })}
        description={confirming?.kind === 'cancel' ? t('transcriptions.cancel.description') : t('transcriptions.delete.description')}
        confirmLabel={confirming?.kind === 'cancel' ? t('transcriptions.cancel.action') : t('common:action.delete')}
        onConfirm={() => {
          if (!confirming) return
          if (confirming.kind === 'cancel') cancel.mutate(confirming.ids[0] ?? '')
          else remove.mutate(confirming.ids)
        }}
        onCancel={() => setConfirming(null)}
      />
    </div>
  )
}
