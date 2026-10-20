import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  Input,
  PageHeader,
  Select,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate, useSession } from '@/hooks'
import {
  apiDelete,
  apiPost,
  apiRequest,
  isApiError,
  waitForTask,
  type ApiError,
  type QueryParams,
} from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { atLeast } from '@/lib/roles'
import {
  IDENTITY_SOURCES,
  IDENTITY_STATES,
  PLATFORMS,
  type IdentitySource,
  type IdentityState,
  type Platform,
} from '@/lib/types'

import styles from './identities.module.css'
import { useIdentityColumns } from './identities/columns'
import { IdentityDrawer } from './identities/IdentityDrawer'
import { BulkRotationDialog } from './identities/BulkRotationDialog'
import { ImportDialog } from './identities/ImportDialog'
import { MintDialog } from './identities/MintDialog'
import { FilterSlot, ServesNotice, SuppressionNotice } from './identities/notices'
import { ProbeDialog } from './identities/ProbeDialog'
import { RefillCard } from './identities/RefillCard'
import {
  DEAD_SESSION,
  exportFilename,
  healthBand,
  lastLoginLosses,
  needsReset,
  runAll,
  saveJson,
  usePlatformInSentence,
  usePlatformName,
  type BulkOutcome,
} from './identities/shared'
import {
  IDENTITIES_KEY,
  POOL_KEY,
  PROXY_OPTIONS_KEY,
  type IdentityBundleFile,
  type IdentityPage,
  type IdentityProbeResult,
  type IdentityRow,
  type PoolLevel,
  type ProbeState,
  type ProxyOption,
} from './identities/types'
import {
  LOGIN_FILTERS,
  ROTATION_FILTERS,
  useIdentityFilters,
  type LoginFilter,
  type RotationFilter,
} from './identities/useIdentityFilters'

/** One page of the listing; "Load more" asks for this many again. */
const PAGE_LIMIT = 200
/** MAX_ADMIN_PAGE_SIZE on the server: past this the filters have to narrow. */
const MAX_LIMIT = 500
/** What one export, and one restore, accepts (`IdentityExport.identity_ids`). */
const EXPORT_CHUNK = 200

/**
 * One page of the listing, with what `meta` says about the rest.
 *
 * Its own request rather than `useApiQuery`, which hands back `data` alone:
 * the total and the retired count are in `meta`, and they are the two numbers
 * that let the page admit what it is not showing.
 */
async function fetchPage(params: QueryParams, signal: AbortSignal): Promise<IdentityPage> {
  const response = await apiRequest<IdentityRow[]>(paths.identities.list, { params, signal })
  const rows = response.data
  const total = typeof response.meta.total === 'number' ? response.meta.total : rows.length
  const retired = typeof response.meta.retired === 'number' ? response.meta.retired : 0
  return { rows, total, retired }
}

const LOGIN_LABELS: Record<LoginFilter, string> = {
  any: 'console:identity.filter.anyLogin',
  yes: 'console:identity.filter.loggedInOnly',
  no: 'console:identity.filter.guestOnly',
}

const ROTATION_LABELS: Record<RotationFilter, string> = {
  any: 'console:identity.filter.anyRotation',
  rotating: 'console:identity.filter.rotatingOnly',
  reserved: 'console:identity.filter.reservedOnly',
  limited: 'console:identity.filter.limitedOnly',
}

/** The fields the search box looks in: wire values and the names people type. */
function searchable(row: IdentityRow, platformName: (platform: string) => string): string[] {
  return [
    row.id,
    row.platform,
    platformName(row.platform),
    row.state,
    row.source,
    row.proxy_id ?? '',
    row.proxy_label ?? '',
    row.retire_reason ?? '',
    row.note ?? '',
    row.account_id ?? '',
  ]
}

