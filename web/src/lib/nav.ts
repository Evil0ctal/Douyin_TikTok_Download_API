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
  | 'plug'
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
  /**
   * Shown to the public demo account.
   *
   * Absent means no, which is the only safe default: a page added later is
   * hidden from a demo instance until somebody decides otherwise, rather than
   * being published to strangers because nobody remembered this field.
   *
   * The flag hides a link; it does not protect anything. The API refuses these
   * pages' endpoints for a demo caller on its own - see `demo_read` and
   * `read_admin_demo` in the backend - and this exists so a demo visitor sees a
   * console that works instead of a sidebar two thirds of which answers 403.
   */
  demo?: true
}

export const NAV_ITEMS: readonly NavItem[] = [
  { path: '/', labelKey: 'nav.overview', group: 'monitor', icon: 'gauge', demo: true },
  { path: '/identities', labelKey: 'nav.identities', group: 'pool', icon: 'idCard' },
  { path: '/proxies', labelKey: 'nav.proxies', group: 'pool', icon: 'globe' },
  { path: '/scheduler', labelKey: 'nav.scheduler', group: 'pool', icon: 'sliders', demo: true },
  { path: '/playground', labelKey: 'nav.playground', group: 'tools', icon: 'terminal', demo: true },
  { path: '/tools', labelKey: 'nav.tools', group: 'tools', icon: 'terminal', demo: true },
  { path: '/library', labelKey: 'nav.library', group: 'tools', icon: 'archive', demo: true },
  { path: '/watchlist', labelKey: 'nav.watchlist', group: 'tools', icon: 'clock' },
  { path: '/downloads', labelKey: 'nav.downloads', group: 'tools', icon: 'download', demo: true },
  { path: '/docs', labelKey: 'nav.apiDocs', group: 'tools', icon: 'book', demo: true },
  // Not '/mcp': that path is the MCP endpoint itself, mounted on the API.
  { path: '/mcp-guide', labelKey: 'nav.mcp', group: 'tools', icon: 'plug', demo: true },
  { path: '/api-keys', labelKey: 'nav.apiKeys', group: 'access', icon: 'key', demo: true },
  { path: '/endpoint-access', labelKey: 'nav.endpointAccess', group: 'access', icon: 'lock' },
  { path: '/users', labelKey: 'nav.users', group: 'access', icon: 'users' },
  { path: '/logs', labelKey: 'nav.logs', group: 'operations', icon: 'list', demo: true },
  { path: '/system', labelKey: 'nav.system', group: 'operations', icon: 'server', demo: true },
  { path: '/diagnose', labelKey: 'nav.diagnose', group: 'operations', icon: 'activity' },
  { path: '/backup', labelKey: 'nav.backup', group: 'operations', icon: 'archive' },
  { path: '/notifications', labelKey: 'nav.notifications', group: 'operations', icon: 'bell' },
  { path: '/settings', labelKey: 'nav.settings', group: 'operations', icon: 'sliders' },
]

/**
 * The navigation a role may see.
 *
 * Only the demo account is filtered. Every other role reaches every page and is
 * refused per endpoint if it lacks the rank, which is the behaviour the console
 * has always had: an operator seeing a Users link and being told no is a useful
 * thing to learn about their own instance. A stranger on a public demo learns
 * nothing from a link that always fails, so theirs is trimmed instead.
 */
export function navItemsFor(role: string | null | undefined): readonly NavItem[] {
  return role === 'demo' ? NAV_ITEMS.filter((item) => item.demo) : NAV_ITEMS
}

/** Whether a role may open a page at all, for a directly typed URL. */
export function canOpen(role: string | null | undefined, path: string): boolean {
  if (role !== 'demo') return true
  if (PUBLIC_ROUTES.includes(path) || path === '/about') return true
  const item = navItemFor(path)
  return item?.demo === true
}

/** Routes reachable without a session. */
export const PUBLIC_ROUTES: readonly string[] = ['/login', '/setup']

/**
 * Pages that are real but not in the navigation groups.
 *
 * About is the colophon: it has its own link pinned under the nav rather than
 * a place in a group, and the breadcrumb still has to be able to name it -
 * without this it resolved to "page not found" on a page that had just
 * rendered.
 */
const UNLISTED: readonly NavItem[] = [
  { path: '/about', labelKey: 'page.about.title', group: 'operations', icon: 'server' },
]

export function navItemFor(path: string): NavItem | undefined {
  if (path === '/') return NAV_ITEMS[0]
  return [...NAV_ITEMS, ...UNLISTED].find(
    (item) => item.path !== '/' && path.startsWith(item.path),
  )
}
