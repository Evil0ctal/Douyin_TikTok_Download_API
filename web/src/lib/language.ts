/**
 * UI language state.
 *
 * Kept out of i18n.ts so that modules which merely need the current language
 * (the API client sends it as Accept-Language, the formatters map it to a BCP-47
 * locale) do not have to import i18next.
 *
 * Resolution order, per docs/design/14-i18n.md:
 *   explicit user choice (localStorage) -> navigator.language prefix -> en
 */

import { readStored, writeStored } from './storage'

export const LANGUAGES = ['en', 'zh'] as const

export type Language = (typeof LANGUAGES)[number]

export const DEFAULT_LANGUAGE: Language = 'en'

/** BCP-47 locale used for Intl. Never shown to the user. */
export const INTL_LOCALE: Record<Language, string> = {
  en: 'en-US',
  zh: 'zh-CN',
}

const STORAGE_KEY = 'language'

export function isLanguage(value: unknown): value is Language {
  return typeof value === 'string' && (LANGUAGES as readonly string[]).includes(value)
}

function detect(): Language {
  const stored = readStored(STORAGE_KEY)
  if (isLanguage(stored)) return stored

  const candidates =
    typeof navigator === 'undefined'
      ? []
      : [...(navigator.languages ?? []), navigator.language].filter(Boolean)

  for (const candidate of candidates) {
    const prefix = candidate.toLowerCase().split('-')[0]
    if (isLanguage(prefix)) return prefix
  }
  return DEFAULT_LANGUAGE
}

let current: Language = detect()
const listeners = new Set<(language: Language) => void>()

export function currentLanguage(): Language {
  return current
}

/** Locale string for Intl formatters bound to the current UI language. */
export function currentLocale(): string {
  return INTL_LOCALE[current]
}

export function setLanguage(language: Language, options: { persist?: boolean } = {}): void {
  if (language === current) return
  current = language
  if (options.persist !== false) writeStored(STORAGE_KEY, language)
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('lang', language)
  }
  for (const listener of listeners) listener(language)
}

export function subscribeLanguage(listener: (language: Language) => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
