/**
 * Namespaced, failure-tolerant localStorage.
 *
 * Storage throws in private windows and when a browser blocks site data, and a
 * console that cannot remember a preference must still render. Every access is
 * therefore guarded and falls back to the caller's default.
 */

const PREFIX = 'dtk.'

export function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(PREFIX + key)
  } catch {
    return null
  }
}

export function writeStored(key: string, value: string | null): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(PREFIX + key)
    } else {
      window.localStorage.setItem(PREFIX + key, value)
    }
  } catch {
    // Preference is lost for this session; not worth surfacing to the user.
  }
}

export function readStoredJson<T>(key: string, fallback: T): T {
  const raw = readStored(key)
  if (raw === null) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function writeStoredJson(key: string, value: unknown): void {
  try {
    writeStored(key, JSON.stringify(value))
  } catch {
    // Non-serialisable value: drop it rather than throw from a setter.
  }
}
