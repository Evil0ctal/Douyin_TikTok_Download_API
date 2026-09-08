import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  Field,
  Input,
  MetricTile,
  PageHeader,
  Select,
  Switch,
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate } from '@/hooks'
import { apiDelete, apiPatch, apiPost } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import type { Platform } from '@/lib/types'

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

const PLATFORMS: readonly Platform[] = ['douyin', 'tiktok']
const KINDS = ['author', 'content'] as const

/** Intervals worth offering. Anything below the server's floor is refused. */
const INTERVALS = [900, 3600, 6 * 3600, 12 * 3600, 24 * 3600] as const

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

export default function Watchlist() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()

  const [platform, setPlatform] = useState<Platform>('douyin')
  const [kind, setKind] = useState<(typeof KINDS)[number]>('author')
  const [targetId, setTargetId] = useState('')
  const [interval, setInterval] = useState<number>(6 * 3600)
  const [pausing, setPausing] = useState(false)

  const query = useApiQuery<WatchList>({
    key: WATCHLIST_KEY,
    path: paths.watchlist.list,
    poll: POLL.slow,
  })

  const refresh = (): void => {
    void invalidate(WATCHLIST_KEY)
  }

  const add = useApiMutation<WatchRow, void>(
    () =>
      apiPost(paths.watchlist.create, {
        platform,
        kind,
        target_id: targetId.trim(),
        interval_seconds: interval,
      }),
    {
      onSuccess: () => {
        setTargetId('')
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
        cell: (row) => (
          <div className={styles.cell}>
            <span className="u-truncate" title={row.target_id}>
              {row.label || row.target_id}
            </span>
            <span className={styles.sub}>
              {t(`watchlist.kind.${row.kind}`)} · {row.platform}
            </span>
          </div>
        ),
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
        id: 'remove',
        header: '',
        width: '100px',
        hideable: false,
        cell: (row) => (
          <Button
            size="sm"
            variant="ghost"
            loading={remove.isPending && remove.variables === row.id}
            onClick={() => {
              remove.mutate(row.id)
            }}
          >
            {t('watchlist.remove')}
          </Button>
        ),
      },
    ],
    [change, formatters, remove, t],
  )

  const stats = query.data?.stats
  const globallyOff = query.data ? !query.data.enabled : false

  return (
    <>
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
        <form
          className="u-row"
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
                setKind(event.target.value as (typeof KINDS)[number])
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
              value={targetId}
              onChange={(event) => {
                setTargetId(event.target.value)
              }}
              placeholder={kind === 'author' ? 'MS4wLjABAAAA…' : '7408915107113127220'}
            />
          </Field>
          <Field id="watch-interval" label={t('watchlist.field.interval')}>
            <Select
              value={String(interval)}
              onChange={(event) => {
                setInterval(Number(event.target.value))
              }}
            >
              {INTERVALS.filter(
                (seconds) => seconds >= (query.data?.min_interval_seconds ?? 0),
              ).map((seconds) => (
                <option key={seconds} value={seconds}>
                  {formatters.duration(seconds * 1000)}
                </option>
              ))}
            </Select>
          </Field>
          <Button type="submit" variant="primary" loading={add.isPending} disabled={!targetId.trim()}>
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
    </>
  )
}
