/**
 * TanStack Query defaults.
 *
 * The console refreshes by polling every 5-10s; there is no WebSocket, because a
 * single-host self-hosted tool never has more than a handful of viewers
 * (docs/design/07-frontend.md).
 */

import { QueryClient, type Query } from '@tanstack/react-query'

import { isApiError } from './api'
import { paths } from './endpoints'
import { subscribeLanguage } from './language'

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

/**
 * Endpoints whose body is written in the caller's language, by path prefix.
 *
 * Very little of the API is prose: rows are data and codes are stable
 * identifiers, and both read the same in either language. What the server
 * actually renders is the settings catalogue, the circuit breaker's reason
 * sentence, a task's stored error and the OpenAPI document - the handlers that
 * ask for `language(request)`. Refetching the rest on a switch would cost every
 * open page a round trip and change nothing on screen.
 */
export const LANGUAGE_SENSITIVE_PATHS: readonly string[] = [
  paths.settings.list,
  paths.endpointsHealth,
  paths.tasks.root,
  paths.docs.openapi,
]

function isLanguageSensitive(query: Query): boolean {
  // A failed query is holding a sentence the server localized when it failed,
  // and an error panel is the most visible place for a stale one. There is no
  // good data to throw away either: the entry is an error.
  if (query.state.status === 'error') return true
  const path = query.meta?.path
  return typeof path === 'string' && LANGUAGE_SENSITIVE_PATHS.some((p) => path.startsWith(p))
}

/**
 * Expire what the language changed the meaning of, and nothing else.
 *
 * useApiQuery records the endpoint each entry came from, which is what makes
 * this selective: query keys are page-authored and say nothing about the wire.
 */
export function invalidateLanguageSensitiveQueries(client: QueryClient): Promise<void> {
  return client.invalidateQueries({ predicate: isLanguageSensitive })
}

export function createQueryClient(): QueryClient {
  const client = new QueryClient({
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

  // Wired here rather than in a component: the client owns the cache, it
  // outlives every page, and a switch made on a screen that renders no query
  // still has to reach the pages behind it.
  subscribeLanguage(() => {
    void invalidateLanguageSensitiveQueries(client)
  })

  return client
}

/** Query-key helper so keys stay consistent across pages. */
export function qk(...parts: ReadonlyArray<string | number | boolean | null | undefined | object>) {
  return parts.filter((part) => part !== undefined)
}
