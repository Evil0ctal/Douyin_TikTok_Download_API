import { useEffect, type RefObject } from 'react'

/** Closes popovers and menus when the pointer lands elsewhere. */
export function useOnClickOutside(
  ref: RefObject<HTMLElement | null>,
  handler: () => void,
  active = true,
): void {
  useEffect(() => {
    if (!active) return
    const onPointerDown = (event: PointerEvent): void => {
      const element = ref.current
      if (!element || element.contains(event.target as Node)) return
      handler()
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
    }
  }, [ref, handler, active])
}
