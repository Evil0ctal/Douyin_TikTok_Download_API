import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  DataTable,
  Drawer,
  EmptyState,
  Input,
  MetricTile,
  PageHeader,
  Select,
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate } from '@/hooks'
import { apiPost, apiText } from '@/lib/api'
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
  q: string
}

const NO_FILTERS: Filters = { platform: '', kind: '', duration_bucket: '', availability: '', q: '' }

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

  const cursor = trail[page] ?? null

  const stats = useApiQuery<ArchiveStats>({ key: STATS_KEY, path: paths.archive.stats })

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
    <>
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
          className="u-row"
          onSubmit={(event) => {
            event.preventDefault()
            search(draft)
          }}
        >
          <Input
            value={draft.q}
            onChange={(event) => {
              setDraft({ ...draft, q: event.target.value })
            }}
            placeholder={t('library.filter.searchPlaceholder')}
            aria-label={t('library.filter.search')}
          />
          <Select
            value={draft.platform}
            aria-label={t('library.column.post')}
            onChange={(event) => {
              setDraft({ ...draft, platform: event.target.value })
            }}
          >
            <option value="">{t('library.filter.anyPlatform')}</option>
            {PLATFORMS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
          <Select
            value={draft.kind}
            aria-label={t('library.column.kind')}
            onChange={(event) => {
              setDraft({ ...draft, kind: event.target.value })
            }}
          >
            <option value="">{t('library.filter.anyKind')}</option>
            {KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {t(`library.kind.${kind}`)}
              </option>
            ))}
          </Select>
          <Select
            value={draft.duration_bucket}
            aria-label={t('library.column.duration')}
            onChange={(event) => {
              setDraft({ ...draft, duration_bucket: event.target.value })
            }}
          >
            <option value="">{t('library.filter.anyDuration')}</option>
            {DURATIONS.map((bucket) => (
              <option key={bucket} value={bucket}>
                {t(`library.duration.${bucket}`)}
              </option>
            ))}
          </Select>
          <Select
            value={draft.availability}
            aria-label={t('library.column.availability')}
            onChange={(event) => {
              setDraft({ ...draft, availability: event.target.value })
            }}
          >
            <option value="">{t('library.filter.anyAvailability')}</option>
            {AVAILABILITIES.map((state) => (
              <option key={state} value={state}>
                {t(`library.availability.${state}`)}
              </option>
            ))}
          </Select>
          <Button type="submit" variant="primary">
            {t('library.filter.action')}
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setDraft(NO_FILTERS)
              search(NO_FILTERS)
            }}
          >
            {t('library.filter.clear')}
          </Button>
        </form>
        <p className="u-xs u-muted">{t('library.filter.searchHint')}</p>
      </Card>

      <DataTable
        columns={columns}
        rows={list.data?.items}
        getRowId={(row) => `${row.platform}:${row.content_id}`}
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
              <Button
                variant="primary"
                loading={store.isPending}
                onClick={() => {
                  store.mutate(inspecting)
                }}
              >
                {t('library.storeMedia')}
              </Button>
            </>
          ) : undefined
        }
      >
        {inspecting ? <Detail row={inspecting} /> : null}
      </Drawer>
    </>
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
function Detail({ row }: { row: ArchivedRow }) {
  const { t } = useTranslation(['console', 'common'])
  const formatters = useFormatters()

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
