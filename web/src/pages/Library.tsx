import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'wouter'

import {
  Button,
  Card,
  CompactProgress,
  ConfirmDialog,
  CrossIcon,
  DataTable,
  DownloadIcon,
  Drawer,
  EmptyState,
  ErrorState,
  Input,
  MetricTile,
  Modal,
  PageHeader,
  PlayIcon,
  Select,
  Skeleton,
  TranscribeButton,
  type Column,
  type ExistingTranscription,
  useToast,
  useTranscriberState,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate, useSession } from '@/hooks'
import { apiDelete, apiGet, apiPost, apiText } from '@/lib/api'
import { downloadHref, patchParams } from '@/lib/contentLinks'
import { paths } from '@/lib/endpoints'
import { atLeast } from '@/lib/roles'
import { PLATFORMS } from '@/lib/types'

import styles from './Library.module.css'
import { NO_TALLY, eachInTurn, type StopFlag, type Tally } from './downloadRuns'

/**
 * What this instance has already collected.
 *
 * The one page in the console that answers from local storage: nothing here
 * spends an identity, nothing is rate limited upstream, and a post that has
 * since been deleted is still here with `availability` saying so. That is the
 * whole reason the archive exists, so the page leads with the totals rather
 * than with a search box.
 *
 * Paging is by cursor, never by page number, because the table is written to
 * while a client walks it - an offset silently skips and repeats rows, and the
 * failure mode is a user who believes they have everything.
 */

const STATS_KEY = ['archive', 'stats'] as const

const KINDS = ['video', 'image_album'] as const
const DURATIONS = ['short', 'medium', 'long', 'unknown'] as const
const AVAILABILITIES = ['live', 'deleted', 'private', 'unknown'] as const

interface ArchivedAuthor {
  uid: string
  nickname: string | null
}

interface ArchivedRow {
  platform: string
  content_id: string
  kind: string
  web_url: string
  title: string
  description: string
  created_at: string | null
  duration_ms: number | null
  author: ArchivedAuthor
  music: { music_id: string | null; title: string | null }
  tags: string[]
  location: string | null
  cover_url: string | null
  classification: {
    orientation: string
    duration_bucket: string
    resolution_class: string
    script: string
  }
  availability: string
  first_seen_at: string
  last_seen_at: string
  /**
   * What this instance has on its own disk for the post, or null. Null also
   * when the caller lacks `media:read` - the archive and the volume are
   * separate scopes on purpose.
   */
  stored: StoredMedia | null
  /** Ids of the hand-made sets this post is in. Empty for most posts. */
  collections: string[]
}

interface CollectionRow {
  id: string
  name: string
  note: string | null
  items: number
  created_at: string | null
  updated_at: string | null
}

type ViewMode = 'grid' | 'table'

/**
 * How the wall is broken up. Author is the platform's own grouping and the one
 * an operator thinks in; "saved" groups by when this instance stored the media,
 * which is the question "what did I pull down last night".
 */
type GroupBy = 'none' | 'author' | 'saved'

interface StoredMedia {
  download_id: string
  bytes_total: number
  /**
   * When the files landed. Optional: an older server does not send it, and
   * the grouping then falls back to what it used before.
   */
  saved_at?: string | null
  /** File names, as `GET /downloads/{id}/files/{name}` takes them. */
  video: string | null
  cover: string | null
  images: string[]
  /**
   * The clips of an album's live photos, each named after its slide
   * (`image-01.mp4` beside `image-01.webp`). Optional: an older server does
   * not send it.
   */
  live_videos?: string[]
  /** The stored video's newest openable transcript, if it has one. */
  transcription?: ExistingTranscription | null
}

interface ArchivePage {
  items: ArchivedRow[]
  cursor: string | null
  has_more: boolean
}

interface ArchiveStats {
  contents: number
  authors: number
  by_platform: Record<string, number>
  by_availability: Record<string, number>
  /**
   * How many of the above this instance is holding the media for. The archive
   * is what was seen and a download is what was kept, so "46 douyin posts" was
   * being read as 46 videos on the disk - which is the number somebody watching
   * a volume fill up actually wants.
   */
  stored: number
  stored_by_platform: Record<string, number>
}

interface Filters {
  platform: string
  kind: string
  duration_bucket: string
  availability: string
  /** A collection id. The one filter here that is not a property of the post. */
  collection: string
  /** '' for both, 'true' for downloaded only, 'false' for the rest. */
  stored: string
  q: string
  /** One author's posts, by the platform's stable id - never the nickname. */
  author_uid: string
  /** Posts carrying this tag, without the `#`. */
  tag: string
}

/** How long a small download usually takes to land, measured on a 9MB post. */
const STORE_SETTLE_MS = 4000

/** One empty page, so a page still loading keeps the same identity each render. */
const NO_ROWS: ArchivedRow[] = []

/** Posts per call to the selection endpoints - the API's own ceiling. */
const SELECTION_CHUNK = 500

/** The bulk actions that go one request per post, with a running count. */
type BulkAction = 'store' | 'transcribe'

const NO_FILTERS: Filters = {
  platform: '',
  kind: '',
  duration_bucket: '',
  availability: '',
  collection: '',
  stored: '',
  q: '',
  author_uid: '',
  tag: '',
}

/**
 * The URL name of each filter. The API's, except the author, which links from
 * other pages name `author` (C4 in the console UX notes).
 */
const PARAM_OF: Readonly<Record<keyof Filters, string>> = {
  platform: 'platform',
  kind: 'kind',
  duration_bucket: 'duration_bucket',
  availability: 'availability',
  collection: 'collection',
  stored: 'stored',
  q: 'q',
  author_uid: 'author',
  tag: 'tag',
}

/** Values a menu can hold; anything else in a URL is dropped rather than sent. */
const KNOWN: Partial<Record<keyof Filters, readonly string[]>> = {
  platform: PLATFORMS,
  kind: KINDS,
  duration_bucket: DURATIONS,
  availability: AVAILABILITIES,
  stored: ['true', 'false'],
}

/**
 * The filters a URL asks for.
 *
 * A link to one post - `?platform=&content_id=` - names its platform too, and
 * that platform is the drawer's, not a filter: read as one, opening a TikTok
 * post from the downloads page would also have hidden every Douyin post behind
 * it, and closing the drawer would have left the list narrowed for no reason
 * anybody chose.
 */
function filtersFrom(params: URLSearchParams): Filters {
  const linked = params.has('content_id')
  const read = (field: keyof Filters): string => {
    if (field === 'platform' && linked) return ''
    const value = params.get(PARAM_OF[field]) ?? ''
    const known = KNOWN[field]
    return known && !known.includes(value) ? '' : value
  }
  return {
    platform: read('platform'),
    kind: read('kind'),
    duration_bucket: read('duration_bucket'),
    availability: read('availability'),
    collection: read('collection'),
    stored: read('stored'),
    q: read('q'),
    author_uid: read('author_uid'),
    tag: read('tag'),
  }
}

/** The URL patch that applies `next` in full. */
function filterPatch(next: Filters): Record<string, string | null> {
  const patch: Record<string, string | null> = {}
  for (const field of Object.keys(PARAM_OF) as Array<keyof Filters>) {
    patch[PARAM_OF[field]] = next[field] || null
  }
  return patch
}

