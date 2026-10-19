import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Checkbox, CodeBlock, Select } from '@/components'
import { useApiQuery } from '@/hooks'
import { paths } from '@/lib/endpoints'
import type { Platform } from '@/lib/types'

import {
  CATALOG,
  type CapabilityRow,
  type CapabilityTable,
} from '../playgroundCatalog'
import type { IdentityRow, PoolLevel } from './types'

/**
 * What rotation may hand an identity to, as a person chooses it.
 *
 * The API has always taken three shapes - null for every endpoint, `[]` for
 * none, a list for those - and the console offered one switch for the middle
 * one. Somebody who wanted their logins kept for the author feed found the
 * switch, turned it on, and the author feed stopped using them: "only the
 * requests that name it" is all an empty list means, and the feed never names
 * an identity.
 */
export type RotationMode = 'all' | 'limited' | 'reserved'

export interface RotationDraft {
  mode: RotationMode
  /** The endpoints a `limited` identity serves. Kept across mode changes. */
  picked: readonly string[]
}

export function draftOf(endpoints: readonly string[] | null | undefined): RotationDraft {
  if (endpoints == null) return { mode: 'all', picked: [] }
  if (endpoints.length === 0) return { mode: 'reserved', picked: [] }
  return { mode: 'limited', picked: endpoints }
}

/** The body `POST /identities/{id}/rotation` takes, or null when it is not one. */
export function endpointsOf(draft: RotationDraft): { endpoints: string[] | null } | null {
  if (draft.mode === 'all') return { endpoints: null }
  if (draft.mode === 'reserved') return { endpoints: [] }
  return draft.picked.length > 0 ? { endpoints: [...draft.picked].sort() } : null
}

export function sameRotation(
  a: readonly string[] | null | undefined,
  b: readonly string[] | null | undefined,
): boolean {
  if (a == null || b == null) return a == null && b == null
  if (a.length !== b.length) return false
  const sorted = [...b].sort()
  return [...a].sort().every((value, index) => value === sorted[index])
}

/** Whether unpinned traffic for `endpoint` may use an identity with this list. */
function rotatesFor(endpoints: readonly string[] | null | undefined, endpoint: string): boolean {
  return endpoints == null || endpoints.includes(endpoint)
}

/** What a guest misses on an endpoint, as the checklist marks it. */
export type LoginHint = 'required' | 'paging' | 'preferred'

export function loginHint(row: CapabilityRow): LoginHint | null {
  if (row.login === 'required') return 'required'
  if (row.paging_login === 'required') return 'paging'
  if (row.login === 'preferred' || row.paging_login === 'preferred') return 'preferred'
  return null
}

/**
 * The endpoints of one platform rotation can hand out, from the capability
 * table. Session-bound ones are left out: they answer only about the identity
 * that asks, so they are refused without `identity=` and rotation never
 * reaches them whatever a list says.
 */
export function useRotationEndpoints(platform: Platform | null): {
  rows: CapabilityRow[]
  loading: boolean
} {
  const table = useApiQuery<CapabilityTable>({
    key: ['system', 'capabilities'],
    path: paths.system.capabilities,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  })
  const rows = useMemo(
    () =>
      platform ? (table.data?.[platform] ?? []).filter((row) => !row.session_bound) : [],
    [table.data, platform],
  )
  return { rows, loading: table.isLoading }
}

/** An endpoint's name as the Playground shows it, the wire name otherwise. */
export function useEndpointLabel(): (row: Pick<CapabilityRow, 'operation' | 'endpoint'>) => string {
  const { t } = useTranslation('console')
  return (row) => {
    const entry =
      CATALOG.find((candidate) => candidate.operation === row.operation) ??
      (/^(popular|ranking)/.test(row.operation)
        ? CATALOG.find((candidate) => candidate.id === 'popular')
        : undefined)
    return entry ? t(`playground.endpoint.${entry.id}`, { defaultValue: row.endpoint }) : row.endpoint
  }
}

/**
 * The endpoints that need a login and would be left without one if `next`
 * took this identity out of them, or null when they would not.
 *
 * Approximate on purpose: the pool counts a platform's logins that serve any
 * endpoint needing one, not each endpoint's, so this asks only when this is
 * the last of them. Asking too rarely is the failure that matters - the switch
 * that reads "keep my account safe" also reads "every later page now fails".
 */
export function loginEndpointsLost(
  identity: IdentityRow,
  pool: PoolLevel | undefined,
  next: readonly string[] | null,
): string[] | null {
  if (!identity.authenticated || !pool) return null
  if (identity.state !== 'active' && identity.state !== 'degraded') return null
  const row = pool.platforms.find((entry) => entry.platform === identity.platform)
  const needed = row?.login_required_for ?? []
  if (!row?.logins || needed.length === 0 || row.logins.rotating > 1) return null
  const lost = needed.filter(
    (endpoint) =>
      rotatesFor(identity.rotation_endpoints, endpoint) && !rotatesFor(next, endpoint),
  )
  return lost.length > 0 ? lost : null
}

