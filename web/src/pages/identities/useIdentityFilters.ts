import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'wouter'

import {
  IDENTITY_SOURCES,
  IDENTITY_STATES,
  PLATFORMS,
  type IdentitySource,
  type IdentityState,
  type Platform,
} from '@/lib/types'

/** `login=` in the address, as contract C3 spells it. */
export const LOGIN_FILTERS = ['any', 'yes', 'no'] as const
export type LoginFilter = (typeof LOGIN_FILTERS)[number]

/** The rotation an identity is in; `any` is no filter. */
export const ROTATION_FILTERS = ['any', 'rotating', 'reserved', 'limited'] as const
export type RotationFilter = (typeof ROTATION_FILTERS)[number]

export interface IdentityFilters {
  platform: Platform | 'all'
  state: IdentityState | 'all'
  source: IdentitySource | 'all'
  login: LoginFilter
  rotation: RotationFilter
  /** Free text over the rows already loaded. */
  q: string
  /** Show what the default view holds back: retired and spent identities. */
  all: boolean
  /** The identity whose drawer is open. */
  id: string | null
}

const DEFAULTS: IdentityFilters = {
  platform: 'all',
  state: 'all',
  source: 'all',
  login: 'any',
  rotation: 'any',
  q: '',
  all: false,
  id: null,
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : fallback
}

/**
 * The page's filters, kept in the address.
 *
 * `/identities?platform=bilibili&login=yes&id=<uuid>` is contract C3: the
 * Overview and the request log link here with it, and a link that landed on an
 * unfiltered table with no drawer open told the reader nothing. In the address
 * rather than in state for the same reason a link can be shared at all, and so
 * the back button and a reload keep what somebody was looking at.
 *
 * A value this page does not know reads as no filter rather than an empty
 * table: a link written against a newer build should still open.
 */
export function useIdentityFilters(): [
  IdentityFilters,
  (patch: Partial<IdentityFilters>) => void,
] {
  const [params, setParams] = useSearchParams()

  const filters = useMemo<IdentityFilters>(
    () => ({
      platform: oneOf(params.get('platform'), PLATFORMS, 'all'),
      state: oneOf(params.get('state'), IDENTITY_STATES, 'all'),
      source: oneOf(params.get('source'), IDENTITY_SOURCES, 'all'),
      login: oneOf(params.get('login'), LOGIN_FILTERS, 'any'),
      rotation: oneOf(params.get('rotation'), ROTATION_FILTERS, 'any'),
      q: params.get('q') ?? '',
      all: params.get('all') === '1',
      id: params.get('id') || null,
    }),
    [params],
  )

  const update = useCallback(
    (patch: Partial<IdentityFilters>) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current)
          for (const [key, value] of Object.entries(patch)) {
            const fallback = DEFAULTS[key as keyof IdentityFilters]
            if (value === fallback || value === '' || value === null || value === false) {
              next.delete(key)
            } else {
              next.set(key, value === true ? '1' : String(value))
            }
          }
          return next
        },
        // Replaced rather than pushed: every keystroke in the search box is a
        // change, and a history entry per letter makes Back useless.
        { replace: true },
      )
    },
    [setParams],
  )

  return [filters, update]
}
