import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'

import { marked } from '@/lib/spacing'

import { useFormatters } from './useFormatters'

/**
 * A platform as people say it - Douyin, TikTok, Bilibili - in this language.
 *
 * Pages printed the wire value, which is an identifier and not a name; in
 * Chinese it read as the one English word in a sentence. Falls back to the
 * wire value for a platform this build has no name for.
 */
export function usePlatformName(): (platform: string) => string {
  const { t } = useTranslation('console')
  return useCallback(
    (platform: string) => t(`platform.name.${platform}`, { defaultValue: platform }),
    [t],
  )
}

/**
 * Platform names for a `{platform}` or `{platforms}` in a translated sentence:
 * one name, or several in a row.
 *
 * Only for a value passed to `t()`. The name comes back marked, and the marks
 * are turned into the spacing the sentence needs once it is translated (see
 * lib/spacing.ts); shown anywhere else, the marks would be dropped unspaced.
 */
export function usePlatformInSentence(): (platforms: string | readonly string[]) => string {
  const name = usePlatformName()
  const format = useFormatters()
  return useCallback(
    (platforms: string | readonly string[]) =>
      marked(typeof platforms === 'string' ? name(platforms) : format.list(platforms.map(name))),
    [name, format],
  )
}
