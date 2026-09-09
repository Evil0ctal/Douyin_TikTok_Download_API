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
  ClockIcon,
  DownloadIcon,
  GaugeIcon,
  GlobeIcon,
  IdCardIcon,
  KeyIcon,
  LinkIcon,
  ListIcon,
  ServerIcon,
  LockIcon,
  PlugIcon,
  SlidersIcon,
  TerminalIcon,
  UsersIcon,
  type IconProps,
  InfoIcon,
} from './Icons'
import { Logo } from './Logo'
import { Sponsor } from './Sponsor'
import styles from './shell.module.css'

const NAV_ICONS: Record<NavIconName, (props: IconProps) => ReactElement> = {
  gauge: GaugeIcon,
  idCard: IdCardIcon,
  globe: GlobeIcon,
  key: KeyIcon,
  terminal: TerminalIcon,
  plug: PlugIcon,
  link: LinkIcon,
  book: BookIcon,
  list: ListIcon,
  sliders: SlidersIcon,
  lock: LockIcon,
  bell: BellIcon,
  server: ServerIcon,
  activity: ActivityIcon,
  archive: ArchiveIcon,
  download: DownloadIcon,
  clock: ClockIcon,
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
            <Logo size={24} />
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

      {/*
        Pinned under the scrolling navigation, in this order for a reason.
        About is a page in this console and reads as one more nav row; the
        sponsor is an outbound link to somebody else, and it used to sit
        directly above About with four pixels between them, so the two read as
        one block - an advert with a menu item stuck to it.

        The rule on the wrapper is load bearing. The navigation scrolls, so
        without it whatever the scroll happened to cut off runs straight into
        this: measured with 214px of nav still below the fold, the first pinned
        row sat under the "operations" heading and looked like part of it.
      */}
      {!embedded ? (
        <div className={styles.pinned}>
          {/* Copyright and the licence, from every page. An Apache-2.0 project
              that never says so anywhere in its own interface is asking people
              to go and find out. */}
          <Link
            href="/about"
            className={cn(styles.about, location === '/about' && styles.aboutActive)}
            title={collapsed ? t('common:nav.about') : undefined}
            onClick={onNavigate}
          >
            <span className={styles.navIcon}>
              <InfoIcon size={14} />
            </span>
            <span className={cn(styles.navLabel, 'u-truncate')}>{t('common:nav.about')}</span>
          </Link>

          {/* Sponsors keep this free, so they are visible from every page
              rather than only from the page about them. Collapsed, the sidebar
              is icons and this would be a logo with no room to say what it is,
              so it goes with the labels. */}
          {!collapsed ? <Sponsor variant="compact" /> : null}
        </div>
      ) : null}

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
