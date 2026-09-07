/**
 * Theme state: writes data-theme onto <html>, persists the choice and follows
 * prefers-color-scheme until the user picks a side (docs/design/12).
 *
 * index.html applies the same resolution before first paint; this module owns it
 * from then on. Exposed as an external store rather than a React context so that
 * non-component code (charts measuring colours, for instance) can read it too.
 */

import { useSyncExternalStore } from 'react'

import { readStored, writeStored } from './storage'

export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

export const THEME_MODES: readonly ThemeMode[] = ['dark', 'light', 'system']

function isThemeMode(value: unknown): value is ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system'
}

function systemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || !window.matchMedia) return 'dark'
  return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
}

function resolve(mode: ThemeMode): ResolvedTheme {
  return mode === 'system' ? systemTheme() : mode
}

interface ThemeSnapshot {
  readonly mode: ThemeMode
  readonly resolved: ResolvedTheme
}

const storedMode = readStored(STORAGE_KEY)
let snapshot: ThemeSnapshot = Object.freeze({
  mode: isThemeMode(storedMode) ? storedMode : 'system',
  resolved: resolve(isThemeMode(storedMode) ? storedMode : 'system'),
})

const listeners = new Set<() => void>()

function apply(next: ThemeSnapshot): void {
  // Identity check keeps useSyncExternalStore from re-rendering on every poll of
  // the media query.
  if (next.mode === snapshot.mode && next.resolved === snapshot.resolved) return
  snapshot = Object.freeze(next)
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme', next.resolved)
  }
  for (const listener of listeners) listener()
}

export function getThemeMode(): ThemeMode {
  return snapshot.mode
}

export function getResolvedTheme(): ResolvedTheme {
  return snapshot.resolved
}

export function setThemeMode(mode: ThemeMode): void {
  writeStored(STORAGE_KEY, mode)
  apply({ mode, resolved: resolve(mode) })
}

/** Installs the media-query listener. Called once from main.tsx. */
export function initTheme(): () => void {
  apply({ mode: snapshot.mode, resolved: resolve(snapshot.mode) })
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}

  const media = window.matchMedia(DARK_QUERY)
  const onChange = (): void => {
    if (snapshot.mode !== 'system') return
    apply({ mode: 'system', resolved: systemTheme() })
  }
  media.addEventListener('change', onChange)
  return () => {
    media.removeEventListener('change', onChange)
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function getSnapshot(): ThemeSnapshot {
  return snapshot
}

export interface UseThemeResult extends ThemeSnapshot {
  setMode: (mode: ThemeMode) => void
  /** Flips to the opposite of what is currently on screen. */
  toggle: () => void
}

export function useTheme(): UseThemeResult {
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  return {
    mode: state.mode,
    resolved: state.resolved,
    setMode: setThemeMode,
    toggle: () => {
      setThemeMode(state.resolved === 'dark' ? 'light' : 'dark')
    },
  }
}
