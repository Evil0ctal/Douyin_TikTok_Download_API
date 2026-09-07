import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from './Button'
import { CodeBlock } from './CodeBlock'
import { EmptyState } from './EmptyState'

function BoundaryFallback({ error }: { error: Error }) {
  const { t } = useTranslation(['errors'])

  return (
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
}

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
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
    return <BoundaryFallback error={this.state.error} />
  }
}
