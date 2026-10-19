import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'

import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  Field,
  Input,
  MetricTile,
  Modal,
  PageHeader,
  Select,
  Switch,
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate } from '@/hooks'
import { apiDelete, apiPatch, apiPost } from '@/lib/api'
import { cn } from '@/lib/cn'
import { libraryAuthorHref, libraryPostHref } from '@/lib/contentLinks'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { TargetError, resolveLink } from '@/lib/targets'
import { PLATFORMS, type Platform } from '@/lib/types'

import styles from './Watchlist.module.css'

/**
 * Scheduled collection.
 *
 * This page is the only place in the console that creates *standing* work:
 * every other action happens once and finishes, and an entry here spends the
 * identity pool every few hours for as long as it exists. So the page is built
 * around making that cost legible - the interval is a first-class column, the
 * failure count is visible, and Pause all is a button rather than something
 * you assemble out of per-row toggles.
 *
 * What it collects is not configurable here, and that is deliberate: an author
 * entry fetches the post list because that carries the author record on every
 * item anyway, so one request answers both "what is new" and "how many
 * followers now".
 */

const WATCHLIST_KEY = ['admin', 'watchlist'] as const

const KINDS = ['author', 'content'] as const
type Kind = (typeof KINDS)[number]

/** Intervals worth offering. Anything below the server's floor is refused. */
const INTERVALS = [900, 3600, 6 * 3600, 12 * 3600, 24 * 3600] as const
/** What the form starts on until the server says what its default is. */
const FALLBACK_INTERVAL = 6 * 3600

/**
 * What a link to each looks like, per platform. The Douyin example used to be
 * the placeholder whatever was picked, on a page that takes three platforms.
 */
const PLACEHOLDER: Readonly<Record<Platform, Record<Kind, string>>> = {
  douyin: {
    author: 'https://www.douyin.com/user/MS4wLjABAAAA…',
    content: 'https://www.douyin.com/video/7408915107113127220',
  },
  tiktok: {
    author: 'https://www.tiktok.com/@handle',
    content: 'https://www.tiktok.com/@handle/video/7408915107113127220',
  },
  bilibili: {
    author: 'https://space.bilibili.com/2',
    content: 'https://www.bilibili.com/video/BV1xx411c7mD',
  },
}

/**
 * Whether the box holds a bare id rather than something to parse.
 *
 * Every id these platforms use for an author or a post - sec_user_id, secUid,
 * a numeric mid, a BV id, a post id - is letters, digits, `_` and `-`. A link
 * has a `/` or a `.` in it, and share text has spaces.
 */
const BARE_ID = /^[\w-]+$/

/**
 * The intervals a menu should offer: the standard ones the server accepts,
 * and whatever is chosen now. The default sat outside the menu whenever the
 * server's floor was above six hours, so the menu showed its first option and
 * the form submitted a value that was not on it.
 */
function intervalChoices(floor: number, extra: readonly number[]): number[] {
  const allowed = INTERVALS.filter((seconds) => seconds >= floor)
  return [...new Set([...allowed, ...extra.filter((seconds) => seconds >= floor)])].sort(
    (a, b) => a - b,
  )
}

interface WatchRow {
  id: string
  platform: string
  kind: string
  target_id: string
  label: string | null
  interval_seconds: number
  enabled: boolean
  pages: number
  next_run_at: string
  last_run_at: string | null
  last_error: string | null
  consecutive_failures: number
  runs: number
}

interface WatchList {
  items: WatchRow[]
  total: number
  stats: { entries: number; enabled: number; failing: number; runs: number }
  min_interval_seconds: number
  default_interval_seconds: number
  enabled: boolean
}

/**
 * Where the library keeps what this entry collects, or null.
 *
 * A post is keyed the same way in both. An author is too on Douyin and
 * Bilibili, where the id watched is the id archived. Not on TikTok: the post
 * list takes the `secUid`, and the archive keys TikTok authors by their
 * numeric id, so a link by the watched id would open an empty library.
 */
function libraryHref(row: WatchRow): string | null {
  if (row.kind === 'content') return libraryPostHref(row.platform, row.target_id)
  return row.platform === 'tiktok' ? null : libraryAuthorHref(row.target_id)
}

