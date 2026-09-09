import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  Checkbox,
  CopyableId,
  ConfirmDialog,
  DataTable,
  Drawer,
  Field,
  Input,
  MetricTile,
  PageHeader,
  Select,
  StatusBadge,
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate } from '@/hooks'
import { apiDelete, apiGet, apiPost, isApiError } from '@/lib/api'
import { cn } from '@/lib/cn'
import { API_V1, paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { DOWNLOAD_STATES, type DownloadState, type Platform } from '@/lib/types'

import styles from './Downloads.module.css'

/**
 * Media stored on this instance's own disk.
 *
 * A file can be opened from here, and that is a reversal worth stating. The
 * page used to refuse it on the grounds that streaming a stored byte back
 * through the API is the relay docs/design/07-frontend.md rules out. Doc 18
 * §1.4.1 revisits that: none of doc 07's three reasons - bandwidth, an open
 * proxy, expiring signed links - describes handing an already-stored file to
 * an authenticated operator's own browser. The endpoint fetches nothing,
 * addresses a database row rather than a URL, and sits behind `media:read`.
 * The downloader is still a sink; the bytes leave through the API because the
 * API is the part of this system that can say who is asking.
 *
 * A row whose files have been cleaned up still appears, greyed, saying so.
 * "Collected and later evicted" is a different fact from "never fetched" -
 * only the first can be undone by asking again - and hiding the evicted rows
 * would erase the only evidence the size ceiling ever ran.
 */

/** The stored cover, when the row still has its files. */
function coverOf(row: DownloadRow): string | null {
  if (!row.on_disk) return null
  const file = row.files.find((entry) => entry.kind === 'cover' && entry.state === 'done')
  return file ? paths.downloads.file(row.id, file.name) : null
}

/** The stored video, when there is one. An image post has none. */
function videoOf(row: DownloadRow): string | null {
  if (!row.on_disk) return null
  const file = row.files.find((entry) => entry.kind === 'video' && entry.state === 'done')
  return file ? paths.downloads.file(row.id, file.name) : null
}

/**
 * What this page can fetch, as a table rather than as buttons.
 *
 * Three buttons under one input was already hiding two of them, and the list
 * is going to grow. A mode is a row here: what it takes, what it does, what to
 * call it. Adding the next one is adding an entry.
 *
 * `wants` is the shape of the thing pasted, and it is the only reason the
 * modes cannot share one submit button: a post link and a profile link are
 * different inputs, and a form that accepted either and guessed would download
 * the wrong thing quietly.
 */
type DownloadMode = 'post' | 'comments' | 'author'

interface ModeSpec {
  id: DownloadMode
  wants: 'post' | 'author'
  /** Whether "skip what is already downloaded" means anything for this mode. */
  skippable: boolean
}

const POST_MODE: ModeSpec = { id: 'post', wants: 'post', skippable: true }

const MODES: readonly ModeSpec[] = [
  POST_MODE,
  { id: 'comments', wants: 'post', skippable: false },
  { id: 'author', wants: 'author', skippable: true },
]

/** One page of comments, as the read endpoint returns it. */
interface CommentPage {
  items: unknown[]
  cursor: string | null
  has_more: boolean
}

interface AuthorPage {
  items: Array<{ content_id: string }>
}

interface ParsedLink {
  allowed: boolean
  platform: string | null
  resource: string
  resource_id: string | null
}

/** How deep a comment save goes. Each page is a real request through the pool. */
const COMMENT_PAGES = 5
const COMMENT_PAGE_SIZE = 50
/** One page of an author's feed. Dozens of downloads is already a lot to queue. */
const AUTHOR_PAGE_SIZE = 20

/** Hand the browser a file. The archive export does the same thing. */
function saveJson(data: unknown, filename: string): void {
  const href = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
  )
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => {
    URL.revokeObjectURL(href)
  }, 1_000)
}

const DOWNLOADS_KEY = ['downloads'] as const
const STORAGE_KEY = ['downloads', 'storage'] as const

const PLATFORMS: readonly Platform[] = ['douyin', 'tiktok']

