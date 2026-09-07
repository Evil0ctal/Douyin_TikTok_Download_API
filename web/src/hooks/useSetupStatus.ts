import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { apiGet, type ApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import type { SetupStatus } from '@/lib/types'

export const SETUP_STATUS_KEY = ['setup', 'status'] as const

/**
 * Gate for the whole console: while initialized is false the only reachable page
 * is the setup wizard (docs/design/06-api-auth-mcp.md).
 */
export function useSetupStatus(): UseQueryResult<SetupStatus, ApiError> {
  return useQuery<SetupStatus, ApiError>({
    queryKey: SETUP_STATUS_KEY,
    queryFn: ({ signal }) =>
      apiGet<SetupStatus>(paths.setup.status, { signal, redirectOnUnauthenticated: false }),
    staleTime: 60_000,
    retry: 1,
    refetchOnWindowFocus: false,
  })
}