/** The post a URL opens: `post=platform:id` from this page, or a C4 link. */
function openedFrom(params: URLSearchParams): { platform: string; content_id: string } | null {
  const post = params.get('post')
  if (post && post.includes(':')) return refOf(post)
  const contentId = params.get('content_id')
  const platform = params.get('platform')
  return contentId && platform ? { platform, content_id: contentId } : null
}

const COLLECTIONS_KEY = ['archive', 'collections'] as const

/**
 * The menus that apply the moment they change.
 *
 * Declared as data rather than written out five times, because the thing that
 * makes them a group is precisely that they behave identically: each is a
 * closed set of values, so picking one IS the decision and there is nothing for
 * a subsequent button press to add.
 */
const MENUS: ReadonlyArray<{
  field: 'platform' | 'kind' | 'duration_bucket' | 'availability'
  label: string
  blank: string
  /** Option labels are catalogue keys unless this says they are literal text. */
  literal?: boolean
  options: ReadonlyArray<{ value: string; label: string }>
}> = [
  {
    field: 'platform',
    label: 'library.column.post',
    blank: 'library.filter.anyPlatform',
    // Platform names are wire values and are never translated (doc 14).
    literal: true,
    options: PLATFORMS.map((name) => ({ value: name, label: name })),
  },
  {
    field: 'kind',
    label: 'library.column.kind',
    blank: 'library.filter.anyKind',
    options: KINDS.map((kind) => ({ value: kind, label: `library.kind.${kind}` })),
  },
  {
    field: 'duration_bucket',
    label: 'library.column.duration',
    blank: 'library.filter.anyDuration',
    options: DURATIONS.map((bucket) => ({ value: bucket, label: `library.duration.${bucket}` })),
  },
  {
    field: 'availability',
    label: 'library.column.availability',
    blank: 'library.filter.anyAvailability',
    options: AVAILABILITIES.map((state) => ({
      value: state,
      label: `library.availability.${state}`,
    })),
  },
]

/** The key a selected post is held under. Neither half is unique on its own. */
function keyOf(row: { platform: string; content_id: string }): string {
  return `${row.platform}:${row.content_id}`
}

/** Back to the pair the API takes. */
function refOf(key: string): { platform: string; content_id: string } {
  const cut = key.indexOf(':')
  return { platform: key.slice(0, cut), content_id: key.slice(cut + 1) }
}

function queryOf(filters: Filters, cursor: string | null): Record<string, string> {
  const params: Record<string, string> = {}
  for (const [key, value] of Object.entries(filters)) if (value) params[key] = value
  if (cursor) params['cursor'] = cursor
  return params
}

