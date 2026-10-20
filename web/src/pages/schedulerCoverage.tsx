import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'

import { Card, DataTable, ErrorState, Skeleton, Switch, useToast, type Column } from '@/components'
import { useApiQuery, useInvalidate, usePlatformName } from '@/hooks'
import { apiPut } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import type { Platform } from '@/lib/types'

import { LoginTag, loginHint, useEndpointLabel } from './identities/rotation'
import type { CapabilityRow } from './playgroundCatalog'

/** One endpoint of `GET /admin/identities/coverage`. */
interface CoverageEndpoint extends CapabilityRow {
  /** Active or degraded identities rotation may send here. */
  ready: number
  /** Of those, the logins. */
  signed_in: number
  /** Resting, and back in rotation for it once they recover. */
  cooling: number
  /** Ready ones limited to a list that names this endpoint. */
  dedicated: number
}

interface CoveragePlatform {
  platform: Platform
  guests_first: boolean
  /** Identities that serve only the requests naming them. */
  reserved: number
  endpoints: CoverageEndpoint[]
}

export const COVERAGE_KEY = ['admin', 'identities', 'coverage'] as const

/**
 * Who serves each endpoint, from the endpoint's side.
 *
 * The pool is counted per platform and handed out per endpoint, and the
 * difference is where the surprises were: a login limited to the author feed
 * is in the pool and absent everywhere else, a reserved one is absent from
 * every endpoint, and an endpoint that needs a login with none in rotation
 * fails every later page while the pool reads full. Each platform's switch is
 * `pool.<platform>.guests_first`, which decides whether its logins are spent
 * on endpoints a guest answers just as well.
 */
export function CoverageCard({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation(['console', 'common'])
  const platformName = usePlatformName()
  const label = useEndpointLabel()
  const toast = useToast()
  const invalidate = useInvalidate()
  const [saving, setSaving] = useState<Platform | null>(null)

  const coverage = useApiQuery<{ platforms: CoveragePlatform[] }>({
    key: COVERAGE_KEY,
    path: paths.identities.coverage,
    poll: POLL.normal,
  })

  const toggle = async (platform: Platform, value: boolean): Promise<void> => {
    setSaving(platform)
    try {
      await apiPut(paths.settings.byKey(`pool.${platform}.guests_first`), {
        value,
        confirm: false,
      })
      toast.success(t('console:scheduler.coverage.saved'))
      await invalidate(COVERAGE_KEY)
      await invalidate(['admin', 'settings'])
    } catch (error) {
      toast.apiError(error, t('console:scheduler.coverage.saveFailed'))
    } finally {
      setSaving(null)
    }
  }

  const columns = (platform: Platform): Array<Column<CoverageEndpoint>> => [
    {
      id: 'endpoint',
      header: t('console:field.endpoint'),
      hideable: false,
      cell: (row) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span>{label(row)}</span>
          <span className="u-xs u-mono u-muted">{row.endpoint}</span>
        </div>
      ),
    },
    {
      id: 'login',
      header: t('console:scheduler.coverage.login'),
      cell: (row) => {
        const hint = loginHint(row)
        return hint ? <LoginTag hint={hint} /> : <span className="u-muted">—</span>
      },
    },
    {
      id: 'ready',
      header: t('console:scheduler.coverage.ready'),
      align: 'right',
      mono: true,
      sortValue: (row) => row.ready,
      cell: (row) => (
        // With `ready`, so the list holds what this number counts. Without
        // it the cooling identities came along, and the list it opened was
        // longer than the number it was opened from.
        <Link
          href={`/identities?platform=${platform}&serves=${encodeURIComponent(row.endpoint)}&ready=1`}
          title={t('console:scheduler.coverage.open')}
        >
          {row.ready}
        </Link>
      ),
    },
    {
      id: 'signedIn',
      header: t('console:scheduler.coverage.signedIn'),
      align: 'right',
      mono: true,
      sortValue: (row) => row.signed_in,
      cell: (row) =>
        row.signed_in === 0 && loginHint(row) !== null && loginHint(row) !== 'preferred' ? (
          <span style={{ color: 'var(--caution)' }} title={t('console:scheduler.coverage.noLogin')}>
            0
          </span>
        ) : (
          row.signed_in
        ),
    },
    {
      id: 'dedicated',
      header: t('console:scheduler.coverage.dedicated'),
      align: 'right',
      mono: true,
      sortValue: (row) => row.dedicated,
      cell: (row) => row.dedicated,
    },
    {
      id: 'cooling',
      header: t('console:scheduler.coverage.cooling'),
      align: 'right',
      mono: true,
      sortValue: (row) => row.cooling,
      cell: (row) =>
        row.cooling > 0 ? (
          <Link
            href={`/identities?platform=${platform}&serves=${encodeURIComponent(row.endpoint)}&state=cooling`}
            title={t('console:scheduler.coverage.openCooling')}
          >
            {row.cooling}
          </Link>
        ) : (
          row.cooling
        ),
    },
  ]

  return (
    <Card
      title={t('console:scheduler.coverage.title')}
      description={t('console:scheduler.coverage.description')}
    >
      {coverage.isLoading ? (
        <div className="u-stack-sm" aria-busy="true">
          <Skeleton height={18} width="30%" />
          <Skeleton height={44} />
        </div>
      ) : coverage.isError ? (
        <ErrorState
          error={coverage.error}
          compact
          onRetry={() => {
            void coverage.refetch()
          }}
        />
      ) : (
        <div className="u-stack">
          {(coverage.data?.platforms ?? []).map((entry) => (
            <section key={entry.platform} className="u-stack-sm">
              <div className="u-row-between u-wrap">
                <strong>{platformName(entry.platform)}</strong>
                {entry.reserved > 0 ? (
                  <span className="u-xs u-muted">
                    {t('console:scheduler.coverage.reserved', { count: entry.reserved })}
                  </span>
                ) : null}
              </div>
              <Switch
                checked={entry.guests_first}
                disabled={!canWrite || saving !== null}
                label={t('console:scheduler.coverage.guestsFirst')}
                hint={t(
                  entry.guests_first
                    ? 'console:scheduler.coverage.guestsFirstOn'
                    : 'console:scheduler.coverage.guestsFirstOff',
                )}
                onChange={(event) => {
                  void toggle(entry.platform, event.target.checked)
                }}
              />
              <DataTable
                columns={columns(entry.platform)}
                rows={entry.endpoints.filter((row) => !row.session_bound)}
                getRowId={(row) => row.endpoint}
                storageKey={`scheduler-coverage-${entry.platform}`}
                caption={t('console:scheduler.coverage.caption', {
                  platform: platformName(entry.platform),
                })}
              />
            </section>
          ))}
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('console:scheduler.coverage.footnote')}
          </p>
        </div>
      )}
    </Card>
  )
}
