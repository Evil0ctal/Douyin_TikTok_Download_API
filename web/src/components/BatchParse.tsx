import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useApiMutation, useFormatters } from '@/hooks'
import {
  apiPost,
  getTask,
  isApiError,
  isErrorCode,
  NON_RETRYABLE_CODES,
  type TaskEnvelope,
} from '@/lib/api'
import { paths } from '@/lib/endpoints'
import type { Platform, TaskState } from '@/lib/types'

import { Button } from './Button'
import { Card } from './Card'
import { CodeBlock } from './CodeBlock'
import { CopyableId } from './CopyableId'
import { DataTable, type Column } from './DataTable'
import { Drawer } from './Drawer'
import { ErrorState } from './ErrorState'
import { ErrorCodeBadge, StatusBadge } from './StatusBadge'
import { Textarea } from './Textarea'
import { useToast } from './Toast'

/**
 * Paste many links, parse them all, keep what came back.
 *
 * This was its own page and is now a mode of the downloads page, because the
 * two answer one question - "I have some links, get me the videos" - and
 * splitting it across two menu entries meant picking the wrong one first.
 *
 * The split that matters: submission is batched, tracking is not. One round
 * trip hands the server N links and gets N task ids back, and from then on each
 * row lives its own life - its own state, its own error, its own retry. A
 * synchronous bulk endpoint would smear one dead link into a single error for
 * the whole paste (docs/design/07-frontend.md).
 *
 * There are two different downloads here and they are not interchangeable.
 *
 * **To this computer**, from the drawer: browser-to-CDN, so the file lands in
 * the reader's own downloads folder and the server never relays media - that
 * costs bandwidth, turns the instance into an open proxy, and a signed CDN link
 * is likelier to work from a browser than from a datacentre. The cost is that
 * some CDNs refuse cross-origin reads, so the mirrors are tried in order and a
 * blocked download says so in words instead of failing silently.
 *
 * **To the instance**, from the toolbar: the same request the single-post mode
 * makes, so these rows land in the archive with the progress, retry, dedupe and
 * skip-what-is-already-here that everything else on the page has. This is what
 * "batch parse and download" means, and it is the reason a parse result knows
 * its platform and content id at all.
 */

/* -------------------------------------------------------------------------- */
/* Result shapes (docs/design/11-data-contracts.md)                            */
/* -------------------------------------------------------------------------- */

interface MediaImage {
  url: string
  urls?: string[]
  width?: number | null
  height?: number | null
}

interface VideoStream {
  url: string
  urls?: string[]
  width?: number | null
  height?: number | null
  bitrate?: number | null
  format?: string | null
  size_bytes?: number | null
  watermark?: boolean
}

interface MediaLike {
  covers?: MediaImage[]
  video?: VideoStream | null
  streams?: VideoStream[]
  images?: MediaImage[]
}

interface AuthorLike {
  uid?: string
  unique_id?: string | null
  nickname?: string
  web_url?: string
  avatar?: MediaImage | null
}

interface StatsLike {
  play_count?: number | null
  digg_count?: number | null
  comment_count?: number | null
  share_count?: number | null
  collect_count?: number | null
}

interface ContentLike {
  platform?: string
  content_id?: string
  kind?: string
  web_url?: string
  title?: string
  description?: string
  created_at?: string | null
  duration_ms?: number | null
  author?: AuthorLike | null
  stats?: StatsLike | null
  media?: MediaLike | null
  tags?: string[]
}

function asContent(data: unknown): ContentLike | null {
  if (!data || typeof data !== 'object') return null
  const record = data as Record<string, unknown>
  if (typeof record['content_id'] === 'string') return record as ContentLike
  // The front door also answers with an author for a profile link.
  if (typeof record['uid'] === 'string' && typeof record['nickname'] === 'string') {
    const author = record as AuthorLike
    return { platform: String(record['platform'] ?? ''), title: author.nickname, author }
  }
  return null
}

/** Every mirror the platform offered, de-duplicated, best first. */
function mirrorsOf(source: MediaImage | VideoStream | null | undefined): string[] {
  if (!source) return []
  const all = [source.url, ...(source.urls ?? [])].filter(
    (entry): entry is string => typeof entry === 'string' && entry.length > 0,
  )
  return [...new Set(all)]
}

