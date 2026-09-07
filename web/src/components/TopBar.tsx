import { useTranslation } from 'react-i18next'
import { useLocation } from 'wouter'

import { cn } from '@/lib/cn'
import { navItemFor } from '@/lib/nav'
import { useSession, useSignOut } from '@/hooks/useSession'

import { LanguageSwitcher } from './LanguageSwitcher'
import { LogoutIcon, MenuIcon, UsersIcon } from './Icons'
import { Menu, MenuItem, MenuSection } from './Menu'
import { ThemeToggle } from './ThemeToggle'
import styles from './shell.module.css'

export interface TopBarProps {
  onOpenNav: () => void
}

/** Breadcrumb, deployment identity, theme, language and the account menu. */
export function TopBar({ onOpenNav }: TopBarProps) {
  const { t } = useTranslation(['console', 'common'])
  const [location, navigate] = useLocation()
  const session = useSession()
  const signOut = useSignOut()

  const item = navItemFor(location)
  const current = item ? t(`console:${item.labelKey}`) : t('console:page.notFound.title')
  const host = typeof window === 'undefined' ? '' : window.location.host

  return (
    <header className={styles.topbar}>
      <div className={styles.breadcrumb} aria-label={t('console:shell.breadcrumb')}>
        <button
          type="button"
          className={cn(styles.iconButton, styles.mobileNav)}
          onClick={onOpenNav}
          aria-label={t('common:nav.openMenu')}
        >
          <MenuIcon size={15} />
        </button>
        <span className="u-muted">{t('common:app.console')}</span>
        <span className="u-muted" aria-hidden="true">
          /
        </span>
        <span className={cn(styles.breadcrumbCurrent, 'u-truncate')}>{current}</span>
      </div>

      <div className={styles.topbarActions}>
        <span className={styles.envTag} title={t('console:shell.environment')}>
          {host}
        </span>
        <LanguageSwitcher />
        <ThemeToggle />
        <Menu
          label={t('console:shell.accountMenu')}
          trigger={<UsersIcon size={15} />}
        >
          <MenuSection
            title={
              session.data
                ? t('console:shell.signedInAs', { username: session.data.username })
                : t('console:shell.notSignedIn')
            }
          >
            {session.data ? (
              <MenuItem
                icon={<LogoutIcon size={13} />}
                onSelect={() => {
                  void signOut().then(() => {
                    navigate('/login')
                  })
                }}
              >
                {t('common:action.signOut')}
              </MenuItem>
            ) : (
              <MenuItem
                onSelect={() => {
                  navigate('/login')
                }}
              >
                {t('common:action.signIn')}
              </MenuItem>
            )}
          </MenuSection>
        </Menu>
      </div>
    </header>
  )
}
