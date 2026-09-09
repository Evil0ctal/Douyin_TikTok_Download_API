/**
 * Console navigation.
 *
 * Pages come from docs/design/07-frontend.md; the grouping is what an operator
 * reaches for during an incident, not the order the docs list them in.
 */

export const NAV_GROUPS = ['monitor', 'pool', 'tools', 'access', 'operations'] as const
export type NavGroup = (typeof NAV_GROUPS)[number]

export type NavIconName =
  | 'gauge'
  | 'idCard'
  | 'globe'
  | 'key'
  | 'terminal'
  | 'link'
  | 'book'
  | 'list'
  | 'sliders'
  | 'bell'
  | 'server'
  | 'activity'
  | 'archive'
  | 'download'
  | 'clock'
  | 'users'
  | 'lock'

export interface NavItem {
  path: string
  /** Key in the console namespace. */
  labelKey: string
  group: NavGroup
  icon: NavIconName
}

export const NAV_ITEMS: readonly NavItem[] = [
  { path: '/', labelKey: 'nav.overview', group: 'monitor', icon: 'gauge' },
  { path: '/identities', labelKey: 'nav.identities', group: 'pool', icon: 'idCard' },
  { path: '/proxies', labelKey: 'nav.proxies', group: 'pool', icon: 'globe' },
  { path: '/scheduler', labelKey: 'nav.scheduler', group: 'pool', icon: 'sliders' },
  { path: '/playground', labelKey: 'nav.playground', group: 'tools', icon: 'terminal' },
  { path: '/parse', labelKey: 'nav.parseTool', group: 'tools', icon: 'link' },
  { path: '/tools', labelKey: 'nav.tools', group: 'tools', icon: 'terminal' },
  { path: '/library', labelKey: 'nav.library', group: 'tools', icon: 'archive' },
  { path: '/watchlist', labelKey: 'nav.watchlist', group: 'tools', icon: 'clock' },
  { path: '/downloads', labelKey: 'nav.downloads', group: 'tools', icon: 'download' },
  { path: '/docs', labelKey: 'nav.apiDocs', group: 'tools', icon: 'book' },
  { path: '/api-keys', labelKey: 'nav.apiKeys', group: 'access', icon: 'key' },
  { path: '/endpoint-access', labelKey: 'nav.endpointAccess', group: 'access', icon: 'lock' },
  { path: '/users', labelKey: 'nav.users', group: 'access', icon: 'users' },
  { path: '/logs', labelKey: 'nav.logs', group: 'operations', icon: 'list' },
  { path: '/system', labelKey: 'nav.system', group: 'operations', icon: 'server' },
  { path: '/diagnose', labelKey: 'nav.diagnose', group: 'operations', icon: 'activity' },
  { path: '/backup', labelKey: 'nav.backup', group: 'operations', icon: 'archive' },
  { path: '/notifications', labelKey: 'nav.notifications', group: 'operations', icon: 'bell' },
  { path: '/settings', labelKey: 'nav.settings', group: 'operations', icon: 'sliders' },
]

/** Routes reachable without a session. */
export const PUBLIC_ROUTES: readonly string[] = ['/login', '/setup']

export function navItemFor(path: string): NavItem | undefined {
  if (path === '/') return NAV_ITEMS[0]
  return NAV_ITEMS.find((item) => item.path !== '/' && path.startsWith(item.path))
}
