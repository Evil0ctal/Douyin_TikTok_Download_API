/**
 * react-i18next with ICU message format.
 *
 * ICU rather than simple interpolation because plural rules differ: English has
 * one/other, Chinese has neither. String concatenation is guaranteed to be wrong
 * in one of the two languages (docs/design/14-i18n.md).
 *
 * Keys are grouped by domain, not by page: one status name appears on Overview,
 * Identities and Logs, and three copies drift apart.
 */

import i18next from 'i18next'
import ICU from 'i18next-icu'
import { initReactI18next } from 'react-i18next'

import enCommon from '@/locales/en/common.json'
import enConsole from '@/locales/en/console.json'
import enErrors from '@/locales/en/errors.json'
import enSetup from '@/locales/en/setup.json'
import zhCommon from '@/locales/zh/common.json'
import zhConsole from '@/locales/zh/console.json'
import zhErrors from '@/locales/zh/errors.json'
import zhSetup from '@/locales/zh/setup.json'

import { currentLanguage, DEFAULT_LANGUAGE, LANGUAGES, subscribeLanguage } from './language'

export const NAMESPACES = ['common', 'console', 'errors', 'setup'] as const
export type Namespace = (typeof NAMESPACES)[number]

const resources = {
  en: { common: enCommon, console: enConsole, errors: enErrors, setup: enSetup },
  zh: { common: zhCommon, console: zhConsole, errors: zhErrors, setup: zhSetup },
} as const

let initialized = false

export function initI18n(): typeof i18next {
  if (initialized) return i18next
  initialized = true

  void i18next
    .use(new ICU({ memoize: true }))
    .use(initReactI18next)
    .init({
      lng: currentLanguage(),
      // A missing translation falls back to English rather than showing the raw
      // key: translations always lag features, and a raw key reads as a bug.
      fallbackLng: DEFAULT_LANGUAGE,
      supportedLngs: [...LANGUAGES],
      ns: [...NAMESPACES],
      defaultNS: 'common',
      resources,
      interpolation: { escapeValue: false },
      returnEmptyString: false,
      debug: false,
    })

  if (import.meta.env.DEV) {
    // Development-only signal that a key is missing in the active language.
    i18next.on('missingKey', (lngs, namespace, key) => {
      console.warn(`[i18n] missing key ${namespace}:${key} for ${String(lngs)}`)
    })
  }

  subscribeLanguage((language) => {
    void i18next.changeLanguage(language)
  })

  return i18next
}

export { i18next }