function videoMirrors(content: ContentLike | null): string[] {
  if (!content?.media) return []
  const primary = mirrorsOf(content.media.video)
  const alternates = (content.media.streams ?? []).flatMap((stream) => mirrorsOf(stream))
  return [...new Set([...primary, ...alternates])]
}

function coverMirrors(content: ContentLike | null): string[] {
  return mirrorsOf(content?.media?.covers?.[0])
}

/* -------------------------------------------------------------------------- */
/* Row state                                                                   */
/* -------------------------------------------------------------------------- */

type RowState = 'pending' | TaskState

interface RowError {
  code: string
  message: string
}

interface ParseRow {
  id: string
  url: string
  taskId: string | null
  state: RowState
  error: RowError | null
  data: unknown
  submittedAt: number
  finishedAt: number | null
  attempts: number
}

interface BatchItemResult {
  url: string
  task_id?: string | null
  state?: TaskState
  error?: { code?: string; message?: string; details?: Record<string, unknown> | null } | null
}

interface BatchResponse {
  items?: BatchItemResult[]
  submitted?: number
  rejected?: number
}

const TERMINAL: ReadonlySet<RowState> = new Set<RowState>(['done', 'failed'])

/** Server ceiling for one batch (MAX_BATCH_ITEMS); longer pastes are chunked. */
const BATCH_CHUNK = 50
const MAX_LINKS = 500
const POLL_INTERVAL_MS = 1_500
const POLL_CONCURRENCY = 8

let rowSequence = 0

function nextRowId(): string {
  rowSequence += 1
  return `row-${rowSequence}`
}

/** Splits a paste into links: one per line, or several per line, or comma separated. */
function splitLinks(input: string): { urls: string[]; duplicates: number } {
  const seen = new Set<string>()
  const urls: string[] = []
  let duplicates = 0

  for (const candidate of input.split(/[\s,;]+/)) {
    const value = candidate.trim()
    if (!value) continue
    if (seen.has(value)) {
      duplicates += 1
      continue
    }
    seen.add(value)
    urls.push(value)
  }
  return { urls, duplicates }
}

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = []
  for (let index = 0; index < items.length; index += size) {
    out.push(items.slice(index, index + size))
  }
  return out
}

/* -------------------------------------------------------------------------- */
/* Export                                                                      */
/* -------------------------------------------------------------------------- */

interface ExportField {
  id: string
  value: (row: ParseRow, content: ContentLike | null) => string | number | null
}

const EXPORT_FIELDS: readonly ExportField[] = [
  { id: 'url', value: (row) => row.url },
  { id: 'state', value: (row) => row.state },
  { id: 'error_code', value: (row) => row.error?.code ?? null },
  { id: 'platform', value: (_row, content) => content?.platform ?? null },
  { id: 'kind', value: (_row, content) => content?.kind ?? null },
  { id: 'content_id', value: (_row, content) => content?.content_id ?? null },
  { id: 'web_url', value: (_row, content) => content?.web_url ?? null },
  { id: 'title', value: (_row, content) => content?.title ?? null },
  { id: 'author', value: (_row, content) => content?.author?.nickname ?? null },
  { id: 'author_uid', value: (_row, content) => content?.author?.uid ?? null },
  { id: 'created_at', value: (_row, content) => content?.created_at ?? null },
  { id: 'duration_ms', value: (_row, content) => content?.duration_ms ?? null },
  { id: 'play_count', value: (_row, content) => content?.stats?.play_count ?? null },
  { id: 'digg_count', value: (_row, content) => content?.stats?.digg_count ?? null },
  { id: 'comment_count', value: (_row, content) => content?.stats?.comment_count ?? null },
  { id: 'share_count', value: (_row, content) => content?.stats?.share_count ?? null },
  { id: 'collect_count', value: (_row, content) => content?.stats?.collect_count ?? null },
  { id: 'video_url', value: (_row, content) => videoMirrors(content)[0] ?? null },
]

/** Quotes for CSV and defuses a leading formula character. */
function csvCell(value: string | number | null): string {
  if (value === null) return ''
  const text = String(value)
  const guarded = /^[=+\-@]/.test(text) ? `'${text}` : text
  return /[",\n\r]/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded
}

function saveBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // Revoked on the next tick: Safari cancels an in-flight save otherwise.
  window.setTimeout(() => {
    URL.revokeObjectURL(href)
  }, 1_000)
}

function fileStamp(): string {
  return new Date().toISOString().replace(/[:.]/g, '-')
}

