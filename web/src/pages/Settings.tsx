import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  DemoCard,
  EmptyState,
  ErrorState,
  PageHeader,
  Banner,
  LockIcon,
  RefreshIcon,
  ServerIcon,
  SettingEditor,
  Skeleton,
  sourceOf,
  type SettingRow,
} from '@/components'
import { useApiQuery, useInvalidate, useSession } from '@/hooks'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'

/**
 * Runtime configuration.
 *
 * The one thing this page must never leave ambiguous is where a value comes
 * from. Once a key is stored in the database it wins over the .env seed, and
 * "I edited .env, restarted, and nothing changed" is the question that follows
 * a settings page that hides that (docs/design/10-configuration.md). Every row
 * therefore names its source and can be reset back to the inherited value.
 *
 * SENSITIVE keys are not a styling variation: the URL allowlist is the only
 * SSRF defence this service has, so widening it takes an administrator, a typed
 * confirmation and an audit row.
 */

const SETTINGS_KEY = ['admin', 'settings'] as const

interface SettingsResponse {
  version: number
  settings: SettingRow[]
}

/**
 * The groups this page shows, in order. `sched` and `pool` are deliberately
 * absent: they moved to their own page under the identity pool, where the
 * numbers sit beside an explanation of what they do to rotation and beside the
 * pool they act on. A key whose prefix is not listed here lands in `other`, so
 * a new setting is never invisible.
 */
const GROUP_ORDER = [
  'signing',
  'cache',
  'snapshot',
  'archive',
  'media',
  'watchlist',
  'capacity',
  'retention',
  'demo',
  'api',
  'security',
  'notify',
  'system',
] as const

const KNOWN_GROUPS: ReadonlySet<string> = new Set(GROUP_ORDER)

/**
 * Owned by the scheduler page. Dropping them from GROUP_ORDER alone was not
 * enough - `other` catches every unlisted prefix, which is what makes a new
 * setting impossible to lose, so they simply reappeared under it.
 */
const ELSEWHERE: ReadonlySet<string> = new Set(['sched', 'pool'])

function groupOf(key: string): string {
  const prefix = key.split('.')[0] ?? ''
  return KNOWN_GROUPS.has(prefix) ? prefix : 'other'
}

function belongsHere(key: string): boolean {
  return !ELSEWHERE.has(key.split('.')[0] ?? '')
}


export default function Settings() {
  const { t } = useTranslation(['console', 'common'])
  const invalidate = useInvalidate()
  const session = useSession()

  const query = useApiQuery<SettingsResponse>({
    key: SETTINGS_KEY,
    path: paths.settings.list,
    poll: POLL.slow,
  })

  const role = session.data?.role ?? null
  // With no session payload the server is still the authority; the console shows
  // the controls and surfaces its refusal, rather than locking a page nobody can
  // then explain. A known role is respected exactly.
  const canWrite = role === null || role !== 'viewer'
  const canWriteSensitive = role === null || role === 'admin'

  const groups = useMemo(() => {
    const rows = (query.data?.settings ?? []).filter((row) => belongsHere(row.key))
    const buckets = new Map<string, SettingRow[]>()
    for (const row of rows) {
      const group = groupOf(row.key)
      const bucket = buckets.get(group)
      if (bucket) bucket.push(row)
      else buckets.set(group, [row])
    }
    const order = [...GROUP_ORDER, 'other']
    return order
      .filter((group) => buckets.has(group))
      .map((group) => ({ group, rows: buckets.get(group) ?? [] }))
  }, [query.data])

  const overrides = (query.data?.settings ?? [])
    .filter((row) => belongsHere(row.key) && sourceOf(row) === 'database').length

  const refresh = (): void => {
    void invalidate(SETTINGS_KEY)
  }

  return (
    <div className="u-page">
      <PageHeader
        title={t('page.settings.title')}
        description={t('page.settings.description')}
        badge={
          query.data ? (
            <span className="u-xs u-muted u-mono">
              {t('settings.version', { version: query.data.version })}
            </span>
          ) : null
        }
        actions={
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshIcon />}
            onClick={refresh}
            loading={query.isFetching && !query.isLoading}
          >
            {t('common:action.refresh')}
          </Button>
        }
      />

      <Banner tone="accent" icon={<ServerIcon size={14} />}>
        <div className="u-stack-sm">
          <span>{t('settings.precedence')}</span>
          <span className="u-xs u-muted">
            {t('settings.precedenceCount', { count: overrides })}
          </span>
        </div>
      </Banner>

      {role === 'viewer' ? (
        <Banner tone="caution" icon={<LockIcon size={14} />}>
          {t('settings.readOnly')}
        </Banner>
      ) : null}

      {/* Above the generic editors. The switch below edits the same setting,
          but only this card can show what turning it on produced. */}
      <DemoCard canWrite={canWriteSensitive} />

      {query.isError ? (
        <Card>
          <ErrorState
            error={query.error}
            onRetry={() => {
              void query.refetch()
            }}
          />
        </Card>
      ) : query.isLoading ? (
        <div className="u-stack">
          {[0, 1, 2].map((index) => (
            <Card key={index} title={<Skeleton width={140} height={16} />}>
              <div className="u-stack-sm">
                <Skeleton height={38} />
                <Skeleton height={38} />
                <Skeleton height={38} />
              </div>
            </Card>
          ))}
        </div>
      ) : groups.length === 0 ? (
        <Card>
          <EmptyState title={t('settings.empty.title')} description={t('settings.empty.description')} />
        </Card>
      ) : (
        groups.map(({ group, rows }) => (
          <Card
            key={group}
            title={t(`settings.group.${group}`)}
            description={t(`settings.groupHint.${group}`)}
          >
            <div>
              {rows.map((row) => (
                <SettingEditor
                  key={row.key}
                  row={row}
                  canWrite={canWrite}
                  canWriteSensitive={canWriteSensitive}
                  onSaved={refresh}
                />
              ))}
            </div>
          </Card>
        ))
      )}
    </div>
  )
}
