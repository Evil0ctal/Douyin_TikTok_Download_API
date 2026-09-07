import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
  type UseMutationOptions,
  type UseMutationResult,
  type UseQueryOptions,
  type UseQueryResult,
} from '@tanstack/react-query'

import { apiGet, type ApiError, type QueryParams } from '@/lib/api'

export interface ApiQueryOptions<T>
  extends Omit<UseQueryOptions<T, ApiError, T, QueryKey>, 'queryKey' | 'queryFn'> {
  /** Query key. Include every parameter that changes the result. */
  key: QueryKey
  path: string
  params?: QueryParams
  /** Poll interval in ms; use a value from POLL in lib/query.ts. */
  poll?: number | false
}

/**
 * useQuery wired to the API client: errors arrive as ApiError, the request is
 * cancelled when the component unmounts, and 202 responses are already resolved
 * to their task result by the client.
 */
export function useApiQuery<T>({
  key,
  path,
  params,
  poll,
  ...options
}: ApiQueryOptions<T>): UseQueryResult<T, ApiError> {
  return useQuery<T, ApiError, T, QueryKey>({
    queryKey: key,
    queryFn: ({ signal }) => apiGet<T>(path, { params, signal }),
    ...(poll ? { refetchInterval: poll } : {}),
    ...options,
    // The endpoint behind the entry. Query keys are page-authored and say
    // nothing about the wire, so this is what lets lib/query.ts expire only the
    // responses the server renders per language.
    meta: { ...options.meta, path },
  })
}

export function useApiMutation<TData, TVariables = void>(
  mutationFn: (variables: TVariables) => Promise<TData>,
  options: Omit<UseMutationOptions<TData, ApiError, TVariables>, 'mutationFn'> = {},
): UseMutationResult<TData, ApiError, TVariables> {
  return useMutation<TData, ApiError, TVariables>({ mutationFn, ...options })
}

/** Invalidates one query key prefix; the usual follow-up to a mutation. */
export function useInvalidate(): (key: QueryKey) => Promise<void> {
  const client = useQueryClient()
  return (key: QueryKey) => client.invalidateQueries({ queryKey: key })
}
