import { useCallback, useSyncExternalStore } from 'react'

/** Reactive media query. Used for the responsive breakpoints in docs/design/12. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === 'undefined' || !window.matchMedia) return () => {}
      const media = window.matchMedia(query)
      media.addEventListener('change', onChange)
      return () => {
        media.removeEventListener('change', onChange)
      }
    },
    [query],
  )

  const getSnapshot = useCallback(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false
    return window.matchMedia(query).matches
  }, [query])

  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}

/** Below this width the sidebar becomes a drawer and tables degrade to cards. */
export const MOBILE_QUERY = '(max-width: 767px)'
export const TABLET_QUERY = '(max-width: 1279px)'

export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_QUERY)
}

export function useIsTablet(): boolean {
  return useMediaQuery(TABLET_QUERY)
}

export function usePrefersReducedMotion(): boolean {
  return useMediaQuery('(prefers-reduced-motion: reduce)')
}
