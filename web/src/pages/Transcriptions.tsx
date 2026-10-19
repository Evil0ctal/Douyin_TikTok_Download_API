import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'wouter'

import {
  Button,
  Card,
  CompactProgress,
  ConfirmDialog,
  CopyableId,
  DataTable,
  Drawer,
  ErrorState,
  PageHeader,
  Select,
  StatusBadge,
  TranscriberNotice,
  transcriberGuideHref,
  type Column,
  useToast,
  useTranscriberState,
} from '@/components'
import { Pager, SearchBox } from '@/components/ListControls'
import { useApiMutation, useApiQuery, useCopy, useFormatters, useInvalidate } from '@/hooks'
import { apiDelete, apiPost } from '@/lib/api'
import { asPlatform, downloadHref, libraryPostHref, patchParams } from '@/lib/contentLinks'
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
  /** Only on the detail read: the list leaves it out, since it grows with the job. */
  partial_text?: string
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
/** Rows per page: the API's own default, so the first page is what it always was. */
const PAGE_SIZE = 100
const LIVE = new Set<TranscriptionState>(['queued', 'running'])
/** The sidecar's stage names, which are identifiers and are translated here. */
const STAGES = new Set(['queued', 'decode', 'vad', 'asr', 'punctuation', 'normalization'])

function isLive(row: TranscriptionRow | undefined): boolean {
  return row !== undefined && LIVE.has(row.state)
}

