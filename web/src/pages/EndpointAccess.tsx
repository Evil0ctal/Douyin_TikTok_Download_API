import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Card,
  ConfirmDialog,
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
    (next: string[]) => apiPut(paths.settings.byKey('api.public_endpoints'), { value: next }),
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

  const openCount = useMemo(() => rows.filter((row) => row.public).length, [rows])

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

  /** Opening one removes a credential check, so it is confirmed. Closing is not. */
  function onToggle(row: EndpointRow, next: boolean) {
    if (next) setPending(row)
    else apply(row, false)
  }

  if (query.isLoading) return <Skeleton />
  if (query.error) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />

  return (
    <div className="u-page">
      <PageHeader
        title={t('access.title')}
        description={t('access.description')}
        badge={
          <span className={styles.count} data-open={openCount > 0}>
            {t('access.openCount', { count: openCount })}
          </span>
        }
      />

      {openCount > 0 && (
        <Card title={t('access.openWarningTitle')}>
          <p>{t('access.openWarning', { count: openCount })}</p>
        </Card>
      )}

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
        <Card flush>
          <ul className={styles.list}>
            {visible.map((row) => (
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
                  <Switch
                    checked={row.public}
                    disabled={save.isPending}
                    onChange={(event) => onToggle(row, event.target.checked)}
                    aria-label={t('access.toggleLabel', { endpoint: row.key })}
                  />
                )}
              </li>
            ))}
          </ul>
        </Card>
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