export default function Library() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()

  // The filters and the open post live in the URL, so the other pages can
  // link to an author, a tag or one post, and a narrowed view survives a
  // reload. Only the search text has a draft: the menus apply as they change.
  const [params, setParams] = useSearchParams()
  const setUrl = useCallback(
    (patch: Record<string, string | null>) => {
      setParams((prev) => patchParams(prev, patch), { replace: true })
    },
    [setParams],
  )
  const applied = useMemo(() => filtersFrom(params), [params])
  const [draftQ, setDraftQ] = useState(applied.q)
  const [seenQ, setSeenQ] = useState(applied.q)
  if (seenQ !== applied.q) {
    setSeenQ(applied.q)
    setDraftQ(applied.q)
  }
  const draft: Filters = { ...applied, q: draftQ }
  // Every cursor walked so far, so Back is a step in the list rather than a
  // second request the server cannot answer - keyset paging is forward-only.
  // Kept with the filters it was walked under, so new ones start at page one.
  const filterKey = JSON.stringify(applied)
  const [walk, setWalk] = useState<{ key: string; trail: Array<string | null>; page: number }>(
    { key: filterKey, trail: [null], page: 0 },
  )
  const trail = walk.key === filterKey ? walk.trail : [null]
  const page = walk.key === filterKey ? walk.page : 0
  const opened = openedFrom(params)
  // Covers by default: an archive of posts looks like a wall of posts, and the
  // material for it - title, cover, author, duration - is already on the row.
  // The table stays for the questions a grid cannot answer, like sorting by
  // duration or scanning availability down a column.
  const [view, setView] = useState<ViewMode>('grid')
  const [groupBy, setGroupBy] = useState<GroupBy>('none')
  // Selection is by key rather than by row, so it survives a poll replacing the
  // objects underneath it. It is deliberately NOT cleared when the page turns:
  // "select some here, some there, then act" is the whole point of it.
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [managingCollections, setManagingCollections] = useState(false)

  // What is actually narrowing the list. Shown beside Clear, because a filter
  // set two scrolls ago is the usual reason a wall of 66 posts shows four.
  const activeFilters = Object.values(applied).filter(Boolean).length

  const cursor = trail[page] ?? null

  const stats = useApiQuery<ArchiveStats>({ key: STATS_KEY, path: paths.archive.stats })

  const collections = useApiQuery<{ items: CollectionRow[] }>({
    key: COLLECTIONS_KEY,
    path: paths.archive.collections,
  })

  const list = useApiQuery<ArchivePage>({
    key: ['archive', 'list', applied, cursor],
    path: paths.archive.list,
    params: queryOf(applied, cursor),
  })

  /** Apply a whole set of filters. The open post closes: it may not be in the result. */
  const search = useCallback(
    (next: Filters) => {
      setUrl({ ...filterPatch(next), post: null, content_id: null })
    },
    [setUrl],
  )

  /** Narrow to one field's value, keeping everything else that is set. */
  const narrow = useCallback(
    (field: 'author_uid' | 'tag', value: string): void => {
      search({ ...applied, [field]: value })
    },
    [applied, search],
  )

  const openPost = (row: ArchivedRow): void => {
    // A C4 link's platform belongs to the drawer it opened, and goes with it.
    setUrl({
      post: keyOf(row),
      content_id: null,
      ...(params.has('content_id') ? { platform: null } : {}),
    })
  }

  const closePost = (): void => {
    setUrl({
      post: null,
      content_id: null,
      ...(params.has('content_id') ? { platform: null } : {}),
    })
  }

  const store = useApiMutation<{ download_id: string }, ArchivedRow>(
    (row) =>
      apiPost(
        paths.downloads.create,
        { platform: row.platform, content_id: row.content_id },
        { awaitTask: false },
      ),
    {
      onSuccess: () => {
        void invalidate(['downloads'])
        // The archive list too, because it is what carries `stored` - without
        // this the card never learned it had been saved, and a click produced
        // a toast over a page identical to the one before it.
        void invalidate(['archive'])
        // Downloading is a queued task, so the row is not stored yet when the
        // submission returns. One follow-up refresh covers the ordinary case;
        // a large video lands later and the next poll or navigation picks it
        // up, which is why the toast says where to look rather than promising
        // it is already there.
        window.setTimeout(() => {
          void invalidate(['archive'])
          void invalidate(['downloads'])
        }, STORE_SETTLE_MS)
        toast.success(t('library.toast.stored'))
      },
      onError: (error) => {
        toast.apiError(error, t('library.toast.storeFailed'))
      },
    },
  )

  const recheck = useApiMutation<{ task_id?: string }, void>(
    () => apiPost(paths.archive.recheck, {}, { awaitTask: false }),
    {
      onSuccess: () => {
        toast.success(t('library.toast.recheckStarted'))
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const backfill = useApiMutation<{ task_id?: string }, ArchivedRow>(
    (row) =>
      apiPost(
        paths.archive.backfill,
        { platform: row.platform, author_id: row.author.uid },
        { awaitTask: false },
      ),
    {
      onSuccess: (_result, row) => {
        toast.success(t('library.toast.backfillStarted'), {
          description: row.author.nickname || row.author.uid,
        })
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const exporting = useApiMutation<number, void>(
    async () => {
      // Streamed as newline-delimited JSON rather than assembled server-side:
      // an archive is meant to outgrow one response, and buffering it would
      // make the size of your own data the thing that breaks the export.
      const rows = await apiText(paths.archive.export, { params: queryOf(applied, null) })
      saveNdjson(rows, `archive-${new Date().toISOString().slice(0, 10)}.ndjson`)
      return rows.split('\n').filter(Boolean).length
    },
    {
      onSuccess: (count) => {
        toast.success(t('library.toast.exported', { count }))
      },
      onError: (error) => {
        toast.apiError(error, t('library.toast.exportFailed'))
      },
    },
  )

  /** Everything a bulk action needs, in the shape the API takes. */
  const chosen = useMemo(() => [...selected].map(refOf), [selected])

  const afterBulk = (message: string): void => {
    void invalidate(['archive'])
    void invalidate(['downloads'])
    setSelected(new Set())
    toast.success(message)
  }

  /**
   * The selected posts, exported as they are: picked across pages, sharing no
   * filter, so the filtered export above cannot express them. Sent in chunks
   * the size the API takes in one call, and joined into one file.
   */
  const exportSelected = useApiMutation<number, void>(
    async () => {
      let rows = ''
      for (let start = 0; start < chosen.length; start += SELECTION_CHUNK) {
        rows += await apiText(paths.archive.export, {
          body: { items: chosen.slice(start, start + SELECTION_CHUNK) },
        })
      }
      saveNdjson(rows, `archive-selected-${new Date().toISOString().slice(0, 10)}.ndjson`)
      return rows.split('\n').filter(Boolean).length
    },
    {
      onSuccess: (count) => {
        toast.success(t('library.toast.exported', { count }))
      },
      onError: (error) => {
        toast.apiError(error, t('library.toast.exportFailed'))
      },
    },
  )

  const session = useSession()
  // Hidden below viewer as the drawer's button is: the demo account may not
  // start a transcription, and could not open the page a success lands on.
  const mayTranscribe = atLeast(session.data?.role ?? null, 'viewer')
  const transcriber = useTranscriberState()

  //: A bulk save or transcription under way: which, how many, how far.
  const [bulkRun, setBulkRun] = useState<{
    action: BulkAction
    total: number
    tally: Tally
  } | null>(null)
  //: The running bulk action's stop flag, in a holder the unmount can reach.
  const bulkStop = useRef<{ flag: StopFlag; busy: boolean }>({
    flag: { stopped: false },
    busy: false,
  })

  // Leaving the page stops a bulk run rather than leaving it spending the
  // pool, one post at a time, with nothing left on screen to stop it.
  useEffect(() => {
    const holder = bulkStop.current
    return () => {
      holder.flag.stopped = true
    }
  }, [])

  /**
   * Run one request per selected post, one at a time, with a running count.
   *
   * There is no bulk download or bulk transcription endpoint, and each request
   * here can spend the identity pool or a transcriber slot, so it goes in turn
   * and can be stopped between any two - a hundred at once is what the
   * scheduler would otherwise have to undo.
   */
  const runBulk = async (
    action: BulkAction,
    call: (ref: { platform: string; content_id: string }) => Promise<'done' | 'skipped'>,
  ): Promise<void> => {
    // Set before anything is awaited: a second click in the same moment
    // would otherwise start a second run that Stop cannot reach.
    if (bulkStop.current.busy) return
    const flag: StopFlag = { stopped: false }
    bulkStop.current.flag = flag
    bulkStop.current.busy = true
    const refs = chosen
    setBulkRun({ action, total: refs.length, tally: NO_TALLY })
    const { tally, stopped } = await eachInTurn(refs, call, flag, (counted) => {
      setBulkRun({ action, total: refs.length, tally: counted })
    })
    bulkStop.current.busy = false
    setBulkRun(null)
    void invalidate(['archive'])
    void invalidate(['downloads'])
    // A save is a queued task, so the rows are not stored yet; one follow-up
    // refresh covers the ordinary case, as for a single save.
    window.setTimeout(() => {
      void invalidate(['archive'])
    }, STORE_SETTLE_MS)
    setSelected(new Set())
    const summary = t(`library.bulk.${action}Done`, {
      done: tally.done,
      skipped: tally.skipped,
      failed: tally.failed,
    })
    const details = [
      stopped ? t('library.bulk.stopped') : null,
      tally.firstError ? t('library.bulk.firstError', { message: tally.firstError }) : null,
    ].filter(Boolean)
    const description = details.length > 0 ? details.join(' ') : undefined
    if (tally.failed > 0) toast.warning(summary, { description })
    else toast.success(summary, { description })
  }

  const storeSelected = (): void => {
    void runBulk('store', async (ref) => {
      // skip_existing: what is already on the disk is reported, not fetched
      // twice - the same rule the author run on Downloads follows.
      const result: { reused?: string | null } = await apiPost(
        paths.downloads.create,
        { ...ref, skip_existing: true },
        { awaitTask: false },
      )
      return result.reused ? 'skipped' : 'done'
    })
  }

  const transcribeSelected = (): void => {
    void runBulk('transcribe', async (ref) => {
      // The row as this page has it, or read now when it was picked on
      // another page - only a video with stored media can be transcribed,
      // and the rest are counted as skipped rather than sent to be refused.
      const row =
        items.find((item) => keyOf(item) === keyOf(ref)) ??
        (await apiGet<ArchivedRow>(paths.archive.post(ref.platform, ref.content_id)))
      if (row.kind !== 'video' || !row.stored?.video) return 'skipped'
      await apiPost(
        paths.transcriptions.create,
        { download_id: row.stored.download_id, normalize: true, refresh: false },
        { awaitTask: false },
      )
      return 'done'
    })
  }

  const addToCollection = useApiMutation<{ added: number }, string>(
    (collectionId) => apiPost(paths.archive.collectionItems(collectionId), { items: chosen }),
    {
      onSuccess: (result) => {
        afterBulk(t('library.toast.addedToCollection', { count: result.added }))
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const removeFromCollection = useApiMutation<{ removed: number }, string>(
    (collectionId) =>
      apiPost(paths.archive.collectionItemsRemove(collectionId), { items: chosen }),
    {
      onSuccess: (result) => {
        afterBulk(t('library.toast.removedFromCollection', { count: result.removed }))
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const destroy = useApiMutation<{ deleted: number; freed_bytes: number }, void>(
    () => apiPost(paths.archive.delete, { items: chosen, media: true }),
    {
      onSuccess: (result) => {
        void invalidate(['archive', 'stats'])
        afterBulk(
          t('library.toast.deleted', {
            count: result.deleted,
            size: formatters.bytes(result.freed_bytes),
          }),
        )
      },
      onError: (error) => {
        // The server refuses rather than orphaning bytes when it cannot reach
        // the downloader, so the message is the useful part here.
        toast.apiError(error, t('library.toast.deleteFailed'))
      },
    },
  )

  const items = list.data?.items ?? NO_ROWS

  /**
   * Sections for the grid. "saved" buckets by the day this instance stored the
   * media rather than by when the platform published it - the question it
   * answers is "what did I pull down last night", and a post from 2023 saved
   * this morning belongs in this morning.
   */
  const groups = useMemo(() => {
    if (groupBy === 'none') return [{ key: 'all', label: null, rows: items }]
    const buckets = new Map<string, { label: string; rows: ArchivedRow[] }>()
    for (const row of items) {
      // When the files landed, not `last_seen_at`: that moves every time the
      // post is parsed or re-checked, so a recheck carried a month-old save
      // into today's group. An older server sends no `saved_at`, and the old
      // reading is all there is then.
      const saved = row.stored?.saved_at ?? row.last_seen_at
      const [key, label] =
        groupBy === 'author'
          ? [row.author.uid || 'unknown', row.author.nickname || row.author.uid || '—']
          : row.stored
            ? [saved.slice(0, 10), saved.slice(0, 10)]
            : ['unsaved', t('library.group.unsaved')]
      const bucket = buckets.get(key)
      if (bucket) bucket.rows.push(row)
      else buckets.set(key, { label, rows: [row] })
    }
    return [...buckets.entries()].map(([key, value]) => ({ key, ...value }))
  }, [items, groupBy, t])

  // The open post, from this page when it is on it and read on its own when
  // it is not - a link from Downloads or Transcriptions names any post.
  const listedPost = opened
    ? (items.find((row) => keyOf(row) === keyOf(opened)) ?? null)
    : null
  const single = useApiQuery<ArchivedRow>({
    key: ['archive', 'post', opened?.platform, opened?.content_id],
    path: opened ? paths.archive.post(opened.platform, opened.content_id) : paths.archive.list,
    enabled: opened !== null && listedPost === null && !list.isLoading,
    retry: false,
  })
  const inspecting = opened ? (listedPost ?? single.data ?? null) : null

  /** Who the author filter names, in words when any row on screen says. */
  const authorName =
    applied.author_uid
      ? (items.find((row) => row.author.uid === applied.author_uid)?.author.nickname ??
        (inspecting?.author.uid === applied.author_uid ? inspecting.author.nickname : null) ??
        applied.author_uid)
      : ''

  /** The stored video of whatever is open in the drawer, as a URL, or null. */
  const exportableVideo =
    inspecting?.stored?.video != null
      ? paths.downloads.file(inspecting.stored.download_id, inspecting.stored.video)
      : null

  const columns: Array<Column<ArchivedRow>> = useMemo(
    () => [
      {
        id: 'title',
        header: t('library.column.post'),
        width: '40%',
        cell: (row) => (
          <div className={styles.cell}>
            <span className="u-truncate" title={row.title}>
              {row.title || <span className="u-muted">{t('library.untitled')}</span>}
            </span>
            <span className={styles.sub}>
              <AuthorButton
                row={row}
                onPick={(uid) => {
                  narrow('author_uid', uid)
                }}
              />{' '}
              · {row.platform}
            </span>
          </div>
        ),
        sortValue: (row) => row.title,
      },
      {
        id: 'kind',
        header: t('library.column.kind'),
        width: '130px',
        cell: (row) => (
          <span className="u-xs">
            {t(`library.kind.${row.kind}`, { defaultValue: row.kind })}
          </span>
        ),
        sortValue: (row) => row.kind,
      },
      {
        id: 'duration',
        header: t('library.column.duration'),
        width: '110px',
        align: 'right',
        mono: true,
        cell: (row) =>
          row.duration_ms ? (
            formatters.duration(row.duration_ms)
          ) : (
            <span className="u-muted">—</span>
          ),
        sortValue: (row) => row.duration_ms ?? 0,
      },
      {
        id: 'availability',
        header: t('library.column.availability'),
        width: '120px',
        cell: (row) => (
          <span
            className="u-xs"
            style={{ color: row.availability === 'live' ? undefined : 'var(--text-muted)' }}
          >
            {t(`library.availability.${row.availability}`, { defaultValue: row.availability })}
          </span>
        ),
        sortValue: (row) => row.availability,
      },
      {
        id: 'created',
        header: t('library.column.published'),
        width: '180px',
        mono: true,
        cell: (row) =>
          row.created_at ? formatters.date(row.created_at) : <span className="u-muted">—</span>,
        sortValue: (row) => row.created_at ?? '',
      },
      {
        id: 'seen',
        header: t('library.column.lastSeen'),
        width: '180px',
        mono: true,
        cell: (row) => formatters.dateTime(row.last_seen_at),
        sortValue: (row) => row.last_seen_at,
      },
    ],
    [formatters, narrow, t],
  )

  const total = stats.data?.contents ?? 0

  return (
    <div className="u-page">
      <PageHeader
        title={t('library.title')}
        description={t('library.description')}
        actions={
          <>
            <Button
              variant="secondary"
              loading={recheck.isPending}
              disabled={total === 0}
              title={t('library.recheckHint')}
              onClick={() => {
                recheck.mutate()
              }}
            >
              {t('library.recheck')}
            </Button>
            <Button
              variant="secondary"
              loading={exporting.isPending}
              disabled={total === 0}
              onClick={() => {
                exporting.mutate()
              }}
            >
              {t('library.export')}
            </Button>
          </>
        }
      />

      <div className={styles.tiles}>
        {/* Every count of posts now says how many of them are on the disk.
            "Seen" and "kept" are different numbers and the page reported only
            the first, so an archive of 66 read as 66 videos stored here. The
            footer is a link rather than a note: the next thing you want after
            reading "12 of 66" is those twelve. */}
        <MetricTile
          label={t('library.metric.posts')}
          value={formatters.number(total)}
          loading={stats.isLoading}
          footer={
            <StoredFooter
              stored={stats.data?.stored ?? 0}
              of={total}
              onShow={() => {
                search({ ...NO_FILTERS, stored: 'true' })
              }}
            />
          }
        />
        <MetricTile
          label={t('library.metric.authors')}
          value={formatters.number(stats.data?.authors ?? 0)}
          loading={stats.isLoading}
          footer={<span className="u-xs u-muted">{t('library.metric.postsHint')}</span>}
        />
        <MetricTile
          label={t('library.metric.gone')}
          value={formatters.number(stats.data?.by_availability?.['deleted'] ?? 0)}
          loading={stats.isLoading}
          footer={<span className="u-xs u-muted">{t('library.metric.goneHint')}</span>}
          tone={(stats.data?.by_availability?.['deleted'] ?? 0) > 0 ? 'muted' : 'neutral'}
        />
        {PLATFORMS.map((name) => (
          <MetricTile
            key={name}
            label={name}
            value={formatters.number(stats.data?.by_platform[name] ?? 0)}
            loading={stats.isLoading}
            footer={
              <StoredFooter
                stored={stats.data?.stored_by_platform?.[name] ?? 0}
                of={stats.data?.by_platform[name] ?? 0}
                onShow={() => {
                  search({ ...NO_FILTERS, platform: name, stored: 'true' })
                }}
              />
            }
          />
        ))}
      </div>

      <Card title={t('library.filter.title')} description={t('library.filter.description')}>
        <form
          className={styles.filters}
          onSubmit={(event) => {
            event.preventDefault()
            search(draft)
          }}
        >
          {/* The search row spans the grid: it is the only control here that
              waits for a submit, so it needs its button beside it rather than
              lost among eight menus. Typing is not a decision until you stop,
              and firing a query per keystroke would page the archive on the way
              to a word; picking "douyin" from a menu IS the decision. */}
          <div className={styles.searchRow}>
            <Input
              value={draftQ}
              onChange={(event) => {
                setDraftQ(event.target.value)
              }}
              placeholder={t('library.filter.searchPlaceholder')}
              aria-label={t('library.filter.search')}
            />
            <Button type="submit" variant="primary">
              {t('library.filter.action')}
            </Button>
          </div>

          {/* Every menu labelled and on its own column. They were an unlabelled
              row of eight, which is a row you have to open one by one to find
              out what any of them narrows. */}
          <div className={styles.menus}>
            {MENUS.map(({ field, label, options, blank, literal }) => (
              <Select
                key={field}
                label={t(label)}
                value={draft[field]}
                onChange={(event) => {
                  // Applied on change, not on submit: `draft` and `applied`
                  // move together for these, and the text box is what keeps
                  // them apart.
                  search({ ...draft, [field]: event.target.value })
                }}
              >
                <option value="">{t(blank)}</option>
                {options.map(({ value, label: option }) => (
                  <option key={value} value={value}>
                    {literal ? option : t(option)}
                  </option>
                ))}
              </Select>
            ))}
            <Select
              label={t('library.collection.filterLabel')}
              value={draft.collection}
              onChange={(event) => {
                search({ ...draft, collection: event.target.value })
              }}
            >
              <option value="">{t('library.collection.any')}</option>
              {(collections.data?.items ?? []).map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name} ({row.items})
                </option>
              ))}
            </Select>
            <Select
              label={t('library.filter.storedLabel')}
              value={draft.stored}
              onChange={(event) => {
                search({ ...draft, stored: event.target.value })
              }}
            >
              <option value="">{t('library.filter.anyStored')}</option>
              <option value="true">{t('library.filter.storedOnly')}</option>
              <option value="false">{t('library.filter.notStoredOnly')}</option>
            </Select>
          </div>

          {/* The two filters with no menu: an author and a tag are picked by
              clicking one on a post, so they show here as chips, each with
              its own way back out. */}
          {applied.author_uid || applied.tag ? (
            <div className={styles.chips}>
              {applied.author_uid ? (
                <FilterChip
                  label={t('library.filter.authorChip', { name: authorName })}
                  removeLabel={t('library.filter.removeAuthor')}
                  onRemove={() => {
                    search({ ...draft, author_uid: '' })
                  }}
                />
              ) : null}
              {applied.tag ? (
                <FilterChip
                  label={t('library.filter.tagChip', { tag: applied.tag })}
                  removeLabel={t('library.filter.removeTag')}
                  onRemove={() => {
                    search({ ...draft, tag: '' })
                  }}
                />
              ) : null}
            </div>
          ) : null}

          {/* The two things that are not filters, kept off the filter grid so
              "clear" cannot be mistaken for one more thing to narrow by. */}
          <div className={styles.filterActions}>
            <span className="u-xs u-muted">
              {activeFilters > 0
                ? t('library.filter.activeCount', { count: activeFilters })
                : t('library.filter.noneActive')}
            </span>
            <span className="u-row">
              <Button
                variant="ghost"
                disabled={activeFilters === 0}
                onClick={() => {
                  search(NO_FILTERS)
                }}
              >
                {t('library.filter.clear')}
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setManagingCollections(true)
                }}
              >
                {t('library.collection.manage')}
              </Button>
            </span>
          </div>
        </form>
        <p className="u-xs u-muted">{t('library.filter.searchHint')}</p>
      </Card>

      <BulkBar
        selected={selected}
        collections={collections.data?.items ?? []}
        busy={
          addToCollection.isPending ||
          removeFromCollection.isPending ||
          destroy.isPending ||
          exportSelected.isPending ||
          bulkRun !== null
        }
        activeCollection={applied.collection}
        running={bulkRun}
        onStop={() => {
          bulkStop.current.flag.stopped = true
        }}
        onStore={storeSelected}
        onTranscribe={mayTranscribe ? transcribeSelected : null}
        transcribeBlocked={
          transcriber === 'off' || transcriber === 'down'
            ? t(`transcriptions.transcriber.${transcriber}Title`)
            : null
        }
        exporting={exportSelected.isPending}
        onExport={() => {
          exportSelected.mutate()
        }}
        onAdd={(id) => {
          addToCollection.mutate(id)
        }}
        onRemove={(id) => {
          removeFromCollection.mutate(id)
        }}
        onDelete={() => {
          setConfirmingDelete(true)
        }}
        onSelectPage={() => {
          setSelected(new Set([...selected, ...items.map(keyOf)]))
        }}
        onClear={() => {
          setSelected(new Set())
        }}
      />

      <div className="u-row-between u-wrap">
        <div className="u-row u-row-wrap" role="tablist" aria-label={t('library.view.label')}>
          {(['grid', 'table'] as const).map((mode) => (
            <Button
              key={mode}
              size="sm"
              role="tab"
              aria-selected={view === mode}
              variant={view === mode ? 'secondary' : 'ghost'}
              onClick={() => {
                setView(mode)
              }}
            >
              {t(`library.view.${mode}`)}
            </Button>
          ))}
        </div>
        {view === 'grid' ? (
          <Select
            aria-label={t('library.group.label')}
            value={groupBy}
            onChange={(event) => {
              setGroupBy(event.target.value as GroupBy)
            }}
            options={(['none', 'author', 'saved'] as const).map((value) => ({
              value,
              label: t(`library.group.${value}`),
            }))}
            style={{ width: '190px' }}
          />
        ) : null}
      </div>

      {view === 'table' ? (
        <DataTable
          columns={columns}
          rows={items}
          getRowId={keyOf}
          selectedIds={selected}
          onSelectionChange={setSelected}
          loading={list.isLoading}
          error={list.error}
          onRetry={() => {
            void list.refetch()
          }}
          onRowClick={openPost}
          storageKey="library"
          emptyTitle={
            activeFilters > 0 ? t('library.empty.filteredTitle') : t('library.empty.title')
          }
          emptyDescription={
            activeFilters > 0
              ? t('library.empty.filteredDescription')
              : t('library.empty.description')
          }
        />
      ) : list.isLoading ? (
        <div className={styles.grid}>
          {Array.from({ length: 12 }, (_, index) => (
            <Skeleton key={index} height={230} />
          ))}
        </div>
      ) : list.error ? (
        <ErrorState
          error={list.error}
          onRetry={() => {
            void list.refetch()
          }}
        />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            title={activeFilters > 0 ? t('library.empty.filteredTitle') : t('library.empty.title')}
            description={
              activeFilters > 0
                ? t('library.empty.filteredDescription')
                : t('library.empty.description')
            }
          />
        </Card>
      ) : (
        groups.map((group) => (
          <div key={group.key} className="u-stack">
            {group.label ? (
              <h2 className={styles.groupTitle}>
                {group.label}
                <span className={styles.groupCount}>
                  {t('library.group.count', { count: group.rows.length })}
                </span>
              </h2>
            ) : null}
            <div className={styles.grid}>
              {group.rows.map((row) => (
                <CoverCard
                  key={keyOf(row)}
                  row={row}
                  selected={selected.has(keyOf(row))}
                  onToggle={() => {
                    const next = new Set(selected)
                    if (next.has(keyOf(row))) next.delete(keyOf(row))
                    else next.add(keyOf(row))
                    setSelected(next)
                  }}
                  onOpen={openPost}
                  onAuthor={(uid) => {
                    narrow('author_uid', uid)
                  }}
                />
              ))}
            </div>
          </div>
        ))
      )}

      <ConfirmDialog
        open={confirmingDelete}
        danger
        title={t('library.delete.title')}
        description={t('library.delete.body', { count: selected.size })}
        confirmLabel={t('library.delete.action')}
        loading={destroy.isPending}
        onConfirm={() => {
          destroy.mutate()
          setConfirmingDelete(false)
        }}
        onCancel={() => {
          setConfirmingDelete(false)
        }}
      />

      <CollectionManager
        open={managingCollections}
        rows={collections.data?.items ?? []}
        onClose={() => {
          setManagingCollections(false)
        }}
        onChanged={() => {
          void invalidate(['archive'])
        }}
      />

      {(page > 0 || list.data?.has_more) && (
        <div className="u-row-between">
          <Button
            variant="secondary"
            disabled={page === 0}
            onClick={() => {
              setWalk({ key: filterKey, trail, page: Math.max(0, page - 1) })
            }}
          >
            {t('library.page.previous')}
          </Button>
          <span className="u-xs u-muted">{t('library.page.number', { page: page + 1 })}</span>
          <Button
            variant="secondary"
            disabled={!list.data?.has_more}
            onClick={() => {
              const next = list.data?.cursor ?? null
              if (!next) return
              setWalk({ key: filterKey, trail: [...trail.slice(0, page + 1), next], page: page + 1 })
            }}
          >
            {t('library.page.next')}
          </Button>
        </div>
      )}

      <Drawer
        open={opened !== null}
        onClose={closePost}
        title={inspecting ? inspecting.title || t('library.untitled') : (opened?.content_id ?? '')}
        description={opened ? `${opened.platform} · ${opened.content_id}` : undefined}
        footer={
          inspecting ? (
            <>
              <Button
                variant="secondary"
                loading={backfill.isPending}
                title={t('library.backfillHint')}
                onClick={() => {
                  backfill.mutate(inspecting)
                }}
              >
                {t('library.backfill')}
              </Button>
              {/* The button that said nothing. Saving a post fired a toast and
                  left a page that looked exactly as it had a second earlier,
                  which reads as the click not having registered. It now says
                  what is already on the disk, and offers to fetch it again
                  rather than pretending the post has never been saved. */}
              <Button
                variant={inspecting.stored ? 'secondary' : 'primary'}
                loading={store.isPending}
                title={
                  inspecting.stored
                    ? t('library.storedHint', {
                        size: formatters.bytes(inspecting.stored.bytes_total),
                      })
                    : undefined
                }
                onClick={() => {
                  store.mutate(inspecting)
                }}
              >
                {inspecting.stored ? t('library.storeAgain') : t('library.storeMedia')}
              </Button>
              {/* Two different destinations, one drawer, so both say where
                  they put things. The button above fetches a post onto the
                  server's media volume; this one takes what is already there
                  and puts it on the reader's own computer. They used to be
                  "store the media" and nothing, which left one verb covering
                  the only action available. */}
              {exportableVideo ? (
                <Button
                  variant="primary"
                  onClick={() => {
                    exportFile(exportableVideo)
                  }}
                >
                  {t('library.exportVideo')}
                </Button>
              ) : null}
            </>
          ) : undefined
        }
      >
        {inspecting ? (
          <Detail
            row={inspecting}
            onAuthor={(uid) => {
              narrow('author_uid', uid)
            }}
            onTag={(tag) => {
              narrow('tag', tag)
            }}
          />
        ) : single.error ? (
          <ErrorState error={single.error} compact />
        ) : (
          <p className="u-muted">{t('library.detail.loading')}</p>
        )}
      </Drawer>
    </div>
  )
}

/**
 * One archived post.
 *
 * The classification block is here because it is derived rather than fetched -
 * every value is a function of what the parser already returned, recomputable
 * from the stored payload if a rule changes. Showing it is how someone can
 * tell that "long" means a duration bucket and not a judgement.
 */
/**
 * One post as a cover tile.
 *
 * The cover comes from the disk when the post has been saved and from the
 * platform otherwise, and that order matters: an archived `cover_url` is a CDN
 * link that expires and may be hotlink-protected, so the copy this instance
 * kept is the one that still renders a year later. With neither, the tile says
 * so rather than showing a broken image.
 */
/** "N of M downloaded", and a way to see those N. */
function StoredFooter({
  stored,
  of,
  onShow,
}: {
  stored: number
  of: number
  onShow: () => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const formatters = useFormatters()

  if (of === 0) return null
  if (stored === 0) {
    return <span className="u-xs u-muted">{t('library.metric.noneStored')}</span>
  }
  return (
    <button type="button" className={styles.storedLink} onClick={onShow}>
      {t('library.metric.storedOf', {
        stored: formatters.number(stored),
        total: formatters.number(of),
      })}
    </button>
  )
}

/**
 * The bulk action bar.
 *
 * Present only while something is selected, and pinned to the bottom of the
 * viewport rather than placed above the grid: the selection is made by
 * scrolling through a wall of covers, and a bar at the top of the page is a bar
 * you have to scroll back to.
 */
function BulkBar({
  selected,
  collections,
  activeCollection,
  busy,
  running,
  onStop,
  onStore,
  onTranscribe,
  transcribeBlocked,
  exporting,
  onExport,
  onAdd,
  onRemove,
  onDelete,
  onSelectPage,
  onClear,
}: {
  selected: ReadonlySet<string>
  collections: readonly CollectionRow[]
  /** The collection being filtered by, if any: "remove from" needs a target. */
  activeCollection: string
  busy: boolean
  /** A save or transcription going post by post, or null. */
  running: { action: BulkAction; total: number; tally: Tally } | null
  onStop: () => void
  onStore: () => void
  /** Null below viewer, where the button would only ever be refused. */
  onTranscribe: (() => void) | null
  /** Why nothing can be transcribed on this instance, or null. */
  transcribeBlocked: string | null
  exporting: boolean
  onExport: () => void
  onAdd: (collectionId: string) => void
  onRemove: (collectionId: string) => void
  onDelete: () => void
  onSelectPage: () => void
  onClear: () => void
}) {
  const { t } = useTranslation(['console', 'common'])

  if (selected.size === 0 && running === null) return null

  // While one runs, the bar is its progress and its Stop: everything else
  // would act on a selection that is half done.
  if (running) {
    const acted = running.tally.done + running.tally.skipped + running.tally.failed
    return (
      <div className={styles.bulkBar} role="region" aria-label={t('library.bulk.label')}>
        <CompactProgress
          value={acted}
          max={running.total}
          label={t(`library.bulk.${running.action}Progress`, {
            done: acted,
            total: running.total,
          })}
        />
        <span className={styles.bulkSpacer} />
        <Button size="sm" variant="secondary" onClick={onStop}>
          {t('library.bulk.stop')}
        </Button>
      </div>
    )
  }

  return (
    <div className={styles.bulkBar} role="region" aria-label={t('library.bulk.label')}>
      <span className={styles.bulkCount}>{t('library.bulk.count', { count: selected.size })}</span>

      {/* What can be done to the posts themselves, before what files them. */}
      <Button
        size="sm"
        variant="secondary"
        disabled={busy}
        title={t('library.bulk.storeHint')}
        onClick={onStore}
      >
        {t('library.bulk.store')}
      </Button>
      {onTranscribe ? (
        <Button
          size="sm"
          variant="secondary"
          disabled={busy || transcribeBlocked !== null}
          title={transcribeBlocked ?? t('library.bulk.transcribeHint')}
          onClick={onTranscribe}
        >
          {t('library.bulk.transcribe')}
        </Button>
      ) : null}
      <Button size="sm" variant="secondary" disabled={busy} loading={exporting} onClick={onExport}>
        {t('library.bulk.export')}
      </Button>

      <Select
        aria-label={t('library.bulk.addTo')}
        value=""
        disabled={busy || collections.length === 0}
        onChange={(event) => {
          if (event.target.value) onAdd(event.target.value)
          // Reset to the prompt: this is an action menu, not a stored choice,
          // and leaving the last collection showing would read as a filter.
          event.target.value = ''
        }}
      >
        <option value="">
          {collections.length === 0 ? t('library.bulk.noCollections') : t('library.bulk.addTo')}
        </option>
        {collections.map((row) => (
          <option key={row.id} value={row.id}>
            {row.name}
          </option>
        ))}
      </Select>

      {/* Only offered while a collection is being viewed. "Remove from which
          one" has no answer otherwise, and a second menu that duplicated the
          first would be two menus one letter apart. */}
      {activeCollection ? (
        <Button
          size="sm"
          variant="secondary"
          disabled={busy}
          onClick={() => {
            onRemove(activeCollection)
          }}
        >
          {t('library.bulk.removeFrom')}
        </Button>
      ) : null}

      <span className={styles.bulkSpacer} />

      <Button size="sm" variant="ghost" onClick={onSelectPage}>
        {t('library.bulk.selectPage')}
      </Button>
      <Button size="sm" variant="ghost" onClick={onClear}>
        {t('library.bulk.clear')}
      </Button>
      <Button size="sm" variant="danger" disabled={busy} onClick={onDelete}>
        {t('library.bulk.delete')}
      </Button>
    </div>
  )
}

/**
 * Making, renaming and deleting the sets themselves.
 *
 * A modal rather than a page: this is housekeeping done occasionally, and the
 * thing an operator wants back afterwards is the wall they were looking at.
 */
function CollectionManager({
  open,
  rows,
  onClose,
  onChanged,
}: {
  open: boolean
  rows: readonly CollectionRow[]
  onClose: () => void
  onChanged: () => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const invalidate = useInvalidate()
  const [name, setName] = useState('')
  const [removing, setRemoving] = useState<CollectionRow | null>(null)

  const refresh = (): void => {
    void invalidate(COLLECTIONS_KEY)
    onChanged()
  }

  const create = useApiMutation<CollectionRow, string>(
    (value) => apiPost(paths.archive.collections, { name: value }),
    {
      onSuccess: () => {
        setName('')
        refresh()
        toast.success(t('library.collection.created'))
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const destroy = useApiMutation<{ deleted: boolean }, CollectionRow>(
    (row) => apiDelete(paths.archive.collection(row.id)),
    {
      onSuccess: () => {
        setRemoving(null)
        refresh()
        toast.success(t('library.collection.deleted'))
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  return (
    <Modal open={open} onClose={onClose} title={t('library.collection.manage')} size="md">
      <div className="u-stack">
        <form
          className="u-row"
          onSubmit={(event) => {
            event.preventDefault()
            if (name.trim()) create.mutate(name)
          }}
        >
          <Input
            value={name}
            onChange={(event) => {
              setName(event.target.value)
            }}
            placeholder={t('library.collection.namePlaceholder')}
            aria-label={t('library.collection.name')}
          />
          <Button type="submit" variant="primary" loading={create.isPending}>
            {t('library.collection.create')}
          </Button>
        </form>

        {rows.length === 0 ? (
          <EmptyState
            title={t('library.collection.emptyTitle')}
            description={t('library.collection.emptyDescription')}
          />
        ) : (
          <ul className={styles.collectionList}>
            {rows.map((row) => (
              <li key={row.id} className={styles.collectionRow}>
                <span className="u-stack-sm" style={{ minWidth: 0 }}>
                  <span className="u-truncate">{row.name}</span>
                  <span className="u-xs u-muted">
                    {t('library.collection.itemCount', { count: row.items })}
                  </span>
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setRemoving(row)
                  }}
                >
                  {t('common:action.delete')}
                </Button>
              </li>
            ))}
          </ul>
        )}

        {/* Worth spelling out: this is the one delete on the page that does not
            touch a single post or byte. */}
        <p className="u-xs u-muted" style={{ margin: 0 }}>
          {t('library.collection.deleteHint')}
        </p>
      </div>

      <ConfirmDialog
        open={removing !== null}
        danger
        title={t('library.collection.confirmTitle')}
        description={t('library.collection.confirmBody', { name: removing?.name ?? '' })}
        confirmLabel={t('common:action.delete')}
        loading={destroy.isPending}
        onConfirm={() => {
          if (removing) destroy.mutate(removing)
        }}
        onCancel={() => {
          setRemoving(null)
        }}
      />
    </Modal>
  )
}

function CoverCard({
  row,
  selected,
  onToggle,
  onOpen,
  onAuthor,
}: {
  row: ArchivedRow
  selected: boolean
  onToggle: () => void
  onOpen: (row: ArchivedRow) => void
  onAuthor: (uid: string) => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const formatters = useFormatters()
  const local = row.stored?.cover
    ? paths.downloads.file(row.stored.download_id, row.stored.cover)
    : null
  const cover = local ?? row.cover_url

  return (
    <div className={styles.cardShell} data-selected={selected}>
      {/* A sibling of the card button rather than a child: a checkbox inside a
          button is invalid, and clicking one would open the drawer as well. */}
      <label className={styles.select} onClick={(event) => event.stopPropagation()}>
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={t('library.bulk.selectOne', { title: row.title || row.content_id })}
        />
      </label>
      <button type="button" className={styles.card} onClick={() => onOpen(row)}>
      <span className={styles.thumb}>
        {cover ? (
          <img src={cover} alt="" loading="lazy" />
        ) : (
          <span className={styles.thumbEmpty}>{t('library.card.noCover')}</span>
        )}
        <span className={styles.badges}>
          <span className={styles.chip}>
            {row.duration_ms ? formatters.duration(row.duration_ms) : row.kind}
          </span>
          {row.stored ? (
            <span className={styles.chip} title={t('library.card.savedHint')}>
              {row.stored.video ? <PlayIcon size={10} /> : <DownloadIcon size={10} />}
              {formatters.bytes(row.stored.bytes_total)}
            </span>
          ) : null}
        </span>
      </span>
      <span className={styles.cardTitle}>{row.title || t('library.untitled')}</span>
      </button>
      {/* Outside the card's button, like the checkbox: the author is a second
          thing to click, and a button inside a button is invalid and would
          open the drawer as well. */}
      <span className={styles.cardMeta}>
        <AuthorButton row={row} onPick={onAuthor} />
        <span className="u-mono">{row.platform}</span>
      </span>
    </div>
  )
}

/**
 * An author's name that narrows the library to them.
 *
 * Plain text before - on the card, in the table and in the drawer - while the
 * archive API took an author filter the console never offered. The uid is
 * what is filtered on, never the nickname, which users change.
 */
function AuthorButton({ row, onPick }: { row: ArchivedRow; onPick: (uid: string) => void }) {
  const { t } = useTranslation(['console', 'common'])
  const name = row.author.nickname || row.author.uid
  if (!row.author.uid) return <span className="u-truncate">{name}</span>
  return (
    <button
      type="button"
      className={styles.inlineLink}
      title={t('library.filter.byAuthor', { name })}
      onClick={(event) => {
        // In a table row that opens the drawer on click, like Retry and Keep
        // on the downloads page.
        event.stopPropagation()
        onPick(row.author.uid)
      }}
    >
      {name}
    </button>
  )
}

/** One applied filter that has no menu, with its own way back out. */
function FilterChip({
  label,
  removeLabel,
  onRemove,
}: {
  label: string
  removeLabel: string
  onRemove: () => void
}) {
  return (
    <span className={styles.filterChip}>
      <span className="u-truncate">{label}</span>
      <button type="button" aria-label={removeLabel} title={removeLabel} onClick={onRemove}>
        <CrossIcon size={12} />
      </button>
    </span>
  )
}

/** Hand the browser newline-delimited JSON, as the archive export streams it. */
function saveNdjson(rows: string, filename: string): void {
  const href = URL.createObjectURL(new Blob([rows], { type: 'application/x-ndjson' }))
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

/**
 * Hand the browser a file this instance already holds.
 *
 * `download` with no value on purpose: same-origin, so the browser takes the
 * name out of the API's Content-Disposition - `dtk-<platform>-<id>-video.mp4`
 * - and the naming policy stays in one place, in dtk.media.plan, where curl
 * and every other client gets it too.
 */
function exportFile(url: string): void {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = ''
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

function Detail({
  row,
  onAuthor,
  onTag,
}: {
  row: ArchivedRow
  onAuthor: (uid: string) => void
  onTag: (tag: string) => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const formatters = useFormatters()

  /**
   * Played from this instance's own disk, never from the platform. The source
   * is `/downloads/{id}/files/{name}`, so there is no CDN link to expire, no
   * hotlink check to fail, and nobody outside this machine is told what is
   * being watched. `preload="metadata"` because a 240MB video should not start
   * transferring just because a drawer opened.
   */
  const video = row.stored?.video
    ? paths.downloads.file(row.stored.download_id, row.stored.video)
    : null
  const poster = row.stored?.cover
    ? paths.downloads.file(row.stored.download_id, row.stored.cover)
    : (row.cover_url ?? undefined)

  /**
   * The stored files worth handing over, in the order somebody wants them.
   *
   * The video is not here: it has a button of its own in the footer, and a
   * second link to the same bytes is clutter rather than a choice. The cover
   * is, because "the thumbnail" is a thing people ask for. A live photo's clip
   * follows its still, which the shared stem sorts it next to.
   */
  const stored = row.stored
    ? [
        ...[...row.stored.images, ...(row.stored.live_videos ?? [])].sort(),
        ...(row.stored.cover ? [row.stored.cover] : []),
      ]
    : []

  // The author is a filter in its own row below the facts, not a fact.
  const facts: Array<[string, string]> = [
    [t('library.column.published'), row.created_at ? formatters.dateTime(row.created_at) : '—'],
    [t('library.column.lastSeen'), formatters.dateTime(row.last_seen_at)],
    [t('library.detail.firstSeen'), formatters.dateTime(row.first_seen_at)],
    [
      t('library.column.duration'),
      row.duration_ms ? formatters.duration(row.duration_ms) : '—',
    ],
    // Derived values get translated like any other label. Leaving "fhd" and
    // "cjk" raw would make a deterministic classification read like an internal
    // enum somebody forgot about, which is exactly what it is not.
    [
      t('library.detail.orientation'),
      t(`library.orientationValue.${row.classification.orientation}`, {
        defaultValue: row.classification.orientation,
      }),
    ],
    [
      t('library.detail.resolution'),
      t(`library.resolutionValue.${row.classification.resolution_class}`, {
        defaultValue: row.classification.resolution_class,
      }),
    ],
    [
      t('library.detail.script'),
      t(`library.scriptValue.${row.classification.script}`, {
        defaultValue: row.classification.script,
      }),
    ],
    [t('library.column.availability'), t(`library.availability.${row.availability}`)],
    [t('library.detail.music'), row.music.title || '—'],
    [t('library.detail.location'), row.location || '—'],
  ]

  return (
    <div className="u-stack">
      {video ? (
        <video className={styles.player} src={video} poster={poster} controls preload="metadata" />
      ) : row.stored ? (
        <p className="u-xs u-muted">{t('library.detail.savedNoVideo')}</p>
      ) : null}

      {/* A video only. The transcriber reads the file stored here, never the
          platform's, so a post not saved yet cannot be transcribed - and a
          disabled button that says why beats one that fails on click. */}
      {row.kind === 'video' ? (
        <TranscribeButton
          downloadId={row.stored?.video ? row.stored.download_id : null}
          unavailable={
            !row.stored
              ? t('library.detail.transcribeUnsaved')
              : !row.stored.video
                ? t('downloads.transcription.noVideo')
                : null
          }
          existing={row.stored?.transcription ?? null}
        />
      ) : null}

      {/* The download behind the stored files - its digests, its state, its
          retry - lives on the Downloads page; this goes straight to it. */}
      {row.stored ? (
        <p className={styles.crossLinks}>
          <Link href={downloadHref(row.stored.download_id)}>{t('library.detail.openDownload')}</Link>
        </p>
      ) : null}

      {/* An image post has no video, so the footer's export button never
          appears for one - and the slides are just as stored and just as
          worth taking. One link each rather than one button: a browser will
          not start several downloads from a single click, and pretending
          otherwise would silently save one file out of thirty. */}
      {stored.length > 0 ? (
        <div className="u-stack-sm">
          <span className={styles.sub}>{t('library.detail.exportFiles')}</span>
          <ul className={styles.exports}>
            {stored.map((file) => (
              <li key={file}>
                <a href={paths.downloads.file(row.stored?.download_id ?? '', file)}>{file}</a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {row.description ? <p className={styles.description}>{row.description}</p> : null}

      <dl className={styles.facts}>
        <div className={styles.fact}>
          <dt className={styles.sub}>{t('library.detail.author')}</dt>
          <dd className="u-truncate">
            <AuthorButton row={row} onPick={onAuthor} />
          </dd>
        </div>
        {facts.map(([label, value]) => (
          <div key={label} className={styles.fact}>
            <dt className={styles.sub}>{label}</dt>
            <dd className="u-mono u-truncate">{value}</dd>
          </div>
        ))}
      </dl>

      {/* Each tag narrows the library to the posts carrying it - the archive
          API always took a tag filter, and the tags were plain text. */}
      {row.tags.length > 0 ? (
        <div className="u-row u-row-wrap">
          {row.tags.map((tag) => (
            <button
              key={tag}
              type="button"
              className={styles.tag}
              title={t('library.filter.byTag', { tag })}
              onClick={() => {
                onTag(tag)
              }}
            >
              #{tag}
            </button>
          ))}
        </div>
      ) : null}

      {/* The link that still resolves. The signed CDN links inside the stored
          manifest expire within hours, which is why none of them are here. */}
      <a href={row.web_url} target="_blank" rel="noreferrer noopener" className="u-xs">
        {t('library.detail.openOnPlatform')}
      </a>

      {row.availability !== 'live' ? (
        <EmptyState
          title={t(`library.availability.${row.availability}`)}
          description={t('library.detail.goneHint')}
        />
      ) : null}
    </div>
  )
}
