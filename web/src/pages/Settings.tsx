import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'

import {
  Button,
  Card,
  Checkbox,
  DemoCard,
  EmptyState,
  ErrorState,
  Input,
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
import { atLeast } from '@/lib/roles'

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

/** The Demo mode card's anchor, for the row that points at it. */
const DEMO_CARD_ID = 'demo-mode'

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

/**
 * Keys with an editor of their own, shown here as a pointer to it instead of
 * as a generic row.
 *
 * The generic editor is not merely redundant for these, it is wrong. Switching
 * `demo.enabled` from a plain toggle mints the demo pair and throws away the
 * only response that carries it. `notify.channels` is a list of objects that
 * the row editor rendered as `[object Object]`, and saving the row wrote that
 * back over every channel. Kept in the group they belong to, so a search for a
 * key still finds where to change it.
 */
interface Destination {
  id: 'demo' | 'notifications' | 'endpointAccess' | 'system'
  /** A console route, or an anchor on this page. */
  href: string
}

const MANAGED: ReadonlyArray<{ match: (key: string) => boolean; to: Destination }> = [
  { match: (key) => key === 'demo.enabled', to: { id: 'demo', href: `#${DEMO_CARD_ID}` } },
  { match: (key) => key.startsWith('notify.'), to: { id: 'notifications', href: '/notifications' } },
  {
    match: (key) => key === 'api.public_endpoints',
    to: { id: 'endpointAccess', href: '/endpoint-access' },
  },
  { match: (key) => key === 'system.check_updates', to: { id: 'system', href: '/system' } },
]

function managedBy(key: string): Destination | null {
  return MANAGED.find((entry) => entry.match(key))?.to ?? null
}

function groupOf(key: string): string {
  const prefix = key.split('.')[0] ?? ''
  return KNOWN_GROUPS.has(prefix) ? prefix : 'other'
}

function belongsHere(key: string): boolean {
  return !ELSEWHERE.has(key.split('.')[0] ?? '')
}

interface Group {
  group: string
  rows: SettingRow[]
  /** Managed keys, gathered per destination so one link stands for several. */
  pointers: Array<{ to: Destination; keys: string[] }>
}

function ManagedPointer({ to, keys }: { to: Destination; keys: string[] }) {
  const { t } = useTranslation('console')
  const label = t(`settings.managed.${to.id}`)
  return (
    <div
      className="u-row u-wrap"
      style={{
        gap: 'var(--space-2)',
        padding: 'var(--space-3) 0',
        borderTop: '1px solid var(--border-subtle)',
        justifyContent: 'space-between',
      }}
    >
      <span className="u-row u-wrap" style={{ gap: 'var(--space-2)', minWidth: 0 }}>
        {keys.map((key) => (
          <code key={key} className="u-mono" style={{ fontSize: 'var(--text-sm)', color: 'var(--text)' }}>
            {key}
          </code>
        ))}
      </span>
      <span className="u-xs u-secondary">
        {to.href.startsWith('#') ? <a href={to.href}>{label}</a> : <Link href={to.href}>{label}</Link>}
      </span>
    </div>
  )
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

  const [search, setSearch] = useState('')
  const [overriddenOnly, setOverriddenOnly] = useState(false)

  const role = session.data?.role ?? null
  // The same floors the server applies in `_authorize_write`: an operator for
  // a runtime key, an administrator for a SENSITIVE one. This used to read
  // "anything but viewer", which handed the demo role every control on the
  // page; a role the console does not know yet shows none of them.
  const canWrite = atLeast(role, 'operator')
  const canWriteSensitive = atLeast(role, 'admin')

  const needle = search.trim().toLowerCase()
  const filtering = needle.length > 0 || overriddenOnly

  const groups = useMemo<Group[]>(() => {
    const rows = (query.data?.settings ?? []).filter((row) => {
      if (!belongsHere(row.key)) return false
      if (overriddenOnly && sourceOf(row) !== 'database') return false
      if (!needle) return true
      return `${row.key} ${row.description}`.toLowerCase().includes(needle)
    })
    const buckets = new Map<string, Group>()
    for (const row of rows) {
      const name = groupOf(row.key)
      const bucket = buckets.get(name) ?? { group: name, rows: [], pointers: [] }
      const to = managedBy(row.key)
      if (to === null) {
        bucket.rows.push(row)
      } else {
        const pointer = bucket.pointers.find((entry) => entry.to.id === to.id)
        if (pointer) pointer.keys.push(row.key)
        else bucket.pointers.push({ to, keys: [row.key] })
      }
      buckets.set(name, bucket)
    }
    return [...GROUP_ORDER, 'other']
      .map((name) => buckets.get(name))
      .filter((bucket): bucket is Group => bucket !== undefined)
  }, [query.data, needle, overriddenOnly])

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

      {role !== null && !canWrite ? (
        <Banner tone="caution" icon={<LockIcon size={14} />}>
          {t('settings.readOnly')}
        </Banner>
      ) : null}

      {/* Above the generic editors, which point here for `demo.enabled`: only
          this card can show what turning it on produced. */}
      <div id={DEMO_CARD_ID}>
        <DemoCard canWrite={canWriteSensitive} />
      </div>

      <div className="u-row u-wrap" style={{ gap: 'var(--space-3)' }}>
        <Input
          type="search"
          value={search}
          placeholder={t('settings.search')}
          aria-label={t('settings.search')}
          style={{ width: '260px', maxWidth: '100%' }}
          onChange={(event) => {
            setSearch(event.target.value)
          }}
        />
        <Checkbox
          checked={overriddenOnly}
          label={t('settings.overriddenOnly')}
          onChange={(event) => {
            setOverriddenOnly(event.target.checked)
          }}
        />
      </div>

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
          {filtering ? (
            <EmptyState
              title={t('settings.noMatch.title')}
              description={t('settings.noMatch.description')}
            />
          ) : (
            <EmptyState title={t('settings.empty.title')} description={t('settings.empty.description')} />
          )}
        </Card>
      ) : (
        groups.map(({ group, rows, pointers }) => (
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
              {pointers.map((pointer) => (
                <ManagedPointer key={pointer.to.id} to={pointer.to} keys={pointer.keys} />
              ))}
            </div>
          </Card>
        ))
      )}
    </div>
  )
}
