import { type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { isApiError, type ApiError } from '@/lib/api'
import { cn } from '@/lib/cn'

import { Button } from './Button'
import { AlertIcon, RefreshIcon } from './Icons'
import { CopyableId } from './CopyableId'
import { ErrorCodeBadge } from './StatusBadge'
import styles from './surfaces.module.css'

export interface ErrorInfo {
  title: string
  message: string
  hint: string | null
  error: ApiError | null
  requestId: string | null
}

/**
 * Turns any thrown value into something a human can act on.
 *
 * A failure must explain itself: the stable code, a localized message, what to
 * do about it and the request id to quote in an issue (docs/design/07-frontend.md).
 */
export function useErrorInfo(error: unknown): ErrorInfo {
  const { t } = useTranslation('errors')

  if (!isApiError(error)) {
    const message = error instanceof Error ? error.message : t('generic.unknown')
    return { title: t('title'), message, hint: null, error: null, requestId: null }
  }

  if (error.kind !== 'api') {
    const key =
      error.kind === 'network'
        ? 'generic.network'
        : error.kind === 'timeout'
          ? 'generic.timeout'
          : error.kind === 'aborted'
            ? 'generic.aborted'
            : 'generic.malformed'
    return {
      title: t('title'),
      message: t(key),
      hint: null,
      error,
      requestId: error.requestId,
    }
  }

  return {
    // The server already localized the message; the bundled copy is the fallback
    // for when it did not reach us.
    title: t('title'),
    message: error.message || t(`code.${error.code}`),
    hint: t(`hint.${error.code}`),
    error,
    requestId: error.requestId,
  }
}

export interface ErrorStateProps {
  error: unknown
  onRetry?: () => void
  title?: ReactNode
  className?: string
  compact?: boolean
}

export function ErrorState({ error, onRetry, title, className, compact = false }: ErrorStateProps) {
  const { t } = useTranslation(['errors', 'common'])
  const info = useErrorInfo(error)

  return (
    <div className={cn(styles.state, className)} role="alert">
      <span className={cn(styles.stateIcon, styles.stateIconDanger)}>
        <AlertIcon size={16} />
      </span>
      <p className={styles.stateTitle}>{title ?? info.title}</p>
      <p className={styles.stateDescription}>{info.message}</p>
      {!compact && info.hint ? (
        <p className={styles.stateDescription} style={{ color: 'var(--text-muted)' }}>
          {info.hint}
        </p>
      ) : null}
      <div className={styles.stateMeta}>
        {info.error && info.error.kind === 'api' ? <ErrorCodeBadge code={info.error.code} /> : null}
        {info.requestId ? (
          <span>
            {t('errors:label.requestId')} <CopyableId value={info.requestId} length={10} />
          </span>
        ) : null}
      </div>
      {onRetry ? (
        <div className={styles.stateActions}>
          <Button variant="secondary" size="sm" icon={<RefreshIcon />} onClick={onRetry}>
            {t('common:action.retry')}
          </Button>
        </div>
      ) : null}
    </div>
  )
}