export default function Identities() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const invalidate = useInvalidate()
  const platformName = usePlatformName()
  const platformPhrase = usePlatformInSentence()
  const [filters, setFilters] = useIdentityFilters()

  // Every write on this page is `manage_pool` or a settings write, both of
  // which take the operator role; the listing, the pool level and the masked
  // jar are `read_admin`. A viewer was shown Mint, Import, Test, Retire,
  // Export, Reveal and Check the login, and every one of them answered 403.
  // Hidden, not disabled: a viewer has no route to them at all, and the server
  // stays the gate either way.
  const session = useSession()
  const canManage = atLeast(session.data?.role ?? null, 'operator')

  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [probes, setProbes] = useState<Record<string, ProbeState>>({})
  const [probeDetail, setProbeDetail] = useState<{ identity: IdentityRow; state: ProbeState } | null>(
    null,
  )
  const [mintOpen, setMintOpen] = useState(false)
  // A fresh dialog per opening, so a preset applies and nothing of the last
  // import - a pasted jar, a loaded file - is sitting in it.
  const [importing, setImporting] = useState<{ open: boolean; preset: Platform | null; key: number }>(
    { open: false, preset: null, key: 0 },
  )
  const [retiring, setRetiring] = useState<IdentityRow[] | null>(null)
  const [scoping, setScoping] = useState<{ rows: IdentityRow[]; key: number } | null>(null)
  const [resetting, setResetting] = useState<IdentityRow[] | null>(null)
  const [autoCheck, setAutoCheck] = useState<string | null>(null)

  const serverParams = useMemo<QueryParams>(
    () => ({
      platform: filters.platform === 'all' ? undefined : filters.platform,
      state: filters.state === 'all' ? undefined : filters.state,
      source: filters.source === 'all' ? undefined : filters.source,
      authenticated: filters.login === 'any' ? undefined : filters.login === 'yes',
      rotation: filters.rotation === 'any' ? undefined : filters.rotation,
      serves: filters.serves ?? undefined,
      ready: filters.ready || undefined,
      include_retired: filters.all || undefined,
    }),
    [
      filters.platform,
      filters.state,
      filters.source,
      filters.login,
      filters.rotation,
      filters.serves,
      filters.ready,
      filters.all,
    ],
  )
  // "Load more" counts pages for one set of filters; changing a filter starts
  // over rather than fetching four pages of something else.
  const filterKey = JSON.stringify(serverParams)
  const [paging, setPaging] = useState({ key: filterKey, pages: 1 })
  const pages = paging.key === filterKey ? paging.pages : 1
  const limit = Math.min(PAGE_LIMIT * pages, MAX_LIMIT)

  const list = useQuery<IdentityPage, ApiError>({
    queryKey: [...IDENTITIES_KEY, 'list', serverParams, limit],
    queryFn: ({ signal }) => fetchPage({ ...serverParams, limit }, signal),
    refetchInterval: POLL.fast,
    // A filter change keeps the old rows on screen until the new ones land,
    // rather than flashing an empty table every time a select moves.
    placeholderData: keepPreviousData,
    meta: { path: paths.identities.list },
  })

  // The refill card polls this; the page reads the same entry for the
  // last-login warnings rather than asking again.
  const pool = useApiQuery<PoolLevel>({ key: POOL_KEY, path: paths.identities.pool })

  const proxies = useApiQuery<ProxyOption[]>({
    key: PROXY_OPTIONS_KEY,
    path: paths.proxies.list,
    poll: POLL.slow,
    enabled: canManage,
  })

  const refresh = useCallback((): Promise<void> => invalidate(IDENTITIES_KEY), [invalidate])

  const loaded = useMemo(() => list.data?.rows ?? [], [list.data])
  const total = list.data?.total ?? 0
  const retiredHidden = list.data?.retired ?? 0

  /**
   * The drawer's identity, read by id from what the listing returns on every
   * poll - or fetched on its own when a link names one the page has not
   * loaded: older than the first page, or filtered out of it.
   */
  const inspectingId = filters.id
  const fromList = inspectingId ? (loaded.find((row) => row.id === inspectingId) ?? null) : null
  const single = useQuery<IdentityPage, ApiError>({
    queryKey: [...IDENTITIES_KEY, 'one', inspectingId],
    queryFn: ({ signal }) => fetchPage({ identity_id: inspectingId, limit: 1 }, signal),
    enabled: inspectingId !== null && fromList === null && !list.isPending,
    refetchInterval: POLL.fast,
    meta: { path: paths.identities.list },
  })
  const inspecting = fromList ?? single.data?.rows[0] ?? null

  /**
   * Search runs over the rows already loaded rather than as a query
   * parameter: it matches the names people type - a proxy label, a platform's
   * name in this language - that the database does not hold, and every count
   * it quotes is then exact for what is on screen. "Showing N of M" says when
   * that is not everything.
   */
  const matched = useMemo(() => {
    const needle = filters.q.trim().toLowerCase()
    if (!needle) return loaded
    return loaded.filter((row) =>
      searchable(row, platformName).some((field) => field.toLowerCase().includes(needle)),
    )
  }, [loaded, filters.q, platformName])

  /**
   * What the default view keeps back. Retired identities are left out by the
   * server and counted there; an identity whose session verdict is dead holds
   * a credential the platform refuses, and is held back here. Cooling and
   * degraded stay: cooling is a timer that runs out on its own, and hiding a
   * recovering pool is how a console reports "no identities" to an operator
   * who has forty.
   */
  const view = useMemo(() => {
    // An explicit "state: retired" asks for exactly the rows the default view
    // drops, and retiring wipes the jar. Suppressing either would answer the
    // operator's question with an empty table.
    if (filters.all || filters.state === 'retired') {
      return { visible: matched, deadSession: 0 }
    }
    const visible = matched.filter((row) => !DEAD_SESSION.has(row.session?.verdict ?? 'unknown'))
    return { visible, deadSession: matched.length - visible.length }
  }, [matched, filters.all, filters.state])
  const rows = view.visible
  const hidden = { retired: filters.all ? 0 : retiredHidden, deadSession: view.deadSession }

  /**
   * The selection as far as it is on screen.
   *
   * It used to survive a filter change whole, so Export sent rows the operator
   * could no longer see and counted them, while Retire and Reset quietly acted
   * on the visible part only - "Retire 0 identities" was a button you could
   * press. Every bulk action and every count now uses this.
   */
  const selectedRows = useMemo(() => rows.filter((row) => selected.has(row.id)), [rows, selected])
  const selectedIds = useMemo(() => new Set(selectedRows.map((row) => row.id)), [selectedRows])
  const retirable = selectedRows.filter((row) => row.state !== 'retired')
  const resettable = selectedRows.filter(needsReset)

  const dropFromSelection = (ids: string[]) => {
    setSelected((current) => {
      const next = new Set(current)
      for (const id of ids) next.delete(id)
      return next
    })
  }

  /** "n done, m failed", with the first refusal's reason. */
  function reportBulk<T>(outcome: BulkOutcome<T>, doneText: string, failTitle: string): void {
    const failed = outcome.failed.length
    if (failed === 0) {
      toast.success(doneText)
      return
    }
    const first = outcome.failed[0]?.error
    if (outcome.done.length === 0) {
      toast.apiError(first, failTitle)
      return
    }
    toast.warning(t('console:identity.bulk.partial', { done: outcome.done.length, failed }), {
      description: isApiError(first) ? first.message : undefined,
    })
  }

  const runProbe = async (identity: IdentityRow, showDialog: boolean): Promise<void> => {
    setProbes((current) => ({ ...current, [identity.id]: { running: true } }))
    try {
      const queued = await apiPost<{ task_id: string }>(paths.identities.test(identity.id))
      const result = await waitForTask<IdentityProbeResult>(queued.task_id, { timeoutMs: 120_000 })
      const next: ProbeState = { running: false, result }
      setProbes((current) => ({ ...current, [identity.id]: next }))
      if (showDialog) setProbeDetail({ identity, state: next })
      await refresh()
    } catch (error) {
      const next: ProbeState = { running: false, error: error as ApiError }
      setProbes((current) => ({ ...current, [identity.id]: next }))
      if (showDialog) setProbeDetail({ identity, state: next })
    }
  }

  /**
   * Put identities back into rotation by hand.
   *
   * Offered because recovery is deliberately slow - a degraded identity waits
   * out a ceiling cooldown, and a failure streak keeps it out of the pool level
   * until that many successes have gone by - and slow is right only when the
   * failures were the identity's fault. Nothing automatic can know when they
   * were not; this instance's own classifier charged identities for looking up
   * posts that did not exist until 2026-09-09, and fixing that repaired none of
   * the damage it had already done.
   */
  const reset = useApiMutation<BulkOutcome<IdentityRow>, IdentityRow[]>(
    (targets) => runAll(targets, (identity) => apiPost(paths.identities.reset(identity.id), {})),
    {
      onSuccess: async (outcome) => {
        reportBulk(
          outcome,
          t('console:identity.reset.done', { count: outcome.done.length }),
          t('console:identity.reset.failed'),
        )
        setResetting(null)
        dropFromSelection(outcome.done.map((row) => row.id))
        await refresh()
      },
    },
  )

  /** One rotation choice for every selected identity; see BulkRotationDialog. */
  const scope = useApiMutation<
    BulkOutcome<IdentityRow>,
    { rows: IdentityRow[]; endpoints: string[] | null }
  >(
    ({ rows: targets, endpoints }) =>
      runAll(targets, (identity) =>
        apiPost(paths.identities.rotation(identity.id), { endpoints }),
      ),
    {
      onSuccess: async (outcome) => {
        reportBulk(
          outcome,
          t('console:identity.rotation.bulkDone', { count: outcome.done.length }),
          t('console:identity.rotation.failed'),
        )
        setScoping(null)
        await refresh()
      },
    },
  )

  const retire = useApiMutation<BulkOutcome<IdentityRow>, IdentityRow[]>(
    (targets) =>
      runAll(targets, (identity) =>
        apiDelete(paths.identities.byId(identity.id), {
          body: { reason: 'retired from the console' },
        }),
      ),
    {
      onSuccess: async (outcome) => {
        reportBulk(
          outcome,
          t('console:identity.retired', { count: outcome.done.length }),
          t('console:identity.retireFailed'),
        )
        setRetiring(null)
        dropFromSelection(outcome.done.map((row) => row.id))
        await refresh()
      },
    },
  )

  /**
   * Write identities to a file.
   *
   * The pool could be filled and never emptied: an operator who had built one
   * that works could not move it to a second instance, keep a copy before a
   * risky change, or hand one identity to somebody debugging. The only way out
   * was the database.
   *
   * What lands in the downloads folder is a credential file. The name says the
   * date so two exports do not overwrite each other, and the document carries
   * a warning field for whoever finds it later.
   */
  const exporting = useApiMutation<number, string[]>(
    async (ids) => {
      // In files of EXPORT_CHUNK. The route takes at most that many, and so
      // does a restore: one merged document past it could be written and never
      // read back. "Load more" lets the page hold more than one request's worth,
      // and select-all then asked for all of it and got a 422.
      const chunks: string[][] = []
      for (let start = 0; start < ids.length; start += EXPORT_CHUNK) {
        chunks.push(ids.slice(start, start + EXPORT_CHUNK))
      }
      const filename = exportFilename()
      let written = 0
      for (const [index, chunk] of chunks.entries()) {
        const bundle = await apiPost<IdentityBundleFile>(paths.identities.export, {
          identity_ids: chunk,
        })
        saveJson(
          bundle,
          chunks.length > 1 ? filename.replace(/\.json$/, `-part-${index + 1}.json`) : filename,
        )
        written += bundle.identities.length
      }
      return written
    },
    {
      onSuccess: (written) => {
        toast.success(t('console:identity.export.done', { count: written }))
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.export.failed'))
      },
    },
  )

  const openIdentity = useCallback(
    (id: string | null) => {
      setFilters({ id })
    },
    [setFilters],
  )
  const clearAutoCheck = useCallback(() => {
    setAutoCheck(null)
  }, [])

  const columns = useIdentityColumns({
    canManage,
    probes,
    onTest: (row) => {
      void runProbe(row, true)
    },
    onReset: (row) => {
      setResetting([row])
    },
    onRetire: (row) => {
      setRetiring([row])
    },
  })

  const filtersActive =
    filters.platform !== 'all' ||
    filters.state !== 'all' ||
    filters.source !== 'all' ||
    filters.login !== 'any' ||
    filters.rotation !== 'any' ||
    filters.q.trim() !== ''
  // The pool is not empty, this view is. Offering "mint your first identity"
  // here would answer a question nobody asked and hide the one that matters:
  // which filter to widen.
  const filteredToNothing =
    rows.length === 0 && (loaded.length > 0 || hidden.retired > 0 || filtersActive)
  const retireLosses = lastLoginLosses(retiring ?? [], pool.data)

  return (
    <div className="u-page">
      <PageHeader
        title={t('console:page.identities.title')}
        description={t('console:page.identities.description')}
        actions={
          canManage ? (
            <>
              <Button
                variant="secondary"
                onClick={() => {
                  setImporting((current) => ({ open: true, preset: null, key: current.key + 1 }))
                }}
              >
                {t('console:identity.import.action')}
              </Button>
              <Button
                variant="primary"
                onClick={() => {
                  setMintOpen(true)
                }}
              >
                {t('console:identity.mint.action')}
              </Button>
            </>
          ) : null
        }
      />

      <RefillCard
        canManage={canManage}
        onImport={(platform) => {
          setImporting((current) => ({ open: true, preset: platform, key: current.key + 1 }))
        }}
      />

      {filters.serves ? (
        <ServesNotice
          endpoint={filters.serves}
          ready={filters.ready}
          onClear={() => {
            setFilters({ serves: null, ready: false })
          }}
        />
      ) : null}

      {hidden.retired + hidden.deadSession > 0 || filters.all ? (
        <SuppressionNotice
          retired={hidden.retired}
          deadSession={hidden.deadSession}
          showAll={filters.all}
          onToggle={() => {
            setFilters({ all: !filters.all })
          }}
        />
      ) : null}

      {/* The one sentence that stops the two columns reading as a
          contradiction. Muted and inline rather than a banner: it is a legend,
          not a warning, and the page already carries one notice. */}
      <p className="u-xs u-muted" style={{ margin: 0 }}>
        {t('console:identity.columnsDiffer')}
      </p>

      <Card flush>
        <DataTable
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          loading={list.isLoading}
          error={list.error}
          onRetry={() => {
            void list.refetch()
          }}
          storageKey="identities"
          defaultSort={{ columnId: 'state', direction: 'asc' }}
          selectedIds={canManage ? selectedIds : undefined}
          onSelectionChange={canManage ? setSelected : undefined}
          // What an operator watches a 5s poll for: the state, whether the
          // session is still live, the failure streak, and which health band
          // the score landed in. The raw score is deliberately not in here - it
          // drifts with every bucket, and a table that flashes every row on
          // every poll teaches an operator to stop reading the flash.
          flashValue={(row) =>
            [
              row.state,
              row.session?.verdict ?? 'unknown',
              row.consecutive_fails,
              healthBand(row),
            ].join('|')
          }
          caption={t('console:page.identities.title')}
          onRowClick={(row) => {
            openIdentity(row.id)
          }}
          emptyTitle={
            filteredToNothing
              ? t('console:identity.empty.filteredTitle')
              : t('console:identity.empty.title')
          }
          emptyDescription={
            filteredToNothing
              ? t('console:identity.empty.filteredDescription')
              : t('console:identity.empty.description')
          }
          emptyAction={
            filteredToNothing || !canManage ? null : (
              <Button
                variant="primary"
                onClick={() => {
                  setMintOpen(true)
                }}
              >
                {t('console:identity.mint.action')}
              </Button>
            )
          }
          toolbar={
            <>
              <FilterSlot width="200px">
                <Input
                  value={filters.q}
                  placeholder={t('console:identity.filter.searchPlaceholder')}
                  aria-label={t('common:action.search')}
                  onChange={(event) => {
                    setFilters({ q: event.target.value })
                  }}
                />
              </FilterSlot>
              <FilterSlot>
                <Select
                  value={filters.platform}
                  aria-label={t('console:field.platform')}
                  onChange={(event) => {
                    setFilters({ platform: event.target.value as Platform | 'all' })
                  }}
                  options={[
                    { value: 'all', label: t('console:identity.filter.allPlatforms') },
                    ...PLATFORMS.map((value) => ({ value, label: platformName(value) })),
                  ]}
                />
              </FilterSlot>
              <FilterSlot>
                <Select
                  value={filters.state}
                  aria-label={t('console:field.state')}
                  onChange={(event) => {
                    setFilters({ state: event.target.value as IdentityState | 'all' })
                  }}
                  options={[
                    { value: 'all', label: t('console:identity.filter.allStates') },
                    ...IDENTITY_STATES.map((value) => ({
                      value,
                      label: t(`common:state.identity.${value}`),
                    })),
                  ]}
                />
              </FilterSlot>
              <FilterSlot width="150px">
                <Select
                  value={filters.source}
                  aria-label={t('console:field.source')}
                  onChange={(event) => {
                    setFilters({ source: event.target.value as IdentitySource | 'all' })
                  }}
                  options={[
                    { value: 'all', label: t('console:identity.filter.allSources') },
                    ...IDENTITY_SOURCES.map((value) => ({
                      value,
                      label: t(`console:identity.source.${value}`),
                    })),
                  ]}
                />
              </FilterSlot>
              <FilterSlot width="180px">
                <Select
                  value={filters.login}
                  aria-label={t('console:field.authenticated')}
                  onChange={(event) => {
                    setFilters({ login: event.target.value as LoginFilter })
                  }}
                  options={LOGIN_FILTERS.map((value) => ({
                    value,
                    label: t(LOGIN_LABELS[value]),
                  }))}
                />
              </FilterSlot>
              <FilterSlot width="170px">
                <Select
                  value={filters.rotation}
                  aria-label={t('console:identity.rotation.title')}
                  onChange={(event) => {
                    setFilters({ rotation: event.target.value as RotationFilter })
                  }}
                  options={ROTATION_FILTERS.map((value) => ({
                    value,
                    label: t(ROTATION_LABELS[value]),
                  }))}
                />
              </FilterSlot>
              {canManage && resettable.length > 0 ? (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setResetting(resettable)
                  }}
                >
                  {t('console:identity.reset.bulk', { count: resettable.length })}
                </Button>
              ) : null}
              {canManage && retirable.length > 0 ? (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setScoping((current) => ({ rows: retirable, key: (current?.key ?? 0) + 1 }))
                  }}
                >
                  {t('console:identity.rotation.bulkAction', { count: retirable.length })}
                </Button>
              ) : null}
              {canManage && selectedRows.length > 0 ? (
                <Button
                  size="sm"
                  variant="secondary"
                  loading={exporting.isPending}
                  onClick={() => {
                    exporting.mutate(selectedRows.map((row) => row.id))
                  }}
                >
                  {t('console:identity.export.action', { count: selectedRows.length })}
                </Button>
              ) : null}
              {canManage && retirable.length > 0 ? (
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => {
                    setRetiring(retirable)
                  }}
                >
                  {t('console:identity.bulkRetire', { count: retirable.length })}
                </Button>
              ) : null}
            </>
          }
        />
      </Card>

      {list.data && total > 0 ? (
        <div className={styles.paging}>
          <span className="u-xs u-muted">
            {t('console:identity.paging.showing', {
              shown: format.number(loaded.length),
              total: format.number(total),
            })}
            {filters.q.trim() ? ` · ${t('console:identity.paging.searchScope')}` : ''}
          </span>
          {loaded.length < total ? (
            limit < MAX_LIMIT ? (
              <Button
                size="sm"
                variant="secondary"
                loading={list.isFetching && list.isPlaceholderData}
                onClick={() => {
                  setPaging({ key: filterKey, pages: pages + 1 })
                }}
              >
                {t('console:identity.paging.loadMore')}
              </Button>
            ) : (
              <span className="u-xs u-muted">
                {t('console:identity.paging.ceiling', { shown: format.number(loaded.length) })}
              </span>
            )
          ) : null}
        </div>
      ) : null}

      <MintDialog
        open={mintOpen}
        proxies={proxies.data ?? []}
        onClose={() => {
          setMintOpen(false)
        }}
        onDone={refresh}
      />

      <ImportDialog
        key={importing.key}
        open={importing.open}
        preset={importing.preset}
        proxies={proxies.data ?? []}
        onClose={() => {
          setImporting((current) => ({ ...current, open: false }))
        }}
        onDone={refresh}
        onImported={(identityId, authenticated) => {
          // Straight to the identity just stored, and straight to the one
          // question a login import leaves open: whether the platform takes it.
          if (authenticated) setAutoCheck(identityId)
          openIdentity(identityId)
        }}
      />

      <BulkRotationDialog
        key={scoping?.key ?? 0}
        targets={scoping?.rows ?? null}
        pool={pool.data}
        busy={scope.isPending}
        onCancel={() => {
          setScoping(null)
        }}
        onApply={(endpoints) => {
          if (scoping) scope.mutate({ rows: scoping.rows, endpoints })
        }}
      />

      <ConfirmDialog
        open={resetting !== null}
        loading={reset.isPending}
        title={t('console:identity.reset.dialogTitle', { count: resetting?.length ?? 0 })}
        description={t('console:identity.reset.dialogBody')}
        confirmLabel={t('console:identity.reset.action')}
        onCancel={() => {
          setResetting(null)
        }}
        onConfirm={() => {
          reset.mutate(resetting ?? [])
        }}
      />

      <ConfirmDialog
        open={retiring !== null}
        danger
        loading={retire.isPending}
        title={t('console:identity.retireDialog.title', { count: retiring?.length ?? 0 })}
        description={t('console:identity.retireDialog.description')}
        confirmLabel={t('console:identity.retire')}
        onCancel={() => {
          setRetiring(null)
        }}
        onConfirm={() => {
          retire.mutate(retiring ?? [])
        }}
      >
        {retireLosses.map((loss) => (
          <p key={loss.platform} className="u-xs" style={{ margin: 0, color: 'var(--caution)' }}>
            {t('console:identity.logins.lastRetire', {
              platform: platformPhrase(loss.platform),
              endpoints: loss.endpoints.join(', '),
            })}
          </p>
        ))}
      </ConfirmDialog>

      <IdentityDrawer
        identityId={inspectingId}
        identity={inspecting}
        loading={list.isPending || single.isLoading}
        probe={inspectingId ? probes[inspectingId] : undefined}
        canManage={canManage}
        pool={pool.data}
        autoCheck={inspectingId !== null && autoCheck === inspectingId}
        onAutoChecked={clearAutoCheck}
        onTest={(identity) => {
          void runProbe(identity, false)
        }}
        onReset={(identity) => {
          setResetting([identity])
        }}
        onRetire={(identity) => {
          setRetiring([identity])
        }}
        onExport={(identity) => {
          exporting.mutate([identity.id])
        }}
        exporting={exporting.isPending}
        onChanged={refresh}
        onClose={() => {
          // The drawer and a confirm opened from it both listen for Escape, so
          // cancelling "Retire 1 identity?" also closed the drawer behind it.
          // The confirm is still open when both handlers run; that is the tell.
          if (retiring !== null || resetting !== null) return
          openIdentity(null)
        }}
      />

      <ProbeDialog
        open={probeDetail !== null}
        identity={probeDetail?.identity ?? null}
        state={probeDetail?.state ?? null}
        onClose={() => {
          setProbeDetail(null)
        }}
      />
    </div>
  )
}
