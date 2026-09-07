import { useCallback, useState } from 'react'

import { readStoredJson, writeStoredJson } from '@/lib/storage'

/**
 * State persisted in localStorage under the dtk. namespace. For view
 * preferences only - table density, sidebar collapse, chosen columns.
 */
export function useLocalStorageState<T>(
  key: string,
  initialValue: T,
): [T, (value: T | ((previous: T) => T)) => void] {
  const [state, setState] = useState<T>(() => readStoredJson(key, initialValue))

  const set = useCallback(
    (value: T | ((previous: T) => T)) => {
      setState((previous) => {
        const next =
          typeof value === 'function' ? (value as (previous: T) => T)(previous) : value
        writeStoredJson(key, next)
        return next
      })
    },
    [key],
  )

  return [state, set]
}
