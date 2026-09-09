import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  ConfirmDialog,
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
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate } from '@/hooks'
import { apiDelete, apiPost, apiText } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import type { Platform } from '@/lib/types'

import styles from './Library.module.css'

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

const PLATFORMS: readonly Platform[] = ['douyin', 'tiktok']
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
  /** File names, as `GET /downloads/{id}/files/{name}` takes them. */
  video: string | null
  cover: string | null
  images: string[]
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
}

interface Filters {
  platform: string
  kind: string
  duration_bucket: string
  availability: string
  /** A collection id. The one filter here that is not a property of the post. */
  collection: string
  q: string
}

/** How long a small download usually takes to land, measured on a 9MB post. */
const STORE_SETTLE_MS = 4000

const NO_FILTERS: Filters = {
  platform: '',
  kind: '',
  duration_bucket: '',
  availability: '',
  collection: '',
  q: '',
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

  const [draft, setDraft] = useState<Filters>(NO_FILTERS)
  const [applied, setApplied] = useState<Filters>(NO_FILTERS)
  // Every cursor walked so far, so Back is a step in the list rather than a
  // second request the server cannot answer - keyset paging is forward-only.
  const [trail, setTrail] = useState<Array<string | null>>([null])
  const [page, setPage] = useState(0)
  const [inspecting, setInspecting] = useState<ArchivedRow | null>(null)
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

  const search = useCallback(
    (next: Filters) => {
      setApplied(next)
      setTrail([null])
      setPage(0)
    },
    [setApplied],
  )

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
      const blob = new Blob([rows], { type: 'application/x-ndjson' })
      const href = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = href
      anchor.download = `archive-${new Date().toISOString().slice(0, 10)}.ndjson`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => {
        URL.revokeObjectURL(href)
      }, 1_000)
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

  const afterBulk = useCallback(
    (message: string) => {
      void invalidate(['archive'])
      void invalidate(['downloads'])
      setSelected(new Set())
      toast.success(message)
    },
    [invalidate, toast],
  )

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

  const items = useMemo(() => list.data?.items ?? [], [list.data])

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
      const [key, label] =
        groupBy === 'author'
          ? [row.author.uid || 'unknown', row.author.nickname || row.author.uid || '—']
          : row.stored
            ? [row.last_seen_at.slice(0, 10), row.last_seen_at.slice(0, 10)]
            : ['unsaved', t('library.group.unsaved')]
      const bucket = buckets.get(key)
      if (bucket) bucket.rows.push(row)
      else buckets.set(key, { label, rows: [row] })
    }
    return [...buckets.entries()].map(([key, value]) => ({ key, ...value }))
  }, [items, groupBy, t])

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
              {row.author.nickname || row.author.uid} · {row.platform}
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
    [formatters, t],
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
        <MetricTile
          label={t('library.metric.posts')}
          value={formatters.number(total)}
          loading={stats.isLoading}
          footer={t('library.metric.postsHint')}
        />
        <MetricTile
          label={t('library.metric.authors')}
          value={formatters.number(stats.data?.authors ?? 0)}
          loading={stats.isLoading}
        />
        <MetricTile
          label={t('library.metric.gone')}
          value={formatters.number(stats.data?.by_availability?.['deleted'] ?? 0)}
          loading={stats.isLoading}
          footer={t('library.metric.goneHint')}
          tone={(stats.data?.by_availability?.['deleted'] ?? 0) > 0 ? 'muted' : 'neutral'}
        />
        {PLATFORMS.map((name) => (
          <MetricTile
            key={name}
            label={name}
            value={formatters.number(stats.data?.by_platform[name] ?? 0)}
            loading={stats.isLoading}
          />
        ))}
      </div>

      <Card title={t('library.filter.title')} description={t('library.filter.description')}>
        <form
          className="u-row u-wrap"
          onSubmit={(event) => {
            event.preventDefault()
            search(draft)
          }}
        >
          {/* The text box is the only control that still waits for a submit.
              Typing is not a decision until you stop, and firing a query per
              keystroke would page the archive on the way to a word; picking
              "douyin" from a menu IS the decision, and making somebody confirm
              it afterwards is a step that carries no information. */}
          <Input
            value={draft.q}
            onChange={(event) => {
              setDraft({ ...draft, q: event.target.value })
            }}
            placeholder={t('library.filter.searchPlaceholder')}
            aria-label={t('library.filter.search')}
          />
          <Button type="submit" variant="primary">
            {t('library.filter.action')}
          </Button>
          {MENUS.map(({ field, label, options, blank, literal }) => (
            <Select
              key={field}
              value={draft[field]}
              aria-label={t(label)}
              onChange={(event) => {
                // Applied on change, not on submit: `draft` and `applied` move
                // together for these, and the text box is what keeps them apart.
                const next = { ...draft, [field]: event.target.value }
                setDraft(next)
                search(next)
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
            value={draft.collection}
            aria-label={t('library.collection.filterLabel')}
            onChange={(event) => {
              const next = { ...draft, collection: event.target.value }
              setDraft(next)
              search(next)
            }}
          >
            <option value="">{t('library.collection.any')}</option>
            {(collections.data?.items ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.name} ({row.items})
              </option>
            ))}
          </Select>
          <Button
            variant="ghost"
            onClick={() => {
              setDraft(NO_FILTERS)
              search(NO_FILTERS)
            }}
          >
            {t('library.filter.clear')}
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setManagingCollections(true)
            }}
          >
            {t('library.collection.manage')}
          </Button>
        </form>
        <p className="u-xs u-muted">{t('library.filter.searchHint')}</p>
      </Card>

      <BulkBar
        selected={selected}
        collections={collections.data?.items ?? []}
        busy={addToCollection.isPending || removeFromCollection.isPending || destroy.isPending}
        activeCollection={applied.collection}
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
        <div className="u-row" role="tablist" aria-label={t('library.view.label')}>
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
          onRowClick={setInspecting}
          storageKey="library"
          emptyTitle={t('library.empty.title')}
          emptyDescription={t('library.empty.description')}
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
            title={t('library.empty.title')}
            description={t('library.empty.description')}
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
                  onOpen={setInspecting}
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
              setPage((value) => Math.max(0, value - 1))
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
              setTrail((value) => [...value.slice(0, page + 1), next])
              setPage((value) => value + 1)
            }}
          >
            {t('library.page.next')}
          </Button>
        </div>
      )}

      <Drawer
        open={inspecting !== null}
        onClose={() => {
          setInspecting(null)
        }}
        title={inspecting?.title || t('library.untitled')}
        description={inspecting ? `${inspecting.platform} · ${inspecting.content_id}` : undefined}
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
            </>
          ) : undefined
        }
      >
        {inspecting ? <Detail row={inspecting} /> : null}
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
  onAdd: (collectionId: string) => void
  onRemove: (collectionId: string) => void
  onDelete: () => void
  onSelectPage: () => void
  onClear: () => void
}) {
  const { t } = useTranslation(['console', 'common'])

  if (selected.size === 0) return null

  return (
    <div className={styles.bulkBar} role="region" aria-label={t('library.bulk.label')}>
      <span className={styles.bulkCount}>{t('library.bulk.count', { count: selected.size })}</span>

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
}: {
  row: ArchivedRow
  selected: boolean
  onToggle: () => void
  onOpen: (row: ArchivedRow) => void
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
      <span className={styles.cardMeta}>
        <span className="u-truncate">{row.author.nickname || row.author.uid}</span>
        <span className="u-mono">{row.platform}</span>
      </span>
      </button>
    </div>
  )
}


function Detail({ row }: { row: ArchivedRow }) {
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

  const facts: Array<[string, string]> = [
    [t('library.detail.author'), row.author.nickname || row.author.uid],
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

      {row.description ? <p className={styles.description}>{row.description}</p> : null}

      <dl className={styles.facts}>
        {facts.map(([label, value]) => (
          <div key={label} className={styles.fact}>
            <dt className={styles.sub}>{label}</dt>
            <dd className="u-mono u-truncate">{value}</dd>
          </div>
        ))}
      </dl>

      {row.tags.length > 0 ? (
        <div className="u-row">
          {row.tags.map((tag) => (
            <span key={tag} className={styles.tag}>
              #{tag}
            </span>
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
