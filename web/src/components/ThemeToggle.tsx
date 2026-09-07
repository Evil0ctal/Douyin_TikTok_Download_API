import { useTranslation } from 'react-i18next'

import { useTheme, type ThemeMode } from '@/lib/theme'

import { CheckIcon, MonitorIcon, MoonIcon, SunIcon } from './Icons'
import { Menu, MenuItem } from './Menu'

const ICONS = {
  dark: MoonIcon,
  light: SunIcon,
  system: MonitorIcon,
} as const

const ORDER: ThemeMode[] = ['dark', 'light', 'system']

/** Dark, light or follow the system. The choice persists in localStorage. */
export function ThemeToggle() {
  const { t } = useTranslation()
  const { mode, resolved, setMode } = useTheme()
  const Current = mode === 'system' ? ICONS[resolved] : ICONS[mode]

  return (
    <Menu label={t('theme.label')} trigger={<Current size={15} />}>
      {ORDER.map((option) => {
        const Icon = ICONS[option]
        return (
          <MenuItem
            key={option}
            icon={<Icon size={13} />}
            selected={mode === option}
            onSelect={() => {
              setMode(option)
            }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)' }}>
              {t(`theme.${option}`)}
              {mode === option ? <CheckIcon size={11} /> : null}
            </span>
          </MenuItem>
        )
      })}
    </Menu>
  )
}
