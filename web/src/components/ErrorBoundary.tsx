import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'

import { Button } from './Button'
import { CodeBlock } from './CodeBlock'
import { EmptyState } from './EmptyState'
import { LanguageSwitcher } from './LanguageSwitcher'
import { ThemeToggle } from './ThemeToggle'
import shell from './shell.module.css'

/**
 * Translating a crash screen is safe here: i18n is a module singleton loaded
 * from bundled catalogues before the first render (lib/i18n.ts), so
 * useTranslation resolves without a provider above it and never suspends. That
 * matters more than usual - a fallback that throws while rendering takes the
 * whole tree down and leaves a blank page with no way back.
 */
function BoundaryFallback({ error, fullScreen }: { error: Error; fullScreen: boolean }) {
  const { t } = useTranslation(['errors', 'common'])

  const report = (
    <EmptyState
      title={t('errors:boundary.title')}
      description={t('errors:boundary.description')}
      action={
        <div className="u-stack-sm" style={{ width: '100%' }}>
          <Button
            variant="secondary"
            onClick={() => {
              window.location.reload()
            }}
          >
            {t('errors:boundary.action')}
          </Button>
          <CodeBlock
            title={t('errors:boundary.details')}
            language="text"
            code={`${error.name}: ${error.message}`}
            collapsible={false}
          />
        </div>
      }
    />
  )

  if (!fullScreen) return report

  // Reading the crash is the second problem; reading it in a language one does
  // not speak is the first. Nothing else is on screen to switch from.
  return (
    <div className={shell.centered}>
      <div className={cn(shell.centeredPanel, 'u-stack')}>
        <div className="u-row-between">
          <span className="u-mono u-secondary">{t('common:app.name')}</span>
          <span className="u-row">
            <LanguageSwitcher />
            <ThemeToggle />
          </span>
        </div>
        {report}
      </div>
    </div>
  )
}

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
  /** Set where the boundary owns the viewport and no top bar survives it. */
  fullScreen?: boolean
}

interface ErrorBoundaryState {
  error: Error | null
}

/**
 * Keeps one broken page from blanking the whole console. A render crash is not
 * an API failure, so this is deliberately separate from ErrorState.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // English only, and structured enough to grep for.
    console.error('console.render_error', error, info.componentStack)
  }

  override render(): ReactNode {
    if (!this.state.error) return this.props.children
    if (this.props.fallback) return this.props.fallback
    return <BoundaryFallback error={this.state.error} fullScreen={this.props.fullScreen ?? false} />
  }
}