interface RotationEditorProps {
  /** Null when the identities being set span platforms: no list is offered. */
  platform: Platform | null
  value: RotationDraft
  onChange: (next: RotationDraft) => void
  disabled?: boolean
  /** One identity's id, for the `identity=` a reserved one is reached with. */
  identityId?: string
}

/** The three choices, and the endpoint list the middle one needs. */
export function RotationEditor({
  platform,
  value,
  onChange,
  disabled = false,
  identityId,
}: RotationEditorProps) {
  const { t } = useTranslation(['console', 'common'])
  const label = useEndpointLabel()
  const { rows } = useRotationEndpoints(platform)
  const known = new Set(rows.map((row) => row.endpoint))
  // A list set through the API can name an endpoint the table does not offer.
  // Shown, so saving does not silently drop it.
  const unlisted = value.picked.filter((endpoint) => !known.has(endpoint))
  const wanted = rows.filter((row) => loginHint(row) !== null).map((row) => row.endpoint)

  const toggle = (endpoint: string, on: boolean) => {
    const picked = on
      ? [...new Set([...value.picked, endpoint])]
      : value.picked.filter((entry) => entry !== endpoint)
    onChange({ ...value, picked })
  }

  return (
    <div className="u-stack-sm">
      <Select
        label={t('console:identity.rotation.mode')}
        value={value.mode}
        disabled={disabled}
        onChange={(event) => {
          onChange({ ...value, mode: event.target.value as RotationMode })
        }}
        options={(['all', 'limited', 'reserved'] as const).map((mode) => ({
          value: mode,
          label: t(`console:identity.rotation.modes.${mode}`),
          disabled: mode === 'limited' && platform === null,
        }))}
      />
      <p className="u-xs u-secondary" style={{ margin: 0 }}>
        {platform === null && value.mode !== 'limited'
          ? t(`console:identity.rotation.modeHint.${value.mode}`) +
            ' ' +
            t('console:identity.rotation.mixedPlatforms')
          : t(`console:identity.rotation.modeHint.${value.mode}`)}
      </p>

      {value.mode === 'reserved' && identityId ? (
        <CodeBlock language="text" code={`identity=${identityId}`} />
      ) : null}

      {value.mode === 'limited' && platform !== null ? (
        <div className="u-stack-sm">
          <div className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
            <Button
              size="sm"
              variant="secondary"
              disabled={disabled || wanted.length === 0}
              onClick={() => {
                onChange({ ...value, picked: wanted })
              }}
            >
              {t('console:identity.rotation.pickLogin')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled}
              onClick={() => {
                onChange({ ...value, picked: rows.map((row) => row.endpoint) })
              }}
            >
              {t('console:identity.rotation.pickAll')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled || value.picked.length === 0}
              onClick={() => {
                onChange({ ...value, picked: [] })
              }}
            >
              {t('console:identity.rotation.pickNone')}
            </Button>
          </div>
          <div className="u-stack-sm" role="group" aria-label={t('console:identity.rotation.mode')}>
            {rows.map((row) => {
              const hint = loginHint(row)
              return (
                <Checkbox
                  key={row.endpoint}
                  checked={value.picked.includes(row.endpoint)}
                  disabled={disabled}
                  onChange={(event) => {
                    toggle(row.endpoint, event.target.checked)
                  }}
                  label={
                    <span className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
                      <span>{label(row)}</span>
                      {hint ? <LoginTag hint={hint} /> : null}
                    </span>
                  }
                  hint={<span className="u-mono">{row.endpoint}</span>}
                />
              )
            })}
            {unlisted.map((endpoint) => (
              <Checkbox
                key={endpoint}
                checked
                disabled={disabled}
                onChange={(event) => {
                  toggle(endpoint, event.target.checked)
                }}
                label={<span className="u-mono">{endpoint}</span>}
                hint={t('console:identity.rotation.unlisted')}
              />
            ))}
          </div>
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('console:identity.rotation.sessionBoundNote')}
          </p>
          {value.picked.length === 0 ? (
            <p className="u-xs" style={{ margin: 0, color: 'var(--caution)' }}>
              {t('console:identity.rotation.pickSome')}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

/** What a guest misses on an endpoint, beside its name wherever one is listed. */
export function LoginTag({ hint }: { hint: LoginHint }) {
  const { t } = useTranslation('console')
  return (
    <span
      className="u-xs u-nowrap"
      style={{ color: hint === 'preferred' ? 'var(--text-muted)' : 'var(--caution)' }}
      title={t(`identity.rotation.loginHint.${hint}`)}
    >
      {t(`identity.rotation.login.${hint}`)}
    </span>
  )
}
