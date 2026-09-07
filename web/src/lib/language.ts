/**
 * UI language state.
 *
 * Kept out of i18n.ts so that modules which merely need the current language
 * (the API client sends it as Accept-Language, the formatters map it to a BCP-47
 * locale) do not have to import i18next.
 *
 * Resolution order, per docs/design/14-i18n.md:
 *   ?lang= on this navigation, or earlier in this tab
 *     -> explicit user choice (localStorage)
 *     -> the language the server negotiated for this request
 *     -> navigator.language prefix
 *     -> en
 *
 * The server step is the one that makes the console agree with the API. Both
 * now answer to the same Accept-Language header, so a request that gets Chinese
 * error messages no longer arrives at an English shell.
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

/** The parameter the API and /docs already answer to (dtk/i18n/negotiate.py). */
const QUERY_PARAM = 'lang'

/**
 * Where a ?lang= visit is remembered, and why it is not localStorage.
 *
 * A shared link says which language this visit should be in; it does not say
 * what the person opening it prefers from now on. Persisting it would let one
 * link from a colleague quietly re-language every future session on that
 * machine. It does have to outlive the first navigation, though - the router
 * drops the query string the moment the user clicks anything - so it lives for
 * the tab that opened the link and dies with it.
 *
 * Written directly rather than through lib/storage.ts, which wraps localStorage
 * only; the 'dtk.' namespace is the same one.
 */
const LINK_STORAGE_KEY = 'dtk.language.link'

export function isLanguage(value: unknown): value is Language {
  return typeof value === 'string' && (LANGUAGES as readonly string[]).includes(value)
}

/** The language this navigation asked for in the URL, or null. */
function fromQuery(): Language | null {
  if (typeof window === 'undefined') return null
  const value = new URLSearchParams(window.location.search).get(QUERY_PARAM)
  // An unsupported value is ignored rather than repaired, matching
  // resolve_language(): ?lang=fr still lands on the negotiated language.
  return isLanguage(value) ? value : null
}

function readLinkLanguage(): Language | null {
  try {
    const value = window.sessionStorage.getItem(LINK_STORAGE_KEY)
    return isLanguage(value) ? value : null
  } catch {
    return null
  }
}

function writeLinkLanguage(language: Language | null): void {
  try {
    if (language === null) window.sessionStorage.removeItem(LINK_STORAGE_KEY)
    else window.sessionStorage.setItem(LINK_STORAGE_KEY, language)
  } catch {
    // Same policy as lib/storage.ts: a blocked store costs a preference, not
    // the page.
  }
}

/**
 * The language the server picked for the document, or null.
 *
 * Only trusted when the document says the value was negotiated. index.html
 * ships with lang="en" baked in, so reading the attribute unconditionally would
 * pin every `npm run dev` session and every unpatched build to English and
 * never reach the navigator check below.
 */
function negotiated(): Language | null {
  if (typeof document === 'undefined') return null
  const root = document.documentElement
  if (root.getAttribute('data-language-source') !== 'negotiated') return null
  const value = root.getAttribute('lang')
  return isLanguage(value) ? value : null
}

function detect(): Language {
  // ?lang= outranks the stored preference: it is the most explicit thing the
  // reader did, it is what the API and /docs already obey, and the alternative
  // is a link that visibly does nothing for anyone who has ever used the
  // switcher. It is remembered per tab rather than persisted, see
  // LINK_STORAGE_KEY.
  const requested = fromQuery()
  if (requested) {
    writeLinkLanguage(requested)
    return requested
  }

  const link = readLinkLanguage()
  if (link) return link

  const stored = readStored(STORAGE_KEY)
  if (isLanguage(stored)) return stored

  const fromServer = negotiated()
  if (fromServer) return fromServer

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

/**
 * Publish the language on <html>.
 *
 * data-language-source is left alone: it records where the *initial* value came
 * from and it is the server's claim to make, not ours. detect() reads it once,
 * at boot, and never again.
 */
function applyDocumentLanguage(language: Language): void {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('lang', language)
}

/**
 * Stamp the resolved language onto the document. Called once from main.tsx.
 *
 * index.html ships lang="en" and the server rewrites it only for a document it
 * served itself, so a console that detected Chinese any other way would keep
 * announcing itself as English - to screen readers, to the browser's translate
 * prompt, and to every :lang() rule.
 */
export function initLanguage(): void {
  applyDocumentLanguage(current)
}

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
  if (options.persist !== false) {
    writeStored(STORAGE_KEY, language)
    // Choosing by hand ends the link's hold on this tab; leaving it in place
    // would undo the choice on the next reload.
    writeLinkLanguage(null)
  }
  applyDocumentLanguage(language)
  for (const listener of listeners) listener(language)
}

export function subscribeLanguage(listener: (language: Language) => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
