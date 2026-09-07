import { useEffect, useRef, useState } from 'react'

/**
 * True for a moment after `value` changes.
 *
 * Polled values must never animate - a table that fades on every 5s refresh is
 * unreadable. A status that actually CHANGED is the one case that earns a
 * highlight, and only once (docs/design/12).
 */
export function useValueChangeFlash(value: unknown, durationMs = 900): boolean {
  const previous = useRef(value)
  const [flashing, setFlashing] = useState(false)

  useEffect(() => {
    if (Object.is(previous.current, value)) return
    previous.current = value

    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return

    setFlashing(true)
    const timer = window.setTimeout(() => {
      setFlashing(false)
    }, durationMs)
    return () => {
      window.clearTimeout(timer)
    }
  }, [value, durationMs])

  return flashing
}
