import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'

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