function formatMediaDuration(milliseconds: number | null | undefined): string {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) {
    return MISSING
  }
  // Hours of their own: a two-hour recording read as "120:00".
  const totalSeconds = Math.round(Math.max(0, milliseconds) / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const clock = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  return hours > 0 ? `${hours}:${clock}` : clock
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
  const { t, i18n } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()
  const copy = useCopy()
  const transcriber = useTranscriberState()
  // Filters and the open drawer live in the URL, as on Downloads and the
  // Library, so the links between the three pages land on a row and a view.
  const [params, setParams] = useSearchParams()
  const setFilter = (patch: Record<string, string | null>): void => {
    setParams((prev) => patchParams(prev, patch), { replace: true })
  }
  const stateParam = params.get('state') ?? ''
  const stateFilter: TranscriptionState | '' = (
    TRANSCRIPTION_STATES as readonly string[]
  ).includes(stateParam)
    ? (stateParam as TranscriptionState)
    : ''
  const platformFilter: Platform | '' = asPlatform(params.get('platform'), PLATFORMS)
  const search = params.get('q') ?? ''
  const inspectingId = params.get('id')
  // Local, and tied to the filters it was turned under, so a new filter starts
  // at the first page without an effect to reset it.
  const filterKey = `${stateFilter}|${platformFilter}|${search}`
  const [paging, setPaging] = useState({ key: filterKey, offset: 0 })
  const offset = paging.key === filterKey ? paging.offset : 0
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [confirming, setConfirming] = useState<Confirmation>(null)

  const list = useApiQuery<TranscriptionList>({
    key: [...LIST_KEY, stateFilter, platformFilter, search, offset],
    path: paths.transcriptions.list,
    params: {
      ...(stateFilter ? { state: stateFilter } : {}),
      ...(platformFilter ? { platform: platformFilter } : {}),
      ...(search ? { q: search } : {}),
      limit: PAGE_SIZE,
      offset,
    },
    // Fast only while something is moving, as on Downloads: a page of settled
    // records polled every five seconds was a steady load for nothing.
    refetchInterval: (query) =>
      query.state.data?.items.some((row) => isLive(row)) ? POLL.fast : POLL.slow,
  })

  const inspecting = useApiQuery<TranscriptionRow>({
    key: ['transcriptions', 'detail', inspectingId],
    path: inspectingId ? paths.transcriptions.byId(inspectingId) : paths.transcriptions.list,
    enabled: Boolean(inspectingId),
    // Each read carries the whole transcript, so only while it can still change,
    // and not at all once it errored: a stale ?id= used to poll a 404 forever.
    refetchInterval: (query) =>
      query.state.status !== 'error' && isLive(query.state.data) ? POLL.fast : false,
  })

  const refresh = (): void => {
    void invalidate(LIST_KEY)
    if (inspectingId) void invalidate(['transcriptions', 'detail', inspectingId])
  }

  const openDetail = (id: string): void => {
    setFilter({ id })
  }

  const closeDetail = (): void => {
    setFilter({ id: null })
  }

  const cancel = useApiMutation<TranscriptionRow, string>(
    (id) => apiDelete(paths.transcriptions.byId(id)),
    {
      onSuccess: (result) => {
        setConfirming(null)
        refresh()
        // It can finish between opening the dialog and confirming, and the
        // server then leaves it alone; saying "cancelled" would be untrue.
        if (result.state === 'cancelled') toast.success(t('transcriptions.toast.cancelled'))
        else toast.info(t('transcriptions.toast.alreadySettled'))
      },
      onError: (error) => toast.apiError(error, t('transcriptions.toast.cancelFailed')),
    },
  )

  const remove = useApiMutation<{ deleted: number }, string[]>(
    (ids) => apiPost(paths.transcriptions.delete, { ids }),
    {
      onSuccess: (result, ids) => {
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
  // Acted on as shown. A selection made under one filter survived a change of
  // filter, and "Delete 3 selected" then deleted rows that were not on screen.
  const selectedRows = rows.filter((row) => selected.has(row.transcription_id))
  const selectedHasLive = selectedRows.some((row) => isLive(row))
  const stageLabel = (stage: string): string =>
    STAGES.has(stage) ? t(`transcriptions.stage.${stage}`) : stage
  const filtered = Boolean(search || stateFilter || platformFilter)
  // Off is the default, and every Transcribe button used to fail on it with
  // TRANSCRIBER_UNAVAILABLE; an empty page said to go and press one.
  const blocked = transcriber === 'off' || transcriber === 'down'

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
            {row.text_preview || row.error || MISSING}
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

      {/* With records on screen the empty state never shows, and they are
          still readable; what is gone is starting new ones, so say that. */}
      {(transcriber === 'off' || transcriber === 'down') && rows.length > 0 ? (
        <Card title={t(`transcriptions.transcriber.${transcriber}Title`)}>
          <TranscriberNotice state={transcriber} />
        </Card>
      ) : null}

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
        emptyTitle={
          filtered
            ? t('transcriptions.empty.filteredTitle')
            : blocked
              ? t(`transcriptions.transcriber.${transcriber}Title`)
              : t('transcriptions.empty.title')
        }
        emptyDescription={
          filtered
            ? t('transcriptions.empty.filteredDescription')
            : blocked
              ? t(`transcriptions.transcriber.${transcriber}Body`)
              : t('transcriptions.empty.description')
        }
        emptyAction={
          filtered ? undefined : transcriber === 'off' ? (
            <a href={transcriberGuideHref(i18n.language)} target="_blank" rel="noreferrer noopener">
              {t('transcriptions.transcriber.guide')}
            </a>
          ) : (
            <Link href="/downloads">{t('transcriptions.empty.action')}</Link>
          )
        }
        toolbar={
          <>
            <SearchBox
              value={search}
              label={t('transcriptions.filter.search')}
              placeholder={t('transcriptions.filter.searchPlaceholder')}
              onSearch={(value) => {
                setFilter({ q: value })
                setSelected(new Set())
              }}
            />
            <Select
              aria-label={t('transcriptions.column.state')}
              value={stateFilter}
              onChange={(event) => {
                setFilter({ state: event.target.value })
                setSelected(new Set())
              }}
            >
              <option value="">{t('transcriptions.filter.allStates')}</option>
              {TRANSCRIPTION_STATES.map((state) => (
                <option key={state} value={state}>{t(`common:state.transcription.${state}`)}</option>
              ))}
            </Select>
            <Select
              aria-label={t('transcriptions.column.platform')}
              value={platformFilter}
              onChange={(event) => {
                setFilter({ platform: event.target.value })
                setSelected(new Set())
              }}
            >
              <option value="">{t('transcriptions.filter.allPlatforms')}</option>
              {PLATFORMS.map((platform) => <option key={platform} value={platform}>{platform}</option>)}
            </Select>
            {selectedRows.length > 0 ? (
              <Button
                size="sm"
                variant="danger"
                disabled={selectedHasLive}
                title={selectedHasLive ? t('transcriptions.delete.liveHint') : undefined}
                onClick={() =>
                  setConfirming({
                    kind: 'delete',
                    ids: selectedRows.map((row) => row.transcription_id),
                  })
                }
              >
                {t('transcriptions.delete.selected', { count: selectedRows.length })}
              </Button>
            ) : null}
          </>
        }
      />

      <Pager
        offset={offset}
        pageSize={PAGE_SIZE}
        total={list.data?.total ?? 0}
        onOffset={(next) => {
          setPaging({ key: filterKey, offset: next })
          setSelected(new Set())
        }}
        range={(from, to, total) => t('transcriptions.page.range', { from, to, total })}
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
        {inspecting.error ? <ErrorState error={inspecting.error} compact /> : null}
        {detail ? (
          <div className="u-stack">
            <div className="u-row-between">
              <StatusBadge kind="transcription" value={detail.state} />
              <span className="u-mono u-xs u-muted">{formatMediaDuration(detail.duration_ms)}</span>
            </div>
            {detail.progress ? (
              <p className="u-xs u-muted">
                {detail.progress.progress_percent !== null
                  ? t('transcriptions.detail.progress', {
                      stage: stageLabel(detail.progress.stage),
                      percent: detail.progress.progress_percent,
                    })
                  : stageLabel(detail.progress.stage)}
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
            <div className={styles.transcript}>
              {detail.text || detail.progress?.partial_text || t('transcriptions.detail.noText')}
            </div>
            {!detail.text && detail.progress?.partial_text ? (
              <p className="u-xs u-muted">{t('transcriptions.detail.provisional')}</p>
            ) : null}
            {/* The video this came from, and the post it belongs to. The
                download used to be here only as an id to copy. The post link
                needs the archive to hold it, which is what `post` says. */}
            {detail.download_id || detail.post ? (
              <p className={styles.crossLinks}>
                {detail.download_id ? (
                  <Link href={downloadHref(detail.download_id)}>
                    {t('transcriptions.detail.openDownload')}
                  </Link>
                ) : null}
                {detail.post ? (
                  <Link href={libraryPostHref(detail.platform, detail.content_id)}>
                    {t('transcriptions.detail.openInLibrary')}
                  </Link>
                ) : null}
              </p>
            ) : null}
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
