/**
 * TanStack Query defaults.
 *
 * The console refreshes by polling every 5-10s; there is no WebSocket, because a
 * single-host self-hosted tool never has more than a handful of viewers
 * (docs/design/07-frontend.md).
 */

import { QueryClient } from '@tanstack/react-query'

import { isApiError } from './api'

/** Poll intervals. Pass one as refetchInterval; do not invent new cadences. */
export const POLL = {
  /** Pool state and anything an operator watches during an incident. */
  fast: 5_000,
  /** Charts and aggregate counters. */
  normal: 8_000,
  /** Slow-moving inventories: keys, users, settings. */
  slow: 10_000,
} as const

const MAX_RETRIES = 2

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => {
          // Never retry what the error contract says is permanent.
          if (isApiError(error) && !error.retryable) return false
          return failureCount < MAX_RETRIES
        },
        retryDelay: (attemptIndex, error) => {
          // Honour Retry-After when the server told us when to come back.
          if (isApiError(error) && error.retryAfter !== null) {
            return Math.min(error.retryAfter * 1000, 30_000)
          }
          return Math.min(1_000 * 2 ** attemptIndex, 15_000)
        },
        staleTime: 3_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        // A poll that fires while a tab is hidden wakes nothing useful.
        refetchIntervalInBackground: false,
      },
      mutations: {
        retry: false,
      },
    },
  })
}

/** Query-key helper so keys stay consistent across pages. */
export function qk(...parts: ReadonlyArray<string | number | boolean | null | undefined | object>) {
  return parts.filter((part) => part !== undefined)
}
