import { useCallback, useEffect, useRef, useState } from 'react'

export interface Size {
  width: number
  height: number
}

/**
 * Observed element size, for SVG charts that must be responsive without a
 * charting library.
 */
export function useElementSize<T extends HTMLElement>(): [
  (node: T | null) => void,
  Size,
] {
  const [size, setSize] = useState<Size>({ width: 0, height: 0 })
  const observer = useRef<ResizeObserver | null>(null)
  const node = useRef<T | null>(null)

  const ref = useCallback((element: T | null) => {
    node.current = element
    if (observer.current) {
      observer.current.disconnect()
      observer.current = null
    }
    if (!element || typeof ResizeObserver === 'undefined') return

    observer.current = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      const { width, height } = entry.contentRect
      setSize((previous) =>
        Math.abs(previous.width - width) < 1 && Math.abs(previous.height - height) < 1
          ? previous
          : { width, height },
      )
    })
    observer.current.observe(element)
    setSize({ width: element.clientWidth, height: element.clientHeight })
  }, [])

  useEffect(
    () => () => {
      observer.current?.disconnect()
    },
    [],
  )

  return [ref, size]
}