export default function Watchlist() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()

  const [platform, setPlatform] = useState<Platform>('douyin')
  const [kind, setKind] = useState<Kind>('author')
  //: A link or an id, read on submit - see `BARE_ID`.
  const [target, setTarget] = useState('')
  const [label, setLabel] = useState('')
  //: null until chosen: the server's default stands in for it until then.
  const [interval, setInterval] = useState<number | null>(null)
  const [pausing, setPausing] = useState(false)
  const [removing, setRemoving] = useState<WatchRow | null>(null)
  const [editing, setEditing] = useState<WatchRow | null>(null)

  const query = useApiQuery<WatchList>({
    key: WATCHLIST_KEY,
    path: paths.watchlist.list,
    poll: POLL.slow,
  })

  const floor = query.data?.min_interval_seconds ?? 0
  const serverDefault = query.data?.default_interval_seconds ?? FALLBACK_INTERVAL
  const choices = intervalChoices(floor, [serverDefault])
  // What the menu shows is what gets sent: the choice if there is one, the
  // server's default otherwise, and never a value the menu does not list.
  const chosenInterval =
    interval !== null && choices.includes(interval)
      ? interval
      : choices.includes(serverDefault)
        ? serverDefault
        : (choices[0] ?? serverDefault)

  const refresh = (): void => {
    void invalidate(WATCHLIST_KEY)
  }

  /**
   * The platform and id to watch. A bare id goes with the menu beside it; a
   * link is parsed - the platform it names wins over the menu, which is moved
   * to match so the form says what was sent.
   */
  const resolveTarget = async (): Promise<{ platform: Platform; id: string }> => {
    const text = target.trim()
    if (BARE_ID.test(text)) return { platform, id: text }
    try {
      const resolved = await resolveLink(text, kind === 'author' ? 'user' : 'video')
      setPlatform(resolved.platform)
      return resolved
    } catch (error) {
      if (!(error instanceof TargetError)) throw error
      throw new Error(t(`watchlist.target.${error.problem}`), { cause: error })
    }
  }

  const add = useApiMutation<WatchRow, void>(
    async () => {
      const resolved = await resolveTarget()
      return apiPost(paths.watchlist.create, {
        platform: resolved.platform,
        kind,
        target_id: resolved.id,
        interval_seconds: chosenInterval,
        ...(label.trim() ? { label: label.trim() } : {}),
      })
    },
    {
      onSuccess: () => {
        setTarget('')
        setLabel('')
        refresh()
        toast.success(t('watchlist.toast.added'))
      },
      onError: (error) => {
        toast.apiError(error, t('watchlist.toast.addFailed'))
      },
    },
  )

  const change = useApiMutation<WatchRow, { id: string; patch: Record<string, unknown> }>(
    ({ id, patch }) => apiPatch(paths.watchlist.byId(id), patch),
    {
      onSuccess: () => {
        refresh()
      },
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const remove = useApiMutation<unknown, string>((id) => apiDelete(paths.watchlist.byId(id)), {
    onSuccess: () => {
      setRemoving(null)
      refresh()
      toast.success(t('watchlist.toast.removed'))
    },
    onError: (error) => {
      toast.apiError(error)
    },
  })

  const pauseAll = useApiMutation<{ paused: number }, void>(
    () => apiPost(paths.watchlist.pause, {}),
    {
      onSuccess: (result) => {
        setPausing(false)
        refresh()
        toast.success(t('watchlist.toast.paused', { count: result.paused }))
      },
      onError: (error) => {
        setPausing(false)
        toast.apiError(error)
      },
    },
  )

  const columns: Array<Column<WatchRow>> = useMemo(
    () => [
      {
        id: 'target',
        header: t('watchlist.column.target'),
        width: '32%',
        cell: (row) => {
          const href = libraryHref(row)
          const name = row.label || row.target_id
          return (
            <div className={styles.cell}>
              {/* To what this entry has collected: the reason it exists. */}
              {href ? (
                <Link
                  href={href}
                  className={cn('u-truncate', styles.targetLink)}
                  title={t('watchlist.openInLibrary', { target: row.target_id })}
                >
                  {name}
                </Link>
              ) : (
                <span className="u-truncate" title={row.target_id}>
                  {name}
                </span>
              )}
              <span className={styles.sub}>
                {t(`watchlist.kind.${row.kind}`)} · {row.platform}
              </span>
            </div>
          )
        },
        sortValue: (row) => row.label ?? row.target_id,
      },
      {
        id: 'interval',
        header: t('watchlist.column.interval'),
        width: '130px',
        mono: true,
        cell: (row) => formatters.duration(row.interval_seconds * 1000),
        sortValue: (row) => row.interval_seconds,
      },
      {
        id: 'next',
        header: t('watchlist.column.next'),
        width: '170px',
        mono: true,
        cell: (row) =>
          row.enabled ? (
            formatters.relative(row.next_run_at)
          ) : (
            <span className="u-muted">{t('watchlist.paused')}</span>
          ),
        sortValue: (row) => row.next_run_at,
      },
      {
        id: 'runs',
        header: t('watchlist.column.runs'),
        width: '100px',
        align: 'right',
        mono: true,
        cell: (row) => formatters.number(row.runs),
        sortValue: (row) => row.runs,
      },
      {
        id: 'health',
        header: t('watchlist.column.health'),
        width: '210px',
        cell: (row) =>
          row.consecutive_failures > 0 ? (
            <span className={styles.failing} title={row.last_error ?? undefined}>
              {t('watchlist.failing', { count: row.consecutive_failures })}
            </span>
          ) : row.last_run_at ? (
            <span className="u-xs u-muted">{formatters.relative(row.last_run_at)}</span>
          ) : (
            <span className="u-xs u-muted">{t('watchlist.neverRun')}</span>
          ),
        sortValue: (row) => row.consecutive_failures,
      },
      {
        id: 'enabled',
        header: t('watchlist.column.enabled'),
        width: '110px',
        hideable: false,
        cell: (row) => (
          <Switch
            checked={row.enabled}
            disabled={change.isPending}
            aria-label={t('watchlist.toggleLabel', { target: row.label || row.target_id })}
            onChange={(event) => {
              change.mutate({ id: row.id, patch: { enabled: event.target.checked } })
            }}
          />
        ),
        sortValue: (row) => (row.enabled ? 1 : 0),
      },
      {
        id: 'actions',
        header: '',
        width: '170px',
        hideable: false,
        cell: (row) => (
          <span className="u-row">
            <Button
              size="sm"
              variant="ghost"
              aria-label={t('watchlist.editLabel', { target: row.label || row.target_id })}
              onClick={() => {
                setEditing(row)
              }}
            >
              {t('common:action.edit')}
            </Button>
            {/* Asks first. One click used to delete a schedule somebody had
                tuned, with nothing between the click and the loss. */}
            <Button
              size="sm"
              variant="ghost"
              loading={remove.isPending && remove.variables === row.id}
              onClick={() => {
                setRemoving(row)
              }}
            >
              {t('watchlist.remove')}
            </Button>
          </span>
        ),
      },
    ],
    [change, formatters, remove, t],
  )

  const stats = query.data?.stats
  const globallyOff = query.data ? !query.data.enabled : false

  return (
    <div className="u-page">
      <PageHeader
        title={t('watchlist.title')}
        description={t('watchlist.description')}
        actions={
          <Button
            variant="secondary"
            disabled={(stats?.enabled ?? 0) === 0}
            onClick={() => {
              setPausing(true)
            }}
          >
            {t('watchlist.pauseAll')}
          </Button>
        }
      />

      {globallyOff ? (
        <Card title={t('watchlist.offTitle')}>
          <p className="u-muted">{t('watchlist.offHint')}</p>
        </Card>
      ) : null}

      <div className={styles.tiles}>
        <MetricTile
          label={t('watchlist.metric.entries')}
          value={formatters.number(stats?.entries ?? 0)}
          loading={query.isLoading}
          footer={t('watchlist.metric.enabled', { count: stats?.enabled ?? 0 })}
        />
        <MetricTile
          label={t('watchlist.metric.runs')}
          value={formatters.number(stats?.runs ?? 0)}
          loading={query.isLoading}
          footer={t('watchlist.metric.runsHint')}
        />
        <MetricTile
          label={t('watchlist.metric.failing')}
          value={formatters.number(stats?.failing ?? 0)}
          loading={query.isLoading}
          tone={(stats?.failing ?? 0) > 0 ? 'warning' : 'neutral'}
          footer={t('watchlist.metric.failingHint')}
        />
      </div>

      <Card title={t('watchlist.add.title')} description={t('watchlist.add.description')}>
        {/* Five fields and a button, only some of which carry help text - the
            case a flex row gets wrong, putting those fields' controls 20px
            above their neighbours. u-form-row shares three rows between them. */}
        <form
          className={cn('u-form-row', styles.addForm)}
          onSubmit={(event) => {
            event.preventDefault()
            add.mutate()
          }}
        >
          <Field id="watch-platform" label={t('watchlist.field.platform')}>
            <Select
              value={platform}
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
          <Field id="watch-kind" label={t('watchlist.field.kind')}>
            <Select
              value={kind}
              onChange={(event) => {
                setKind(event.target.value as Kind)
              }}
            >
              {KINDS.map((value) => (
                <option key={value} value={value}>
                  {t(`watchlist.kind.${value}`)}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            id="watch-target"
            label={t('watchlist.field.target')}
            description={t(`watchlist.field.targetHint.${kind}`)}
          >
            <Input
              value={target}
              mono
              onChange={(event) => {
                setTarget(event.target.value)
              }}
              placeholder={PLACEHOLDER[platform][kind]}
            />
          </Field>
          <Field
            id="watch-label"
            label={t('watchlist.field.label')}
            description={t('watchlist.field.labelHint')}
          >
            <Input
              value={label}
              maxLength={128}
              onChange={(event) => {
                setLabel(event.target.value)
              }}
            />
          </Field>
          <Field id="watch-interval" label={t('watchlist.field.interval')}>
            <Select
              value={String(chosenInterval)}
              onChange={(event) => {
                setInterval(Number(event.target.value))
              }}
            >
              {choices.map((seconds) => (
                <option key={seconds} value={seconds}>
                  {formatters.duration(seconds * 1000)}
                </option>
              ))}
            </Select>
          </Field>
          <Button type="submit" variant="primary" loading={add.isPending} disabled={!target.trim()}>
            {t('watchlist.add.action')}
          </Button>
        </form>
        <p className="u-xs u-muted">{t('watchlist.add.costHint')}</p>
      </Card>

      <DataTable
        columns={columns}
        rows={query.data?.items}
        getRowId={(row) => row.id}
        loading={query.isLoading}
        error={query.error}
        onRetry={() => {
          void query.refetch()
        }}
        storageKey="watchlist"
        emptyTitle={t('watchlist.empty.title')}
        emptyDescription={t('watchlist.empty.description')}
      />

      <ConfirmDialog
        open={pausing}
        title={t('watchlist.pauseAll')}
        description={t('watchlist.pauseConfirm', { count: stats?.enabled ?? 0 })}
        confirmLabel={t('watchlist.pauseAll')}
        loading={pauseAll.isPending}
        onConfirm={() => {
          pauseAll.mutate()
        }}
        onCancel={() => {
          setPausing(false)
        }}
      />

      <ConfirmDialog
        open={removing !== null}
        danger
        title={t('watchlist.removeTitle', { target: removing?.label || removing?.target_id || '' })}
        description={t('watchlist.removeBody')}
        confirmLabel={t('watchlist.remove')}
        loading={remove.isPending}
        onConfirm={() => {
          if (removing) remove.mutate(removing.id)
        }}
        onCancel={() => {
          setRemoving(null)
        }}
      />

      {editing ? (
        <EditEntry
          // Remounted per entry, so the form starts from the row it edits.
          key={editing.id}
          row={editing}
          floor={floor}
          onClose={() => {
            setEditing(null)
          }}
          onSaved={() => {
            setEditing(null)
            refresh()
            toast.success(t('watchlist.toast.saved'))
          }}
        />
      ) : null}
    </div>
  )
}

/**
 * Rename an entry, or change how often it runs.
 *
 * The page offered neither, so changing an interval meant removing the entry
 * - and its failure history - and adding it again.
 *
 * Not the page depth, although the API stores one: a scheduled run reads one
 * page of the newest posts whatever `pages` says (`watchlist.task_for`), and a
 * control that changes nothing would be a promise the worker does not keep.
 */
function EditEntry({
  row,
  floor,
  onClose,
  onSaved,
}: {
  row: WatchRow
  floor: number
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const [label, setLabel] = useState(row.label ?? '')
  const [interval, setInterval] = useState(row.interval_seconds)
  // The entry's own interval stays on the menu even when it is off the
  // standard list, or the menu would show one value and save another.
  const choices = intervalChoices(floor, [row.interval_seconds])

  const save = useApiMutation<WatchRow, void>(
    () => {
      // Only what changed. An unchanged label in particular is left out
      // rather than resent: sending it would be harmless, sending '' for an
      // auto-filled one would clear it.
      const patch: Record<string, unknown> = {}
      if (label.trim() !== (row.label ?? '')) patch['label'] = label.trim() || null
      if (interval !== row.interval_seconds) patch['interval_seconds'] = interval
      return apiPatch(paths.watchlist.byId(row.id), patch)
    },
    {
      onSuccess: onSaved,
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  return (
    <Modal
      open
      onClose={onClose}
      title={t('watchlist.edit.title')}
      description={`${t(`watchlist.kind.${row.kind}`)} · ${row.platform} · ${row.target_id}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common:action.cancel')}
          </Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => {
              save.mutate()
            }}
          >
            {t('common:action.save')}
          </Button>
        </>
      }
    >
      <form
        className="u-stack"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <Input
          label={t('watchlist.field.label')}
          description={t('watchlist.edit.labelHint')}
          value={label}
          maxLength={128}
          onChange={(event) => {
            setLabel(event.target.value)
          }}
        />
        <Select
          label={t('watchlist.field.interval')}
          value={String(interval)}
          onChange={(event) => {
            setInterval(Number(event.target.value))
          }}
        >
          {choices.map((seconds) => (
            <option key={seconds} value={seconds}>
              {formatters.duration(seconds * 1000)}
            </option>
          ))}
        </Select>
      </form>
    </Modal>
  )
}
