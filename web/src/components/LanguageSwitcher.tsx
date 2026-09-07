import { useTranslation } from 'react-i18next'

import { currentLanguage, isLanguage, LANGUAGES, setLanguage, type Language } from '@/lib/language'

import { CheckIcon, GlobeIcon } from './Icons'
import { Menu, MenuItem } from './Menu'

/**
 * Switching takes effect immediately without a reload: i18next swaps the
 * catalogue, and every Intl formatter is keyed on the language (docs/design/14).
 */
export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  const active: Language = isLanguage(i18n.language) ? i18n.language : currentLanguage()

  return (
    <Menu label={t('language.label')} trigger={<GlobeIcon size={15} />}>
      {LANGUAGES.map((language) => (
        <MenuItem
          key={language}
          selected={active === language}
          icon={active === language ? <CheckIcon size={13} /> : <span style={{ width: 13 }} />}
          onSelect={() => {
            setLanguage(language)
          }}
        >
          {t(`language.${language}`)}
        </MenuItem>
      ))}
    </Menu>
  )
}