/* -------------------------------------------------------------------------- */
/* Downloads                                                                   */
/* -------------------------------------------------------------------------- */

type DownloadStatus = 'idle' | 'trying' | 'done' | 'blocked'

interface DownloadState {
  status: DownloadStatus
  mirror: number
  total: number
}

function filenameFor(url: string, fallback: string): string {
  try {
    const parsed = new URL(url)
    const last = parsed.pathname.split('/').filter(Boolean).pop()
    if (last && /\.[a-z0-9]{2,5}$/i.test(last)) return last
  } catch {
    // A relative or malformed URL just gets the fallback name.
  }
  return fallback
}

export interface BatchParseProps {
  /**
   * Whether queuing a row should join what is already downloaded rather than
   * fetching it again. Owned by the page, because it is the same choice the
   * other modes make and one checkbox for all of them is the point of it.
   */
  skipExisting: boolean
  /** Called after rows are queued, so the download list can refresh itself. */
  onQueued: () => void
}

export function BatchParse({ skipExisting, onQueued }: BatchParseProps) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()

  const [input, setInput] = useState('')
  const [rows, setRows] = useState<ParseRow[]>([])
  const [preview, setPreview] = useState<string | null>(null)
  const [downloads, setDownloads] = useState<Record<string, DownloadState>>({})
  const trackingRef = useRef(false)
  const cursorRef = useRef(0)

  const parsed = useMemo(() => splitLinks(input), [input])

  const submit = useApiMutation<ParseRow[], string[]>(
    async (urls) => {
      const created: ParseRow[] = []
      for (const group of chunk(urls, BATCH_CHUNK)) {
        const response = await apiPost<BatchResponse>(
          paths.tasks.batch,
          { items: group.map((url) => ({ url, include_raw: false })) },
          { awaitTask: false },
        )
        const items = response.items ?? []
        for (const [index, url] of group.entries()) {
          const item = items[index] ?? items.find((entry) => entry.url === url)
          const taskId = item?.task_id ?? null
          created.push({
            id: nextRowId(),
            url,
            taskId,
            state: taskId ? (item?.state ?? 'queued') : 'failed',
            error: taskId
              ? null
              : {
                  code: item?.error?.code ?? 'INTERNAL',
                  message: item?.error?.message ?? t('parse.notSubmitted'),
                },
            data: null,
            submittedAt: Date.now(),
            finishedAt: taskId ? null : Date.now(),
            attempts: 1,
          })
        }
      }
      return created
    },
    {
      onSuccess: (created) => {
        setRows((current) => [...created, ...current])
        setInput('')
      },
      onError: (error) => {
        toast.apiError(error, t('parse.submitFailed'))
      },
    },
  )

  /* --------------------------------------------------------- tracking ----- */

  useEffect(() => {
    const pending = rows.filter(
      (row): row is ParseRow & { taskId: string } =>
        row.taskId !== null && !TERMINAL.has(row.state),
    )
    if (pending.length === 0 || trackingRef.current) return

    const timer = window.setTimeout(() => {
      trackingRef.current = true
      // Round robin rather than always the first N: with a long paste the head
      // of the list would otherwise be polled forever and the tail never.
      const start = cursorRef.current % pending.length
      const slice = [...pending.slice(start), ...pending.slice(0, start)].slice(
        0,
        POLL_CONCURRENCY,
      )
      cursorRef.current = start + slice.length

      void Promise.all(
        slice.map(async (row) => {
          try {
            const task = await getTask<unknown>(row.taskId)
            return { id: row.id, taskId: row.taskId, task, failure: null as unknown }
          } catch (error) {
            return { id: row.id, taskId: row.taskId, task: null, failure: error as unknown }
          }
        }),
      ).then((updates) => {
        trackingRef.current = false

        setRows((current) =>
          current.map((row) => {
            // Matching on the task id as well as the row id drops a late answer
            // for a task the row has already been retried away from.
            const update = updates.find(
              (entry) => entry.id === row.id && entry.taskId === row.taskId,
            )
            if (!update) return row

            if (update.failure) {
              // A lookup that fails is not the task failing: keep polling unless
              // the task itself is gone.
              const gone = isApiError(update.failure) && update.failure.code === 'TASK_NOT_FOUND'
              if (!gone) return row
              return {
                ...row,
                state: 'failed',
                finishedAt: Date.now(),
                error: { code: 'TASK_NOT_FOUND', message: t('parse.taskGone') },
              }
            }

            const task = update.task
            if (!task) return row
            if (task.state === 'done') {
              return {
                ...row,
                state: 'done',
                data: task.data ?? null,
                error: null,
                finishedAt: Date.now(),
              }
            }
            if (task.state === 'failed') {
              return {
                ...row,
                state: 'failed',
                finishedAt: Date.now(),
                error: {
                  code: task.error?.code ?? 'INTERNAL',
                  message: task.error?.message ?? t('parse.taskFailed'),
                },
              }
            }
            return { ...row, state: task.state }
          }),
        )
      })
    }, POLL_INTERVAL_MS)

    return () => {
      window.clearTimeout(timer)
    }
  }, [rows, t])

  /* ------------------------------------------------------------ actions --- */

  const retryRow = useCallback(
    async (row: ParseRow): Promise<void> => {
      setRows((current) =>
        current.map((entry) =>
          entry.id === row.id
            ? {
                ...entry,
                state: 'pending',
                error: null,
                data: null,
                taskId: null,
                finishedAt: null,
                attempts: entry.attempts + 1,
              }
            : entry,
        ),
      )

      try {
        const response = await apiPost<TaskEnvelope<unknown>>(
          paths.parse,
          { url: row.url, include_raw: false },
          { awaitTask: false },
        )
        // A coalesced or cached submission can come back finished, without a
        // task to follow; anything else gets tracked like a fresh row.
        const taskId = response.task_id ?? null
        const state: RowState = taskId ? (response.state ?? 'queued') : 'done'
        setRows((current) =>
          current.map((entry) =>
            entry.id === row.id
              ? {
                  ...entry,
                  taskId,
                  state,
                  data: taskId ? (response.data ?? null) : response,
                  finishedAt: state === 'done' ? Date.now() : null,
                }
              : entry,
          ),
        )
      } catch (error) {
        const code = isApiError(error) ? error.code : 'INTERNAL'
        const message = error instanceof Error ? error.message : String(error)
        setRows((current) =>
          current.map((entry) =>
            entry.id === row.id
              ? { ...entry, state: 'failed', finishedAt: Date.now(), error: { code, message } }
              : entry,
          ),
        )
      }
    },
    [],
  )

  /**
   * A dead link is not worth another identity: the error contract marks which
   * codes are permanent, and retrying those only burns pool capacity.
   */
  const retryable = useCallback((row: ParseRow): boolean => {
    if (row.state !== 'failed') return false
    const code = row.error?.code
    if (code && isErrorCode(code) && NON_RETRYABLE_CODES.has(code)) return false
    return true
  }, [])

  const exportRows = (kind: 'csv' | 'json'): void => {
    if (rows.length === 0) return

    if (kind === 'json') {
      const payload = rows.map((row) => ({
        url: row.url,
        state: row.state,
        task_id: row.taskId,
        error: row.error,
        data: row.data,
      }))
      saveBlob(
        new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }),
        `dtk-parse-${fileStamp()}.json`,
      )
      return
    }

    const header = EXPORT_FIELDS.map((field) => field.id).join(',')
    const lines = rows.map((row) => {
      const content = asContent(row.data)
      return EXPORT_FIELDS.map((field) => csvCell(field.value(row, content))).join(',')
    })
    saveBlob(
      new Blob([`${header}\n${lines.join('\n')}\n`], { type: 'text/csv;charset=utf-8' }),
      `dtk-parse-${fileStamp()}.csv`,
    )
  }

  const download = useCallback(
    async (key: string, mirrors: string[], fallbackName: string): Promise<void> => {
      if (mirrors.length === 0) return
      const total = mirrors.length

      for (const [index, url] of mirrors.entries()) {
        setDownloads((current) => ({
          ...current,
          [key]: { status: 'trying', mirror: index + 1, total },
        }))
        try {
          const response = await fetch(url, { mode: 'cors', credentials: 'omit' })
          if (!response.ok) throw new Error(String(response.status))
          const blob = await response.blob()
          saveBlob(blob, filenameFor(url, fallbackName))
          setDownloads((current) => ({
            ...current,
            [key]: { status: 'done', mirror: index + 1, total },
          }))
          return
        } catch {
          // CORS, hotlink protection or an expired signature. Try the next mirror.
          continue
        }
      }

      setDownloads((current) => ({ ...current, [key]: { status: 'blocked', mirror: total, total } }))
      const first = mirrors[0]
      if (first) window.open(first, '_blank', 'noopener,noreferrer')
      toast.warning(t('parse.downloadBlockedTitle'), {
        description: t('parse.downloadBlockedBody', { count: total }),
      })
    },
    [t, toast],
  )

  /**
   * Send parsed rows to the instance's downloader.
   *
   * Exactly the request a single post makes, once per row: the server decides
   * what "already here" means and joins anything in flight, so a paste that
   * overlaps yesterday's paste costs nothing and cannot race itself into the
   * same directory twice.
   *
   * One failure does not abandon the rest, and the counts reported at the end
   * are what actually happened rather than what was attempted - a paste of
   * fifty links where three posts have been deleted should queue forty-seven
   * and say so.
   */
  const queue = useApiMutation<{ queued: number; joined: number; failed: number }, ParseRow[]>(
    async (targets) => {
      let queued = 0
      let joined = 0
      let failed = 0
      for (const row of targets) {
        const content = asContent(row.data)
        if (!content?.platform || !content.content_id) {
          failed += 1
          continue
        }
        try {
          const result: { reused?: string | null } = await apiPost(
            paths.downloads.create,
            {
              platform: content.platform as Platform,
              content_id: content.content_id,
              skip_existing: skipExisting,
            },
            { awaitTask: false },
          )
          if (result.reused) joined += 1
          else queued += 1
        } catch {
          failed += 1
        }
      }
      return { queued, joined, failed }
    },
    {
      onSuccess: ({ queued: started, joined, failed }) => {
        onQueued()
        toast.success(t('parse.queue.done', { count: started }), {
          description:
            joined || failed
              ? t('parse.queue.detail', { joined, failed })
              : t('parse.queue.watch'),
        })
      },
      onError: (error) => {
        toast.apiError(error, t('parse.queue.failed'))
      },
    },
  )

  /** Rows that named a post the downloader could actually be pointed at. */
  const downloadable = useMemo(
    () =>
      rows.filter((row) => {
        const content = asContent(row.data)
        return row.state === 'done' && Boolean(content?.platform) && Boolean(content?.content_id)
      }),
    [rows],
  )

  /* ------------------------------------------------------------ columns --- */

  const columns = useMemo<Array<Column<ParseRow>>>(
    () => [
      {
        id: 'state',
        header: t('console:field.state'),
        hideable: false,
        cell: (row) =>
          row.state === 'pending' ? (
            <StatusBadge kind="task" value="queued" size="sm" />
          ) : (
            <StatusBadge kind="task" value={row.state} size="sm" />
          ),
        sortValue: (row) => row.state,
      },
      {
        id: 'url',
        header: t('parse.field.url'),
        hideable: false,
        width: '22%',
        cell: (row) => (
          <span className="u-truncate u-mono u-xs" title={row.url}>
            {row.url}
          </span>
        ),
        sortValue: (row) => row.url,
      },
      {
        id: 'title',
        header: t('parse.field.title'),
        cell: (row) => {
          const content = asContent(row.data)
          if (!content) return <span className="u-muted">—</span>
          return (
            <span className="u-truncate" title={content.title ?? ''}>
              {content.title || content.description || '—'}
            </span>
          )
        },
        sortValue: (row) => asContent(row.data)?.title ?? null,
      },
      {
        id: 'author',
        header: t('parse.field.author'),
        cell: (row) => {
          const author = asContent(row.data)?.author
          if (!author) return <span className="u-muted">—</span>
          return <span className="u-truncate">{author.nickname ?? author.unique_id ?? '—'}</span>
        },
        sortValue: (row) => asContent(row.data)?.author?.nickname ?? null,
      },
      {
        id: 'platform',
        header: t('console:field.platform'),
        mono: true,
        cell: (row) => asContent(row.data)?.platform ?? <span className="u-muted">—</span>,
        sortValue: (row) => asContent(row.data)?.platform ?? null,
      },
      {
        id: 'kind',
        header: t('parse.field.kind'),
        mono: true,
        cell: (row) => asContent(row.data)?.kind ?? <span className="u-muted">—</span>,
        sortValue: (row) => asContent(row.data)?.kind ?? null,
      },
      {
        id: 'contentId',
        header: t('parse.field.contentId'),
        mono: true,
        cell: (row) => <CopyableId value={asContent(row.data)?.content_id} />,
      },
      {
        id: 'plays',
        header: t('parse.field.plays'),
        align: 'right',
        cell: (row) => (
          <span className="u-mono">{format.compact(asContent(row.data)?.stats?.play_count)}</span>
        ),
        sortValue: (row) => asContent(row.data)?.stats?.play_count ?? null,
      },
      {
        id: 'diggs',
        header: t('parse.field.diggs'),
        align: 'right',
        cell: (row) => (
          <span className="u-mono">{format.compact(asContent(row.data)?.stats?.digg_count)}</span>
        ),
        sortValue: (row) => asContent(row.data)?.stats?.digg_count ?? null,
      },
      {
        id: 'comments',
        header: t('parse.field.comments'),
        align: 'right',
        defaultHidden: true,
        cell: (row) => (
          <span className="u-mono">{format.compact(asContent(row.data)?.stats?.comment_count)}</span>
        ),
        sortValue: (row) => asContent(row.data)?.stats?.comment_count ?? null,
      },
      {
        id: 'shares',
        header: t('parse.field.shares'),
        align: 'right',
        defaultHidden: true,
        cell: (row) => (
          <span className="u-mono">{format.compact(asContent(row.data)?.stats?.share_count)}</span>
        ),
        sortValue: (row) => asContent(row.data)?.stats?.share_count ?? null,
      },
      {
        id: 'createdAt',
        header: t('parse.field.publishedAt'),
        defaultHidden: true,
        cell: (row) => {
          const created = asContent(row.data)?.created_at
          return created ? (
            <span className="u-mono u-nowrap">{format.dateTime(created)}</span>
          ) : (
            <span className="u-muted">—</span>
          )
        },
        sortValue: (row) => asContent(row.data)?.created_at ?? null,
      },
      {
        id: 'error',
        header: t('console:field.errorCode'),
        cell: (row) =>
          row.error ? (
            <span className="u-row u-wrap" title={row.error.message}>
              <ErrorCodeBadge code={row.error.code} />
            </span>
          ) : (
            <span className="u-muted">—</span>
          ),
        sortValue: (row) => row.error?.code ?? null,
      },
      {
        id: 'actions',
        header: <span className="u-sr-only">{t('common:action.more')}</span>,
        align: 'right',
        hideable: false,
        cell: (row) => (
          <span className="u-row" style={{ justifyContent: 'flex-end' }}>
            {row.state === 'done' ? (
              <>
                {/* One row rather than the whole paste. The toolbar button is
                    for "keep all of these"; this is for the one you came for. */}
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={queue.isPending || !asContent(row.data)?.content_id}
                  onClick={(event) => {
                    event.stopPropagation()
                    queue.mutate([row])
                  }}
                >
                  {t('parse.queue.one')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(event) => {
                    event.stopPropagation()
                    setPreview(row.id)
                  }}
                >
                  {t('common:action.view')}
                </Button>
              </>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              disabled={!retryable(row)}
              title={retryable(row) ? undefined : t('parse.retryNotAllowed')}
              onClick={(event) => {
                event.stopPropagation()
                void retryRow(row)
              }}
            >
              {t('common:action.retry')}
            </Button>
          </span>
        ),
      },
    ],
    [t, format, retryRow, retryable, queue],
  )

  /* ------------------------------------------------------------- render --- */

  const counts = useMemo(() => {
    const summary = { done: 0, failed: 0, running: 0 }
    for (const row of rows) {
      if (row.state === 'done') summary.done += 1
      else if (row.state === 'failed') summary.failed += 1
      else summary.running += 1
    }
    return summary
  }, [rows])

  const previewRow = rows.find((row) => row.id === preview) ?? null
  const previewContent = asContent(previewRow?.data)
  const previewVideo = videoMirrors(previewContent)
  const previewImages = previewContent?.media?.images ?? []
  const previewCover = coverMirrors(previewContent)[0] ?? null
  const failedRetryable = rows.filter((row) => retryable(row))

  return (
    <div className="u-stack">
      <div className="u-stack">
        <div className="u-stack">
          <Textarea
            label={t('parse.inputLabel')}
            description={t('parse.inputHint', { max: MAX_LINKS, chunk: BATCH_CHUNK })}
            rows={6}
            value={input}
            spellCheck={false}
            placeholder={'https://v.douyin.com/iRNBho6G/\nhttps://www.tiktok.com/@user/video/7300000000000000000'}
            onChange={(event) => {
              setInput(event.target.value)
            }}
            error={
              parsed.urls.length > MAX_LINKS ? t('parse.tooMany', { max: MAX_LINKS }) : undefined
            }
          />
          <div className="u-row u-wrap">
            <Button
              variant="primary"
              loading={submit.isPending}
              disabled={parsed.urls.length === 0 || parsed.urls.length > MAX_LINKS}
              onClick={() => {
                submit.mutate(parsed.urls)
              }}
            >
              {t('parse.submit', { count: parsed.urls.length })}
            </Button>
            <span className="u-xs u-muted">
              {t('parse.detected', { count: parsed.urls.length, duplicates: parsed.duplicates })}
            </span>
            {/* The tally the page header used to carry. It belongs beside the
                button that changes it now that this shares a page. */}
            {rows.length > 0 ? (
              <span className="u-row u-wrap" style={{ marginInlineStart: 'auto' }}>
                <span className="u-row" style={{ gap: 'var(--space-1)' }}>
                  <StatusBadge kind="task" value="done" size="sm" flash={false} />
                  <span className="u-mono u-xs">{counts.done}</span>
                </span>
                <span className="u-row" style={{ gap: 'var(--space-1)' }}>
                  <StatusBadge kind="task" value="running" size="sm" flash={false} />
                  <span className="u-mono u-xs">{counts.running}</span>
                </span>
                <span className="u-row" style={{ gap: 'var(--space-1)' }}>
                  <StatusBadge kind="task" value="failed" size="sm" flash={false} />
                  <span className="u-mono u-xs">{counts.failed}</span>
                </span>
              </span>
            ) : null}
          </div>
          {submit.isError ? (
            <ErrorState
              error={submit.error}
              compact
              onRetry={() => {
                submit.mutate(parsed.urls)
              }}
            />
          ) : null}
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('parse.trackingNote')}
          </p>
        </div>
      </div>

      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        loading={submit.isPending}
        storageKey="parse-results"
        onRowClick={(row) => {
          if (row.state === 'done') setPreview(row.id)
        }}
        flashValue={(row) => row.state}
        emptyTitle={t('parse.emptyTitle')}
        emptyDescription={t('parse.emptyDescription')}
        caption={t('downloads.mode.batch.label')}
        toolbar={
          <>
            {/* First, because it is what the paste was for. Parsing on its own
                answers "what are these"; this is "keep them". */}
            <Button
              size="sm"
              variant="primary"
              loading={queue.isPending}
              disabled={downloadable.length === 0}
              onClick={() => {
                queue.mutate(downloadable)
              }}
            >
              {t('parse.queue.action', { count: downloadable.length })}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={rows.length === 0}
              onClick={() => {
                exportRows('csv')
              }}
            >
              {t('parse.exportCsv')}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={rows.length === 0}
              onClick={() => {
                exportRows('json')
              }}
            >
              {t('parse.exportJson')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={failedRetryable.length === 0}
              onClick={() => {
                for (const row of failedRetryable) void retryRow(row)
              }}
            >
              {t('parse.retryFailed', { count: failedRetryable.length })}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={rows.length === 0}
              onClick={() => {
                setRows([])
                setDownloads({})
              }}
            >
              {t('common:action.clear')}
            </Button>
          </>
        }
      />

      <Drawer
        open={previewRow !== null}
        onClose={() => {
          setPreview(null)
        }}
        title={previewContent?.title || t('parse.previewTitle')}
        description={previewRow?.url}
      >
        {previewRow && previewContent ? (
          <div className="u-stack">
            <div className="u-row u-wrap u-xs u-muted">
              <span className="u-mono">{previewContent.platform}</span>
              <span className="u-mono">{previewContent.kind}</span>
              <CopyableId value={previewContent.content_id} />
              {previewContent.created_at ? (
                <span className="u-mono">{format.dateTime(previewContent.created_at)}</span>
              ) : null}
            </div>

            {previewVideo.length > 0 ? (
              <video
                controls
                playsInline
                preload="metadata"
                poster={previewCover ?? undefined}
                style={{
                  width: '100%',
                  borderRadius: 'var(--radius)',
                  background: 'var(--bg-inset)',
                  border: '1px solid var(--border-subtle)',
                }}
              >
                {previewVideo.map((url) => (
                  <source key={url} src={url} />
                ))}
              </video>
            ) : null}

            {previewImages.length > 0 ? (
              <div
                style={{
                  display: 'grid',
                  gap: 'var(--space-2)',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
                }}
              >
                {previewImages.map((image, index) => (
                  <a
                    key={image.url}
                    href={image.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    style={{ display: 'block' }}
                  >
                    <img
                      src={image.url}
                      alt={t('parse.imageAlt', { index: index + 1 })}
                      loading="lazy"
                      style={{
                        width: '100%',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-subtle)',
                      }}
                    />
                  </a>
                ))}
              </div>
            ) : null}

            <div className="u-row u-wrap u-xs">
              <span>
                {t('parse.field.plays')}{' '}
                <span className="u-mono">{format.compact(previewContent.stats?.play_count)}</span>
              </span>
              <span>
                {t('parse.field.diggs')}{' '}
                <span className="u-mono">{format.compact(previewContent.stats?.digg_count)}</span>
              </span>
              <span>
                {t('parse.field.comments')}{' '}
                <span className="u-mono">{format.compact(previewContent.stats?.comment_count)}</span>
              </span>
              <span>
                {t('parse.field.shares')}{' '}
                <span className="u-mono">{format.compact(previewContent.stats?.share_count)}</span>
              </span>
            </div>

            <Card title={t('parse.downloadTitle')} description={t('parse.downloadDescription')}>
              <div className="u-stack-sm">
                <DownloadButton
                  label={t('parse.downloadVideo')}
                  mirrors={previewVideo}
                  state={downloads[`${previewRow.id}:video`]}
                  onDownload={() => {
                    void download(`${previewRow.id}:video`, previewVideo, 'video.mp4')
                  }}
                  emptyLabel={t('parse.noVideo')}
                />
                <DownloadButton
                  label={t('parse.downloadCover')}
                  mirrors={coverMirrors(previewContent)}
                  state={downloads[`${previewRow.id}:cover`]}
                  onDownload={() => {
                    void download(`${previewRow.id}:cover`, coverMirrors(previewContent), 'cover.jpg')
                  }}
                  emptyLabel={t('parse.noCover')}
                />
                {previewImages.map((image, index) => (
                  <DownloadButton
                    key={image.url}
                    label={t('parse.downloadImage', { index: index + 1 })}
                    mirrors={mirrorsOf(image)}
                    state={downloads[`${previewRow.id}:image-${index}`]}
                    onDownload={() => {
                      void download(
                        `${previewRow.id}:image-${index}`,
                        mirrorsOf(image),
                        `image-${index + 1}.jpg`,
                      )
                    }}
                    emptyLabel={t('parse.noImage')}
                  />
                ))}
                <p className="u-xs u-muted" style={{ margin: 0 }}>
                  {t('parse.downloadNote')}
                </p>
              </div>
            </Card>

            <CodeBlock json={previewRow.data} title={t('parse.rawTitle')} defaultCollapsed />
          </div>
        ) : (
          <p className="u-muted">{t('parse.previewEmpty')}</p>
        )}
      </Drawer>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Local pieces                                                                */
/* -------------------------------------------------------------------------- */

function DownloadButton({
  label,
  mirrors,
  state,
  onDownload,
  emptyLabel,
}: {
  label: string
  mirrors: string[]
  state: DownloadState | undefined
  onDownload: () => void
  emptyLabel: string
}) {
  const { t } = useTranslation(['console', 'common'])

  if (mirrors.length === 0) {
    return <span className="u-xs u-muted">{emptyLabel}</span>
  }

  return (
    <div className="u-row u-wrap">
      <Button
        size="sm"
        variant="secondary"
        loading={state?.status === 'trying'}
        onClick={onDownload}
      >
        {label}
      </Button>
      <span className="u-xs u-muted">{t('parse.mirrorCount', { count: mirrors.length })}</span>
      {state?.status === 'trying' ? (
        <span className="u-xs u-muted">
          {t('parse.mirrorTrying', { index: state.mirror, total: state.total })}
        </span>
      ) : null}
      {state?.status === 'done' ? (
        <span className="u-xs" style={{ color: 'var(--success)' }}>
          {t('parse.mirrorDone', { index: state.mirror })}
        </span>
      ) : null}
      {state?.status === 'blocked' ? (
        <span className="u-xs" style={{ color: 'var(--caution)' }}>
          {t('parse.mirrorBlocked')}
        </span>
      ) : null}
    </div>
  )
}
