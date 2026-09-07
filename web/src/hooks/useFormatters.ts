import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import { formatters, type Formatters } from '@/lib/format'
import { currentLanguage, isLanguage } from '@/lib/language'

/**
 * Formatters bound to the active UI language. Re-created only when the language
 * changes, so tables can call these per cell without rebuilding Intl objects.
 */
export function useFormatters(): Formatters {
  const { i18n } = useTranslation()
  const language = isLanguage(i18n.language) ? i18n.language : currentLanguage()
  return useMemo(() => formatters(language), [language])
}
