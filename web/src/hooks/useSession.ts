import { useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'

import { apiGet, apiPost, isApiError, type ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import type { SessionUser } from '@/lib/types'

export const SESSION_KEY = ['auth', 'session'] as const

/**
 * What /auth/me answers with. The principal is nested under `user`; the rest
 * describes the credential that carried the request and no page needs it.
 */
interface Principal {
  user: SessionUser
  rate_limit_per_min?: number | null
  api_key_id?: string | null
}

/**
 * The signed-in user, or null when there is no session.
 *
 * A 401 here is an answer, not a failure: the console renders a signed-out top
 * bar rather than an error, and pages that need auth get bounced by the API
 * client's unauthenticated handler.
 *
 * No other failure is treated that way. Every role gate reads this hook, so a
 * transport fault answering for "signed out" would silently open the controls
 * on every page instead of showing that the console cannot tell who is here.
 */
export function useSession(): UseQueryResult<SessionUser | null, ApiError> {
  return useQuery<SessionUser | null, ApiError>({
    queryKey: SESSION_KEY,
    queryFn: async ({ signal }) => {
      try {
        const principal = await apiGet<Principal>(paths.auth.me, {
          signal,
          redirectOnUnauthenticated: false,
        })
        return principal.user
      } catch (error) {
        if (isApiError(error) && error.code === 'UNAUTHENTICATED') return null
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
