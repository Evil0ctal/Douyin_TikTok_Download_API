import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Card,
  ConfirmDialog,
  Disclosure,
  EmptyState,
  ErrorState,
  Input,
  GlobeIcon,
  LockIcon,
  PageHeader,
  Skeleton,
  Switch,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useInvalidate } from '@/hooks'
import { apiPut } from '@/lib/api'
import { paths } from '@/lib/endpoints'

import styles from './EndpointAccess.module.css'

/**
 * Which endpoints this instance serves without an API key.
 *
 * Everything needs a credential by default and that is the right default: an
 * instance on a public server is a machine strangers can reach, and its whole
 * purpose is to spend someone else's identity pool. This page exists because
 * some deployments still want a subset open, and because the alternative was
 * asking an operator to hand-type "GET /api/v1/{platform}/video" into a
 * free-text list where a typo fails silently - the entry never matches and the
 * endpoint they meant to open stays closed until somebody notices.
 *
 * The rows come from the server, built from the OpenAPI document, so a switch
 * and the setting it writes cannot disagree about what a path is called.
 *
 * Admin, authentication and setup endpoints render locked, with no switch at
 * all. That is not styling: the server refuses those paths whatever the setting
 * says, so a switch here would be one that lies.
 */

const ACCESS_KEY = ['admin', 'endpoint-access'] as const
const SETTINGS_KEY = ['admin', 'settings'] as const

/** Wire shape of GET /api/v1/admin/endpoints/access. Paths are never translated. */
interface EndpointRow {
  key: string
  method: string
  path: string
  summary: string
  tags: string[]
  /** True when the server will never serve this without a credential. */
  protected: boolean
  public: boolean
  /**
   * The route has no credential check at all, so it is served openly whatever
   * the setting says and no switch can close it. Distinct from `public`, which
   * for every other row means "opened by the operator".
   */
  always_public?: boolean
}

interface AccessPayload {
  setting: string
  protected_prefixes: string[]
  open_count: number
  endpoints: EndpointRow[]
}

/** Method colouring, so a DELETE never reads as an ordinary row. */
const METHOD_TONE: Record<string, string> = {
  GET: 'ok',
  POST: 'warn',
  PUT: 'warn',
  PATCH: 'warn',
  DELETE: 'danger',
}