interface DownloadFile {
  name: string
  kind: string
  state: string
  bytes: number
  sha256: string | null
  content_type: string | null
  error: string | null
}

interface DownloadRow {
  id: string
  platform: string
  content_id: string
  author_uid: string
  state: DownloadState
  directory: string
  bytes_total: number
  file_count: number
  files: DownloadFile[]
  pinned: boolean
  on_disk: boolean
  files_removed_at: string | null
  error: string | null
  task_id: string | null
  created_at: string
  finished_at: string | null
  /**
   * What the archive knows about the post, or null. Null is a real answer:
   * media can be kept for a post the archive later dropped, and the row still
   * describes a real file on the volume.
   */
  post: { title: string | null; author: string | null; duration_ms: number | null } | null
}

interface DownloadList {
  items: DownloadRow[]
  total: number
}

interface DownloaderHealth {
  available: boolean
  version?: string
  workers?: number
  queued?: number
  running?: number
  volume_bytes?: number
  detail?: string
}

interface Storage {
  downloads: number
  bytes_total: number
  pinned: number
  evicted: number
  in_flight: number
  enabled: boolean
  max_bytes: number
  max_file_bytes: number
  downloader: DownloaderHealth
}

export default function Downloads() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()

  const [platform, setPlatform] = useState<Platform>('douyin')
  //: A link or a post id. One box, because the share sheet gives a link and
  //: asking which one you have is asking you to classify your own clipboard.
  const [target, setTarget] = useState('')
  //: How many duplicates a dry run found, while the confirmation is up.
  const [deduping, setDeduping] = useState<number | null>(null)
  const [mode, setMode] = useState<DownloadMode>('post')
  //: Shared by every mode that can act on more than one thing, which is what
  //: makes re-running a feed cheap: the author added three posts and the other
  //: forty are already here.
  const [skipExisting, setSkipExisting] = useState(true)

  // Written as a lookup with a literal fallback rather than MODES[0], which
  // TypeScript cannot prove is there and eslint will not let us assert.
  const active = MODES.find((entry) => entry.id === mode) ?? POST_MODE
  const [stateFilter, setStateFilter] = useState<DownloadState | ''>('')
  const [inspecting, setInspecting] = useState<DownloadRow | null>(null)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [confirming, setConfirming] = useState<'delete' | null>(null)
  const [working, setWorking] = useState(false)

  const storage = useApiQuery<Storage>({
    key: STORAGE_KEY,
    path: paths.downloads.storage,
    poll: POLL.slow,
  })

  const list = useApiQuery<DownloadList>({
    key: [...DOWNLOADS_KEY, stateFilter],
    path: paths.downloads.list,
    params: stateFilter ? { state: stateFilter } : undefined,
    // Fast while something is in flight, slow otherwise: a transfer is the one
    // thing on this page that changes on its own.
    poll: (storage.data?.in_flight ?? 0) > 0 ? POLL.fast : POLL.slow,
  })

  const refresh = (): void => {
    void invalidate(DOWNLOADS_KEY)
    void invalidate(STORAGE_KEY)
  }

  /**
   * One box, two shapes.
   *
   * People have a link far more often than an id - the share sheet gives a
   * link - and asking which one this is would be asking them to classify their
   * own clipboard. Digits are an id, anything else is a link, and the API
   * refuses either if it cannot make sense of it.
   */
  const looksLikeId = /^\d+$/.test(target.trim())

  const start = useApiMutation<
    { download_id: string; skipped?: string[]; archived?: boolean; reused?: string | null },
    void
  >(
    () =>
      apiPost(
        paths.downloads.create,
        looksLikeId
          ? { platform, content_id: target.trim(), skip_existing: skipExisting }
          : { url: target.trim(), skip_existing: skipExisting },
        { awaitTask: false },
      ),
    {
      onSuccess: (result) => {
        setTarget('')
        refresh()
        toast.success(
          // Three outcomes worth telling apart: it is already here, it has to
          // be fetched before it can be downloaded (a slower first result), or
          // it started normally.
          result.reused
            ? t(`downloads.toast.reused.${result.reused}`)
            : result.archived === false
              ? t('downloads.toast.fetchingFirst')
              : t('downloads.toast.started'),
          { description: result.skipped?.length ? result.skipped.join('; ') : undefined },
        )
      },
      onError: (error) => {
        toast.apiError(error, t('downloads.toast.startFailed'))
      },
    },
  )

  /**
   * Save a post's comments as a JSON file, through the browser.
   *
   * Comments are data, not media: the sidecar downloads files onto the
   * operator's volume, and there is no file here to fetch - the platform hands
   * them over as JSON. So this pages through the ordinary read endpoint and
   * hands the browser a blob, the same way the archive export already works.
   * It spends the identity pool like any other read, which is why the page
   * says how many pages it is about to ask for.
   */
  const saveComments = useApiMutation<number, void>(
    async () => {
      const { platform: which, id } = await resolveTarget()
      const all: unknown[] = []
      let cursor: string | null = null
      for (let page = 0; page < COMMENT_PAGES; page += 1) {
        const body: CommentPage = await apiGet(`${API_V1}/${which}/video/comments`, {
          params: {
            aweme_id: id,
            count: String(COMMENT_PAGE_SIZE),
            ...(cursor ? { cursor } : {}),
          },
        })
        all.push(...body.items)
        if (!body.has_more || !body.cursor) break
        cursor = body.cursor
      }
      saveJson(all, `comments-${which}-${id}.json`)
      return all.length
    },
    {
      onSuccess: (count) => {
        toast.success(t('downloads.comments.done', { count }))
      },
      onError: (error) => {
        toast.apiError(error, t('downloads.comments.failed'))
      },
    },
  )

  /**
   * Queue a download for everything an author has posted.
   *
   * One page of their feed, then one download request per post. Bounded on
   * purpose: this is the control on the page that can spend the identity pool
   * dozens of times from one click, and a number the operator can see beats a
   * "download everything" button whose cost is discovered afterwards.
   */
  const saveAuthor = useApiMutation<{ queued: number; posts: number; skipped: number }, void>(
    async () => {
      const { platform: which, id } = await resolveTarget({ author: true })
      const feed: AuthorPage = await apiGet(`${API_V1}/${which}/user/posts`, {
        params: { sec_user_id: id, count: String(AUTHOR_PAGE_SIZE) },
      })
      let queued = 0
      let skipped = 0
      for (const post of feed.items) {
        try {
          // The server decides what "already downloaded" means, so this and a
          // single post's button cannot drift apart about it - and it also
          // joins anything already in flight, which is what stops a feed
          // racing itself into the same directory twice.
          const result: { reused?: string | null } = await apiPost(
            paths.downloads.create,
            { platform: which, content_id: post.content_id, skip_existing: skipExisting },
            { awaitTask: false },
          )
          if (result.reused) skipped += 1
          else queued += 1
        } catch {
          // One post with nothing fetchable must not abandon the rest; the
          // counts reported at the end are what actually happened.
        }
      }
      return { queued, posts: feed.items.length, skipped }
    },
    {
      onSuccess: ({ queued, posts, skipped }) => {
        setTarget('')
        refresh()
        toast.success(
          skipped > 0
            ? t('downloads.author.doneSkipping', { queued, posts, skipped })
            : t('downloads.author.done', { queued, posts }),
        )
      },
      onError: (error) => {
        toast.apiError(error, t('downloads.author.failed'))
      },
    },
  )

  /**
   * Turn whatever is in the box into a platform and an id.
   *
   * A bare id is taken at face value with the platform menu beside it; anything
   * else goes to /tools/parse-url, which is the same allowlist the download
   * endpoint uses, so an unrecognised host never becomes a request.
   */
  const resolveTarget = async (
    opts: { author?: boolean } = {},
  ): Promise<{ platform: Platform; id: string }> => {
    const text = target.trim()
    if (/^\d+$/.test(text) && !opts.author) return { platform, id: text }

    const kind: ParsedLink = await apiGet(paths.tools.parseUrl, { params: { url: text } })
    if (!kind.allowed || !kind.platform || !kind.resource_id) {
      throw new Error(t('downloads.target.unrecognised'))
    }
    const wanted = opts.author ? 'user' : 'video'
    if (kind.resource !== wanted) throw new Error(t(`downloads.target.expected.${wanted}`))
    return { platform: kind.platform as Platform, id: kind.resource_id }
  }

  /** Keep one copy of each post; remove the rest. */
  const dedupe = useApiMutation<
    { duplicates: number; removed: number; freed_bytes: number },
    boolean
  >((dryRun) => apiPost(paths.downloads.dedupe, { dry_run: dryRun }), {
    onSuccess: (result, dryRun) => {
      refresh()
      if (result.duplicates === 0) {
        toast.success(t('downloads.dedupe.none'))
        return
      }
      if (dryRun) {
        setDeduping(result.duplicates)
        return
      }
      toast.success(
        t('downloads.dedupe.done', {
          count: result.removed,
          size: formatters.bytes(result.freed_bytes),
        }),
      )
    },
    onError: (error) => {
      toast.apiError(error)
    },
  })

  const pin = useApiMutation<unknown, { id: string; pinned: boolean }>(
    ({ id, pinned }) => apiPost(paths.downloads.pin(id), { pinned }),
    {
      onSuccess: (_result, { pinned }) => {
        refresh()
        toast.success(pinned ? t('downloads.toast.pinned') : t('downloads.toast.unpinned'))
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const ceiling = storage.data?.max_bytes ?? 0
  const used = storage.data?.downloader?.volume_bytes ?? storage.data?.bytes_total ?? 0
  const share = ceiling > 0 ? Math.min(1, used / ceiling) : 0

  const notConfigured =
    isApiError(storage.error) && storage.error.code === 'NOT_CONFIGURED'
      ? storage.error.message
      : storage.data && !storage.data.downloader.available
        ? storage.data.downloader.detail || t('downloads.unavailable')
        : null

  const rows = list.data?.items ?? []
  const chosen = rows.filter((row) => selected.has(row.id))
  /** Pinning protects a whole directory, so an already-pinned row is a no-op. */
  const unpinned = chosen.filter((row) => !row.pinned)
  const pinned = chosen.filter((row) => row.pinned)
  /** Only a row that still has files on the volume has anything to delete. */
  const removable = chosen.filter((row) => row.on_disk)

  /**
   * One request per row rather than a bulk endpoint. There is no bulk endpoint,
   * and adding a multi-delete to the public API to serve a console button is a
   * bigger thing than the button. Sequential, stopping on the first failure, so
   * a server that has already refused is not asked N more times.
   */
  const applyToEach = async (
    targets: DownloadRow[],
    call: (row: DownloadRow) => Promise<unknown>,
    toastKey: string,
  ): Promise<void> => {
    setWorking(true)
    let done = 0
    for (const row of targets) {
      try {
        await call(row)
        done += 1
      } catch (error) {
        toast.apiError(error)
        break
      }
    }
    if (done > 0) toast.success(t(toastKey, { count: done }))
    setSelected(new Set())
    setConfirming(null)
    setWorking(false)
    void invalidate(DOWNLOADS_KEY)
    void invalidate(STORAGE_KEY)
  }

  const columns: Array<Column<DownloadRow>> = useMemo(
    () => [
      {
        id: 'content',
        header: t('downloads.column.content'),
        // The thumbnail is the fastest way to know which post a row is, and it
        // costs nothing new: the cover is already on the volume and already
        // servable. Falls back to the content id, which is what this column
        // used to be on its own - a table of receipts that said something was
        // kept and not what.
        // Bounded, or a long caption pushes every other column off the screen.
        // A title is worth a lot of room and not the whole table.
        width: '360px',
        cell: (row) => {
          const cover = coverOf(row)
          return (
            <div className="u-row" style={{ minWidth: 0 }}>
              <span className={styles.thumb}>
                {cover ? <img src={cover} alt="" loading="lazy" /> : null}
              </span>
              <div className={styles.cell} style={{ minWidth: 0, maxWidth: '310px' }}>
                <span className="u-truncate" title={row.directory}>
                  {row.post?.title || row.content_id}
                </span>
                <span className={styles.sub}>
                  {row.post?.author ? `${row.post.author} · ` : ''}
                  {row.platform}
                </span>
              </div>
            </div>
          )
        },
        sortValue: (row) => row.post?.title || row.content_id,
      },
      {
        id: 'state',
        header: t('downloads.column.state'),
        width: '150px',
        cell: (row) => <StatusBadge kind="download" value={row.state} flash />,
        sortValue: (row) => row.state,
      },
      {
        id: 'files',
        header: t('downloads.column.files'),
        width: '120px',
        align: 'right',
        mono: true,
        cell: (row) => row.file_count || <span className="u-muted">—</span>,
        sortValue: (row) => row.file_count,
      },
      {
        id: 'size',
        header: t('downloads.column.size'),
        width: '120px',
        align: 'right',
        mono: true,
        cell: (row) =>
          row.on_disk ? (
            formatters.bytes(row.bytes_total)
          ) : (
            <span className="u-muted" title={t('downloads.evictedHint')}>
              {t('downloads.evicted')}
            </span>
          ),
        sortValue: (row) => row.bytes_total,
      },
      {
        id: 'finished',
        header: t('downloads.column.finished'),
        width: '180px',
        mono: true,
        cell: (row) =>
          row.finished_at ? (
            formatters.dateTime(row.finished_at)
          ) : (
            <span className="u-muted">—</span>
          ),
        sortValue: (row) => row.finished_at ?? '',
      },
      {
        id: 'pinned',
        header: t('downloads.column.keep'),
        width: '130px',
        cell: (row) => (
          <Button
            size="sm"
            variant={row.pinned ? 'primary' : 'ghost'}
            loading={pin.isPending && pin.variables?.id === row.id}
            onClick={() => {
              pin.mutate({ id: row.id, pinned: !row.pinned })
            }}
            title={row.pinned ? t('downloads.unpinHint') : t('downloads.pinHint')}
          >
            {row.pinned ? t('downloads.pinned') : t('downloads.pin')}
          </Button>
        ),
        sortValue: (row) => (row.pinned ? 1 : 0),
      },
    ],
    [formatters, pin, t],
  )

  return (
    <div className="u-page">
      <PageHeader
        title={t('downloads.title')}
        description={t('downloads.description')}
        actions={
          <Button variant="secondary" onClick={refresh}>
            {t('common:action.refresh')}
          </Button>
        }
      />

      <div className={styles.tiles}>
        <MetricTile
          label={t('downloads.metric.stored')}
          value={formatters.bytes(used)}
          loading={storage.isLoading}
          footer={
            ceiling > 0
              ? t('downloads.metric.ofCeiling', { ceiling: formatters.bytes(ceiling) })
              : t('downloads.metric.noCeiling')
          }
          tone={share >= 0.9 ? 'danger' : share >= 0.75 ? 'warning' : 'neutral'}
        />
        <MetricTile
          label={t('downloads.metric.downloads')}
          value={formatters.number(storage.data?.downloads ?? 0)}
          loading={storage.isLoading}
          footer={t('downloads.metric.inFlight', { count: storage.data?.in_flight ?? 0 })}
        />
        <MetricTile
          label={t('downloads.metric.pinned')}
          value={formatters.number(storage.data?.pinned ?? 0)}
          loading={storage.isLoading}
          footer={t('downloads.metric.pinnedHint')}
        />
        <MetricTile
          label={t('downloads.metric.evicted')}
          value={formatters.number(storage.data?.evicted ?? 0)}
          loading={storage.isLoading}
          footer={t('downloads.metric.evictedHint')}
        />
      </div>

      {notConfigured ? (
        <Card title={t('downloads.unavailableTitle')}>
          <p className="u-muted">{notConfigured}</p>
          <p className="u-xs u-muted">{t('downloads.unavailableHint')}</p>
        </Card>
      ) : null}

      <Card title={t('downloads.start.title')} description={t(`downloads.mode.${mode}.description`)}>
        {/* Modes as a tab row, not three buttons under one input.
            Three buttons were already hiding two of them, and the list is
            going to grow - so what this page can fetch is a table now, and
            adding the next thing is adding a row to it rather than another
            button nobody notices. */}
        <div className={styles.modes} role="tablist" aria-label={t('downloads.start.title')}>
          {MODES.map((entry) => (
            <button
              key={entry.id}
              type="button"
              role="tab"
              aria-selected={mode === entry.id}
              className={styles.mode}
              onClick={() => {
                setMode(entry.id)
              }}
            >
              {t(`downloads.mode.${entry.id}.label`)}
            </button>
          ))}
        </div>

        <form
          className={cn('u-form-row', styles.startForm)}
          onSubmit={(event) => {
            event.preventDefault()
            if (!target.trim()) return
            if (mode === 'post') start.mutate()
            else if (mode === 'comments') saveComments.mutate()
            else saveAuthor.mutate()
          }}
        >
          {/* Only consulted for a bare id, and only a post can be named by
              one - an author is a sec_user_id, which is not digits. A link
              says which platform it is, and the API refuses one that disagrees
              with this rather than guessing, so the menu is disabled while a
              link is in the box. */}
          <Field id="download-platform" label={t('downloads.field.platform')}>
            <Select
              value={platform}
              disabled={active.wants === 'author' || (target.trim().length > 0 && !looksLikeId)}
              onChange={(event) => {
                setPlatform(event.target.value as Platform)
              }}
            >
              {PLATFORMS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            id="download-content"
            label={t(`downloads.mode.${mode}.field`)}
            description={t(`downloads.mode.${mode}.hint`)}
          >
            <Input
              value={target}
              onChange={(event) => {
                setTarget(event.target.value)
              }}
              mono
              placeholder={t(`downloads.mode.${mode}.placeholder`)}
            />
          </Field>
          <Button
            type="submit"
            variant="primary"
            loading={start.isPending || saveComments.isPending || saveAuthor.isPending}
            disabled={!target.trim()}
          >
            {t(`downloads.mode.${mode}.action`)}
          </Button>
        </form>

        {/* One option, shared by every mode it means anything for. Comments are
            saved to your browser rather than to the volume, so there is nothing
            here for it to skip. */}
        {active.skippable ? (
          <div className={styles.startOptions}>
            <Checkbox
              checked={skipExisting}
              onChange={(event) => {
                setSkipExisting(event.target.checked)
              }}
              label={t('downloads.skipExisting.label')}
              hint={t('downloads.skipExisting.hint')}
            />
          </div>
        ) : null}

        <p className={styles.startNote}>{t('downloads.start.sinkNote')}</p>
      </Card>

      {/* Housekeeping, next to the volume it frees. A dry run first, always:
          the count is the whole decision and nobody can make it from a button
          labelled "deduplicate". */}
      <Card title={t('downloads.dedupe.title')} description={t('downloads.dedupe.description')}>
        <Button
          variant="secondary"
          loading={dedupe.isPending}
          onClick={() => {
            dedupe.mutate(true)
          }}
        >
          {t('downloads.dedupe.action')}
        </Button>
      </Card>

      <ConfirmDialog
        open={deduping !== null}
        danger
        title={t('downloads.dedupe.confirmTitle', { count: deduping ?? 0 })}
        description={t('downloads.dedupe.confirmBody')}
        confirmLabel={t('downloads.dedupe.confirmAction')}
        loading={dedupe.isPending}
        onConfirm={() => {
          setDeduping(null)
          dedupe.mutate(false)
        }}
        onCancel={() => {
          setDeduping(null)
        }}
      />

      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        loading={list.isLoading}
        error={list.error}
        onRetry={() => {
          void list.refetch()
        }}
        storageKey="downloads"
        selectedIds={selected}
        onSelectionChange={setSelected}
        onRowClick={setInspecting}
        defaultSort={{ columnId: 'finished', direction: 'desc' }}
        flashValue={(row) => row.state}
        emptyTitle={t('downloads.empty.title')}
        emptyDescription={t('downloads.empty.description')}
        toolbar={
          <>
            <Select
              aria-label={t('downloads.column.state')}
              value={stateFilter}
              onChange={(event) => {
                setStateFilter(event.target.value as DownloadState | '')
              }}
            >
              <option value="">{t('downloads.filter.allStates')}</option>
              {DOWNLOAD_STATES.map((state) => (
                <option key={state} value={state}>
                  {t(`common:state.download.${state}`)}
                </option>
              ))}
            </Select>
            {unpinned.length > 0 ? (
              <Button
                size="sm"
                variant="secondary"
                loading={working}
                onClick={() => {
                  void applyToEach(
                    unpinned,
                    (row) => apiPost(paths.downloads.pin(row.id), { pinned: true }),
                    'downloads.bulk.pinned',
                  )
                }}
              >
                {t('downloads.bulk.pin', { count: unpinned.length })}
              </Button>
            ) : null}
            {pinned.length > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                loading={working}
                onClick={() => {
                  void applyToEach(
                    pinned,
                    (row) => apiPost(paths.downloads.pin(row.id), { pinned: false }),
                    'downloads.bulk.unpinned',
                  )
                }}
              >
                {t('downloads.bulk.unpin', { count: pinned.length })}
              </Button>
            ) : null}
            {removable.length > 0 ? (
              <Button
                size="sm"
                variant="danger"
                loading={working}
                onClick={() => {
                  setConfirming('delete')
                }}
              >
                {t('downloads.bulk.delete', { count: removable.length })}
              </Button>
            ) : null}
          </>
        }
      />

      <ConfirmDialog
        open={confirming === 'delete'}
        danger
        loading={working}
        title={t('downloads.bulk.deleteTitle')}
        description={t('downloads.bulk.deleteBody', { count: removable.length })}
        confirmLabel={t('downloads.bulk.deleteConfirm')}
        onConfirm={() => {
          void applyToEach(
            removable,
            (row) => apiDelete(paths.downloads.byId(row.id)),
            'downloads.bulk.deleted',
          )
        }}
        onCancel={() => {
          setConfirming(null)
        }}
      />

      <Drawer
        open={inspecting !== null}
        onClose={() => {
          setInspecting(null)
        }}
        title={inspecting?.content_id ?? ''}
        description={inspecting?.directory}
      >
        {inspecting ? <Detail row={inspecting} /> : null}
      </Drawer>
    </div>
  )
}

/**
 * One download's files.
 *
 * The sha256 is here because it is the only thing that makes a stored file
 * verifiable years later, when the post is gone and the CDN link means nothing.
 * It sits beside the file rather than behind it for the same reason: what you
 * check a download against is the digest, not the size.
 */
function Detail({ row }: { row: DownloadRow }) {
  const { t } = useTranslation(['console', 'common'])
  const formatters = useFormatters()

  const video = videoOf(row)
  const cover = coverOf(row)

  return (
    <div className="u-stack">
      {video ? (
        <video className={styles.player} src={video} poster={cover ?? undefined} controls preload="metadata" />
      ) : cover ? (
        <img className={styles.player} src={cover} alt="" />
      ) : null}

      <div className="u-row-between">
        <StatusBadge kind="download" value={row.state} />
        <span className="u-mono u-xs u-muted">
          {row.on_disk ? formatters.bytes(row.bytes_total) : t('downloads.evicted')}
        </span>
      </div>

      {row.error ? <p className="u-muted">{row.error}</p> : null}
      {!row.on_disk ? <p className="u-xs u-muted">{t('downloads.evictedHint')}</p> : null}

      <ul className={styles.files}>
        {row.files.map((file) => (
          <li key={file.name} className={styles.file}>
            <div className="u-row-between">
              <span className="u-mono u-truncate" title={file.name}>
                {file.name}
              </span>
              <span className="u-mono u-xs u-muted">
                {file.state === 'done' ? formatters.bytes(file.bytes) : file.state}
              </span>
            </div>
            {file.sha256 ? (
              <CopyableId value={file.sha256} label={t('downloads.file.sha256')} />
            ) : null}
            {file.error ? <span className={styles.sub}>{file.error}</span> : null}
            {row.on_disk && file.state === 'done' ? (
              <a
                className={styles.open}
                href={paths.downloads.file(row.id, file.name)}
                download={file.name}
              >
                {t('downloads.file.open')}
              </a>
            ) : null}
          </li>
        ))}
      </ul>

      {row.files.length === 0 ? <p className="u-muted">{t('downloads.file.none')}</p> : null}
    </div>
  )
}
