import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { i18next, initI18n } from './lib/i18n'
import { initLanguage } from './lib/language'
import { navItemFor } from './lib/nav'
import { initTheme } from './lib/theme'
import './styles/index.css'

// Wouter patches pushState/replaceState to dispatch these, which is the only way
// to see a client-side navigation from outside React: it exports hooks, not a
// subscribe function, and the title has no component to hang off.
const LOCATION_EVENTS = ['popstate', 'pushState', 'replaceState'] as const

function pageTitleKey(pathname: string): string {
  // The two routes the shell never renders, so they are not in NAV_ITEMS.
  if (pathname === '/login') return 'console:page.login.title'
  if (pathname === '/setup') return 'console:page.setup.title'
  const item = navItemFor(pathname)
  return item ? `console:${item.labelKey}` : 'console:page.notFound.title'
}

function applyDocumentTitle(): void {
  document.title = i18next.t('app.documentTitle', {
    ns: 'common',
    page: i18next.t(pageTitleKey(window.location.pathname)),
  })
}

/**
 * Keep the tab's title on the page the user is actually looking at.
 *
 * The title is the only label a bookmark, a browser-history entry or a window
 * switcher gets, and a console pinned in a tab strip is otherwise fifteen
 * identical tabs. It follows the language too: i18next's own event rather than
 * subscribeLanguage, because changeLanguage resolves asynchronously and a
 * language listener would render the title from the outgoing catalogue.
 */
function initDocumentTitle(): void {
  applyDocumentTitle()
  i18next.on('languageChanged', applyDocumentTitle)
  for (const event of LOCATION_EVENTS) window.addEventListener(event, applyDocumentTitle)
}

initTheme()
initLanguage()
initI18n()
initDocumentTitle()

const container = document.getElementById('root')
if (!container) throw new Error('missing #root element')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
