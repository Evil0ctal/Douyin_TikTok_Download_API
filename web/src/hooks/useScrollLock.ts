import { useEffect } from 'react'

/**
 * Locks background scrolling while an overlay is open, compensating for the
 * scrollbar so the layout does not jump sideways.
 */
export function useScrollLock(active: boolean): void {
  useEffect(() => {
    if (!active) return
    const { body, documentElement } = document
    const previousOverflow = body.style.overflow
    const previousPadding = body.style.paddingRight
    const scrollbar = window.innerWidth - documentElement.clientWidth

    body.style.overflow = 'hidden'
    if (scrollbar > 0) body.style.paddingRight = `${scrollbar}px`

    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPadding
    }
  }, [active])
}
