import { useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'

import { apiGet, apiPost, isApiError, type ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import type { SessionUser } from '@/lib/types'

export const SESSION_KEY = ['auth', 'session'] as const

/**
 * The signed-in user, or null when there is no session.
 *
 * A 401 here is an answer, not a failure: the console renders a signed-out top
 * bar rather than an error, and pages that need auth get bounced by the API
 * client's unauthenticated handler.
 */
export function useSession(): UseQueryResult<SessionUser | null, ApiError> {
  return useQuery<SessionUser | null, ApiError>({
    queryKey: SESSION_KEY,
    queryFn: async ({ signal }) => {
      try {
        return await apiGet<SessionUser>(paths.auth.session, {
          signal,
          redirectOnUnauthenticated: false,
        })
      } catch (error) {
        if (isApiError(error) && (error.code === 'UNAUTHENTICATED' || error.status === 404)) {
          return null
        }
        throw error
      }
    },
    staleTime: 30_000,
    retry: false,
    refetchOnWindowFocus: true,
  })
}

export function useSignOut(): () => Promise<void> {
  const client = useQueryClient()
  return async () => {
    try {
      await apiPost(paths.auth.logout, undefined, { redirectOnUnauthenticated: false })
    } finally {
      client.clear()
    }
  }
}