export default function EndpointAccess() {
  const { t } = useTranslation('console')
  const toast = useToast()
  const invalidate = useInvalidate()
  const [filter, setFilter] = useState('')
  const [pending, setPending] = useState<EndpointRow | null>(null)

  const query = useApiQuery<AccessPayload>({ key: ACCESS_KEY, path: paths.endpointsAccess })

  const save = useApiMutation(
    // `confirm` is not optional here. api.public_endpoints is flagged SENSITIVE
    // (doc 10: widening the attack surface must not be a stray click), so a PUT
    // without it is refused with INVALID_PARAM naming the field - which meant
    // every switch on this page failed, in both directions, and reported it as
    // "could not update endpoint access". The deliberate step the flag is
    // asking for is the confirm dialog this page already shows before opening
    // one; closing one narrows the surface and needs no ceremony.
    (next: string[]) =>
      apiPut(paths.settings.byKey('api.public_endpoints'), { value: next, confirm: true }),
    {
      onSuccess: async () => {
        await invalidate(ACCESS_KEY)
        await invalidate(SETTINGS_KEY)
        toast.success(t('access.saved'))
      },
      onError: () => toast.error(t('access.saveFailed')),
    },
  )

  const rows = useMemo(() => query.data?.endpoints ?? [], [query.data])

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    if (!needle) return rows
    return rows.filter(
      (row) =>
        row.path.toLowerCase().includes(needle) ||
        row.summary.toLowerCase().includes(needle) ||
        row.method.toLowerCase() === needle,
    )
  }, [rows, filter])

  /**
   * Two different numbers that were being reported as one.
   *
   * `opened` is what an operator chose here and can undo here. `alwaysOpen` is
   * the handful of routes with no credential check in the code - a login
   * endpoint cannot require you to be logged in - which no switch on this page
   * can close.
   *
   * Reporting the sum as "N endpoints answer without a credential" over a
   * warning about spending the identity pool was wrong twice: an instance that
   * had opened nothing still got the alarm, and the sentence about the pool is
   * false for every one of the always-open routes. None of them fetch anything.
   */
  const opened = useMemo(
    () => rows.filter((row) => row.public && !row.always_public),
    [rows],
  )
  const alwaysOpen = useMemo(() => rows.filter((row) => row.always_public), [rows])

  /**
   * Grouped by the tag the API document already gives every operation, so the
   * page reads as ten sections rather than as seventy-six rows in a row. Every
   * group starts shut - seventy-six paths is a page nobody scans, and the
   * heading plus its counts is the summary somebody actually came for.
   * Filtering opens everything, because a search with its results collapsed is
   * a search that failed.
   */
  const groups = useMemo(() => {
    const buckets = new Map<string, EndpointRow[]>()
    for (const row of visible) {
      // An operation with no tag is a real possibility and gets its own bucket
      // rather than vanishing.
      const tag = row.tags[0] ?? 'other'
      const bucket = buckets.get(tag)
      if (bucket) bucket.push(row)
      else buckets.set(tag, [row])
    }
    return [...buckets.entries()]
      .map(([tag, rows]) => ({
        tag,
        rows,
        // Only what an operator opened. The always-open routes are listed in
        // their own card above, where they can be explained rather than
        // counted into a warning that does not apply to them.
        open: rows.filter((row) => row.public && !row.always_public).length,
      }))
      .sort((a, b) => a.tag.localeCompare(b.tag))
  }, [visible])

  function apply(row: EndpointRow, next: boolean) {
    // Built from what the SETTING opened, not from what is currently served.
    // `public` now also covers routes that never had a credential check, and
    // rebuilding from it would write those into api.public_endpoints - adding
    // an entry the operator never chose for a route the entry cannot affect.
    const open = new Set(
      rows.filter((entry) => entry.public && !entry.always_public).map((entry) => entry.key),
    )
    if (next) open.add(row.key)
    else open.delete(row.key)
    save.mutate([...open].sort())
  }

  /**
   * The switch is "requires a key", so ON is the protected state.
   *
   * It used to be "is open", which put every protected endpoint's toggle in the
   * off position - a page of switches that are off, describing an instance
   * where nothing is exposed. That reads as protection being switched off. A
   * switch should track the safe property, so that all-on means all-safe.
   *
   * Turning one OFF is therefore what removes a credential check, and that is
   * the direction that gets confirmed. Turning it back on is not.
   */
  function onToggle(row: EndpointRow, requiresKey: boolean) {
    if (requiresKey) apply(row, false)
    else setPending(row)
  }

  if (query.isLoading) return <Skeleton />
  if (query.error) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />


  const searching = filter.trim().length > 0

  return (
    <div className="u-page">
      <PageHeader
        title={t('access.title')}
        description={t('access.description')}
        badge={
          <span className={styles.count} data-open={opened.length > 0}>
            {t('access.openCount', { count: opened.length })}
          </span>
        }
      />

      {/* The alarm, and only when there is something to be alarmed about.
          Every sentence in it is true of these routes and of no others: they
          were opened by hand, they can be closed by hand, and each call really
          does spend the identity pool. */}
      {opened.length > 0 ? (
        <Card title={t('access.openWarningTitle')}>
          <p className="u-secondary">{t('access.openWarning', { count: opened.length })}</p>
          <ul className={styles.uriList}>
            {opened.map((row) => (
              <li key={row.key}>
                <span className={styles.method} data-tone={METHOD_TONE[row.method] ?? 'muted'}>
                  {row.method}
                </span>
                <code>{row.path}</code>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {/* Not a warning: a list. These have no credential check in the code - a
          login endpoint cannot require you to be logged in - so no switch here
          closes them, and saying which they are is more use than counting them
          into an alarm that does not describe them. */}
      {alwaysOpen.length > 0 ? (
        <Card
          title={t('access.alwaysOpenTitle', { count: alwaysOpen.length })}
          description={t('access.alwaysOpenBody')}
        >
          <ul className={styles.uriList}>
            {alwaysOpen.map((row) => (
              <li key={row.key}>
                <span className={styles.method} data-tone={METHOD_TONE[row.method] ?? 'muted'}>
                  {row.method}
                </span>
                <code>{row.path}</code>
                {row.summary ? <span className={styles.summary}>{row.summary}</span> : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <Card description={t('access.lockedHint')}>
        <Input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder={t('access.filter')}
          aria-label={t('access.filter')}
        />
      </Card>

      {visible.length === 0 ? (
        <EmptyState title={t('access.noMatch')} />
      ) : (
        groups.map((group) => (
        <Card flush key={group.tag}>
          <Disclosure
            title={t(`access.group.${group.tag}`, { defaultValue: group.tag })}
            defaultOpen={searching}
            meta={
              <span className="u-row u-xs u-muted">
                {group.open > 0 ? (
                  <span className={styles.count} data-open="true">
                    {t('access.openCount', { count: group.open })}
                  </span>
                ) : null}
                <span>{t('access.groupCount', { count: group.rows.length })}</span>
              </span>
            }
            flush
          >
          <ul className={styles.list}>
            {group.rows.map((row) => (
              <li key={row.key} className={styles.row}>
                <span className={styles.method} data-tone={METHOD_TONE[row.method] ?? 'muted'}>
                  {row.method}
                </span>
                <span className={styles.body}>
                  <code>{row.path}</code>
                  {row.summary ? (
                    <span className={styles.summary}>{row.summary}</span>
                  ) : null}
                </span>
                {row.always_public ? (
                  <span className={styles.locked} title={t('access.alwaysOpenHint')}>
                    <GlobeIcon />
                    {t('access.alwaysOpen')}
                  </span>
                ) : row.protected ? (
                  <span className={styles.locked} title={t('access.lockedHint')}>
                    <LockIcon />
                    {t('access.locked')}
                  </span>
                ) : (
                  <span className={styles.control}>
                    {/* Named, not implied. A switch alone leaves its state as
                        the absence of a signal, and the absence of a signal is
                        how an operator reads "no check here". */}
                    <span className={styles.state} data-open={row.public}>
                      {row.public ? t('access.stateOpen') : t('access.stateClosed')}
                    </span>
                    <Switch
                      checked={!row.public}
                      disabled={save.isPending}
                      onChange={(event) => onToggle(row, event.target.checked)}
                      aria-label={t('access.toggleLabel', { endpoint: row.key })}
                    />
                  </span>
                )}
              </li>
            ))}
          </ul>
          </Disclosure>
        </Card>
        ))
      )}

      <ConfirmDialog
        open={pending !== null}
        danger
        title={t('access.confirmTitle')}
        description={t('access.confirmBody', { endpoint: pending?.key ?? '' })}
        confirmLabel={t('access.confirmAction')}
        loading={save.isPending}
        onConfirm={() => {
          if (pending) apply(pending, true)
          setPending(null)
        }}
        onCancel={() => setPending(null)}
      />
    </div>
  )
}
