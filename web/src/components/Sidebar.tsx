import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'wouter'

import { cn } from '@/lib/cn'
import { NAV_GROUPS, NAV_ITEMS, type NavIconName } from '@/lib/nav'

import {
  ActivityIcon,
  ArchiveIcon,
  BellIcon,
  BookIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  GaugeIcon,
  GlobeIcon,
  IdCardIcon,
  KeyIcon,
  LinkIcon,
  ListIcon,
  ServerIcon,
  LockIcon,
  SlidersIcon,
  TerminalIcon,
  UsersIcon,
  type IconProps,
} from './Icons'
import styles from './shell.module.css'

const NAV_ICONS: Record<NavIconName, (props: IconProps) => ReactElement> = {
  gauge: GaugeIcon,
  idCard: IdCardIcon,
  globe: GlobeIcon,
  key: KeyIcon,
  terminal: TerminalIcon,
  link: LinkIcon,
  book: BookIcon,
  list: ListIcon,
  sliders: SlidersIcon,
  lock: LockIcon,
  bell: BellIcon,
  server: ServerIcon,
  activity: ActivityIcon,
  archive: ArchiveIcon,
  users: UsersIcon,
}

export interface SidebarProps {
  collapsed?: boolean
  onToggleCollapse?: () => void
  /** Called after a navigation, so the mobile drawer can close itself. */
  onNavigate?: () => void
  /** Inside the mobile drawer the brand row and collapse control are redundant. */
  embedded?: boolean
  className?: string
}

export function isActivePath(current: string, target: string): boolean {
  if (target === '/') return current === '/'
  return current === target || current.startsWith(`${target}/`)
}

/** 240px, collapsible to icons, and a drawer under 768px. */
export function Sidebar({
  collapsed = false,
  onToggleCollapse,
  onNavigate,
  embedded = false,
  className,
}: SidebarProps) {
  const { t } = useTranslation(['console', 'common'])
  const [location] = useLocation()

  return (
    <nav
      className={cn(!embedded && styles.sidebar, collapsed && styles.collapsed, className)}
      aria-label={t('common:nav.primary')}
    >
      {!embedded ? (
        <div className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">
            d
          </span>
          <span className={styles.brandName}>{t('common:app.name')}</span>
        </div>
      ) : null}

      <div className={styles.navScroll}>
        {NAV_GROUPS.map((group) => {
          const items = NAV_ITEMS.filter((item) => item.group === group)
          if (items.length === 0) return null
          return (
            <div key={group} className={styles.navGroup}>
              <p className={styles.navGroupTitle}>{t(`console:nav.group.${group}`)}</p>
              {items.map((item) => {
                const Icon = NAV_ICONS[item.icon]
                const active = isActivePath(location, item.path)
                const label = t(`console:${item.labelKey}`)
                return (
                  <Link
                    key={item.path}
                    href={item.path}
                    className={cn(styles.navLink, active && styles.navLinkActive)}
                    aria-current={active ? 'page' : undefined}
                    title={collapsed ? label : undefined}
                    onClick={onNavigate}
                  >
                    <span className={styles.navIcon}>
                      <Icon size={14} />
                    </span>
                    <span className={cn(styles.navLabel, 'u-truncate')}>{label}</span>
                  </Link>
                )
              })}
            </div>
          )
        })}
      </div>

      {!embedded && onToggleCollapse ? (
        <div className={styles.sidebarFooter}>
          <button type="button" className={styles.collapseButton} onClick={onToggleCollapse}>
            {collapsed ? <ChevronRightIcon size={13} /> : <ChevronLeftIcon size={13} />}
            <span className={styles.collapseLabel}>
              {collapsed ? t('common:nav.expand') : t('common:nav.collapse')}
            </span>
          </button>
        </div>
      ) : null}
    </nav>
  )
}
