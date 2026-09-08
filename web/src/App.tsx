import { lazy, Suspense, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { QueryClientProvider } from '@tanstack/react-query'
import { Redirect, Route, Switch, useLocation } from 'wouter'

import {
  Button,
  Drawer,
  ErrorBoundary,
  ErrorState,
  LanguageSwitcher,
  PageHeader,
  Sidebar,
  Skeleton,
  ThemeToggle,
  ToastProvider,
  TopBar,
} from '@/components'
import styles from '@/components/shell.module.css'
import { setUnauthenticatedHandler } from '@/lib/api'
import { cn } from '@/lib/cn'
import { createQueryClient } from '@/lib/query'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { useLocalStorageState } from '@/hooks/useLocalStorageState'
import { useSetupStatus } from '@/hooks/useSetupStatus'

// Every page is code-split: the console is served from the api container, and a
// single bundle would make the first paint wait for the Swagger page.
const Setup = lazy(() => import('@/pages/Setup'))
const Login = lazy(() => import('@/pages/Login'))
const Overview = lazy(() => import('@/pages/Overview'))
const Identities = lazy(() => import('@/pages/Identities'))
const Proxies = lazy(() => import('@/pages/Proxies'))
const ApiKeys = lazy(() => import('@/pages/ApiKeys'))
const EndpointAccess = lazy(() => import('@/pages/EndpointAccess'))
const Tools = lazy(() => import('@/pages/Tools'))
const Downloads = lazy(() => import('@/pages/Downloads'))
const Playground = lazy(() => import('@/pages/Playground'))
const ParseTool = lazy(() => import('@/pages/ParseTool'))
const Logs = lazy(() => import('@/pages/Logs'))
const Settings = lazy(() => import('@/pages/Settings'))
const Notifications = lazy(() => import('@/pages/Notifications'))
const System = lazy(() => import('@/pages/System'))
const Diagnose = lazy(() => import('@/pages/Diagnose'))
const Backup = lazy(() => import('@/pages/Backup'))
const Users = lazy(() => import('@/pages/Users'))
const ApiDocs = lazy(() => import('@/pages/ApiDocs'))

const queryClient = createQueryClient()

function PageFallback() {
  return (
    <div className="u-stack">
      <Skeleton width={220} height={24} />
      <Skeleton width={420} height={14} />
      <Skeleton height={220} radius="var(--radius-lg)" />
    </div>
  )
}

function NotFound() {
  const { t } = useTranslation('console')
  return (
    <>
      <PageHeader title={t('page.notFound.title')} description={t('page.notFound.description')} />
      <Button
        variant="secondary"
        onClick={() => {
          window.location.assign('/')
        }}
      >
        {t('page.notFound.action')}
      </Button>
    </>
  )
}

function ConsoleRoutes() {
  return (
    <Suspense fallback={<PageFallback />}>
      <Switch>
        <Route path="/" component={Overview} />
        <Route path="/identities" component={Identities} />
        <Route path="/proxies" component={Proxies} />
        <Route path="/api-keys" component={ApiKeys} />
        <Route path="/endpoint-access" component={EndpointAccess} />
        <Route path="/playground" component={Playground} />
        <Route path="/parse" component={ParseTool} />
        <Route path="/tools" component={Tools} />
        <Route path="/downloads" component={Downloads} />
        <Route path="/logs" component={Logs} />
        <Route path="/settings" component={Settings} />
        <Route path="/notifications" component={Notifications} />
        <Route path="/system" component={System} />
        <Route path="/diagnose" component={Diagnose} />
        <Route path="/backup" component={Backup} />
        <Route path="/users" component={Users} />
        <Route path="/docs" component={ApiDocs} />
        <Route component={NotFound} />
      </Switch>
    </Suspense>
  )
}

/** 240px sidebar (56px collapsed), sticky top bar, 1440px content column. */
function Shell() {
  const { t } = useTranslation('common')
  const isMobile = useIsMobile()
  const [collapsed, setCollapsed] = useLocalStorageState('sidebar.collapsed', false)
  const [navOpen, setNavOpen] = useState(false)
  const [location] = useLocation()

  // A route change closes the mobile drawer; otherwise it covers the page it
  // just navigated to.
  useEffect(() => {
    setNavOpen(false)
  }, [location])

  return (
    <div className={cn(styles.shell, collapsed && styles.shellCollapsed)}>
      <a className={styles.skipLink} href="#main">
        {t('nav.skipToContent')}
      </a>

      <Sidebar
        collapsed={collapsed}
        onToggleCollapse={() => {
          setCollapsed((value) => !value)
        }}
      />

      {isMobile ? (
        <Drawer
          open={navOpen}
          onClose={() => {
            setNavOpen(false)
          }}
          side="start"
          title={t('app.console')}
        >
          <Sidebar
            embedded
            onNavigate={() => {
              setNavOpen(false)
            }}
          />
        </Drawer>
      ) : null}

      <div className={styles.main}>
        <TopBar
          onOpenNav={() => {
            setNavOpen(true)
          }}
        />
        <main className={styles.content} id="main">
          <ErrorBoundary>
            <ConsoleRoutes />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}

/**
 * Setup gate.
 *
 * While the instance has no administrator, every route redirects to the wizard
 * (docs/design/06-api-auth-mcp.md). If the status call itself fails we fail open
 * rather than bricking the console: a transport problem should not lock an
 * operator out of the pages that would explain it.
 */
function Gate() {
  const { t } = useTranslation('common')
  const [location, navigate] = useLocation()
  const status = useSetupStatus()

  useEffect(() => {
    setUnauthenticatedHandler(() => {
      if (location !== '/login' && location !== '/setup') navigate('/login')
    })
    return () => {
      setUnauthenticatedHandler(null)
    }
  }, [location, navigate])

  if (status.isLoading) {
    return (
      <div className={styles.centered}>
        <div className={cn(styles.centeredPanel, 'u-stack')}>
          <Skeleton height={20} width={160} />
          <Skeleton height={120} radius="var(--radius-lg)" />
        </div>
      </div>
    )
  }

  if (status.isError && status.error.kind === 'network') {
    // No shell renders behind this, so the switchers come along: an operator who
    // cannot reach the API is stuck on this panel, and it is the last place to
    // strand them in a language they do not read.
    return (
      <div className={styles.centered}>
        <div className={cn(styles.centeredPanel, 'u-stack')}>
          <div className="u-row-between">
            <span className="u-mono u-secondary">{t('app.name')}</span>
            <span className="u-row">
              <LanguageSwitcher />
              <ThemeToggle />
            </span>
          </div>
          <ErrorState
            error={status.error}
            onRetry={() => {
              void status.refetch()
            }}
          />
        </div>
      </div>
    )
  }

  const initialized = status.data?.initialized ?? true

  if (!initialized && location !== '/setup') return <Redirect to="/setup" />

  return (
    <Switch>
      <Route path="/setup">
        <Suspense fallback={null}>
          <Setup />
        </Suspense>
      </Route>
      <Route path="/login">
        <Suspense fallback={null}>
          <Login />
        </Suspense>
      </Route>
      <Route>
        <Shell />
      </Route>
    </Switch>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ErrorBoundary fullScreen>
          <Gate />
        </ErrorBoundary>
      </ToastProvider>
    </QueryClientProvider>
  )
}
