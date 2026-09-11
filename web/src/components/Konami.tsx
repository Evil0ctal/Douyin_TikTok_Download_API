import { useEffect, useRef, useState } from 'react'

import styles from './konami.module.css'

/**
 * The sequence, and nothing else about it is configurable.
 *
 * `event.key` rather than `keyCode`: the arrows and letters arrive the same way
 * on every layout that matters, and the deprecated one would have been a
 * decision to explain rather than a shortcut.
 */
const SEQUENCE = [
  'ArrowUp',
  'ArrowUp',
  'ArrowDown',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'ArrowLeft',
  'ArrowRight',
  'b',
  'a',
] as const

/** How long the stars fall for. Matches the CSS; both are two seconds. */
const DURATION_MS = 2000

const STAR_COUNT = 28

/**
 * The Konami code, and what it does: a short fall of stars, then nothing.
 *
 * Deliberately not a game. A game in an operations console is something to
 * maintain, and something that will one day be open on a screen somebody is
 * using to work out why the pool is empty. This is two seconds of CSS with no
 * state left behind.
 *
 * Nothing is typed into an input while this listens: the handler ignores
 * keystrokes whose target is a field, so the sequence cannot fire while
 * somebody is filling in the playground.
 */
export function Konami() {
  const [playing, setPlaying] = useState(false)
  const progress = useRef(0)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      const target = event.target as HTMLElement | null
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return
      if (target?.isContentEditable) return

      const wanted = SEQUENCE[progress.current]
      const pressed = event.key.length === 1 ? event.key.toLowerCase() : event.key
      if (pressed !== wanted) {
        // A wrong key restarts, but a first key of the sequence restarts *at
        // one* rather than at zero - otherwise pressing Up three times leaves
        // you further from the answer than pressing it twice.
        progress.current = pressed === SEQUENCE[0] ? 1 : 0
        return
      }
      progress.current += 1
      if (progress.current === SEQUENCE.length) {
        progress.current = 0
        setPlaying(true)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [])

  useEffect(() => {
    if (!playing) return
    const timer = window.setTimeout(() => {
      setPlaying(false)
    }, DURATION_MS)
    return () => {
      window.clearTimeout(timer)
    }
  }, [playing])

  if (!playing) return null

  return (
    <div className={styles.sky} aria-hidden="true">
      {Array.from({ length: STAR_COUNT }, (_, index) => (
        <span
          key={index}
          className={styles.star}
          style={{
            // Positions and timings only. Every colour comes from the token
            // file, which is what the design system asks for and what makes
            // this legible in both themes without a second rule.
            left: `${(index * 97) % 100}%`,
            animationDelay: `${(index % 7) * 90}ms`,
            animationDuration: `${1200 + ((index * 137) % 700)}ms`,
            fontSize: `${10 + ((index * 13) % 10)}px`,
          }}
        >
          ★
        </span>
      ))}
    </div>
  )
}
