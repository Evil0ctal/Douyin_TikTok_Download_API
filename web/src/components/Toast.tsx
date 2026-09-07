import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { isApiError } from '@/lib/api'
import { cn } from '@/lib/cn'

import { AlertIcon, CheckIcon, CrossIcon } from './Icons'
import { CopyableId } from './CopyableId'
import { ErrorCodeBadge } from './StatusBadge'
import styles from './overlay.module.css'

export type ToastTone = 'success' | 'warning' | 'error' | 'info'

export interface ToastOptions {
  description?: ReactNode
  /** Milliseconds. Ignored for errors, which never auto-dismiss. */
  durationMs?: number
  action?: ReactNode
}

interface ToastRecord extends ToastOptions {
  id: string
  tone: ToastTone
  title: ReactNode
  meta?: ReactNode
}

export interface ToastApi {
  show: (tone: ToastTone, title: ReactNode, options?: ToastOptions) => string
  success: (title: ReactNode, options?: ToastOptions) => string
  warning: (title: ReactNode, options?: ToastOptions) => string
  info: (title: ReactNode, options?: ToastOptions) => string
  error: (title: ReactNode, options?: ToastOptions) => string
  /** Renders a failed request with its code, message and request id. */
  apiError: (error: unknown, title?: ReactNode) => string
  dismiss: (id: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const DEFAULT_DURATION: Record<ToastTone, number | null> = {
  success: 4_000,
  info: 5_000,
  warning: 7_000,
  // Failures stay until dismissed: a toast that vanishes takes the request id
  // with it, and that id is what a bug report needs.
  error: null,
}

const TONE_CLASS: Record<ToastTone, string | undefined> = {
  success: styles.toastSuccess,
  warning: styles.toastWarning,
  error: styles.toastError,
  info: styles.toastInfo,
}

const ICON_CLASS: Record<ToastTone, string | undefined> = {
  success: styles.toastIconSuccess,
  warning: styles.toastIconWarning,
  error: styles.toastIconError,
  info: styles.toastIconInfo,
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation(['common', 'errors'])
  const [toasts, setToasts] = useState<ToastRecord[]>([])
  const timers = useRef(new Map<string, number>())

  const dismiss = useCallback((id: string) => {
    const timer = timers.current.get(id)
    if (timer) {
      window.clearTimeout(timer)
      timers.current.delete(id)
    }
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (record: Omit<ToastRecord, 'id'>): string => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      setToasts((current) => [...current, { ...record, id }])

      const duration = record.durationMs ?? DEFAULT_DURATION[record.tone]
      if (duration !== null && duration !== undefined) {
        timers.current.set(
          id,
          window.setTimeout(() => {
            dismiss(id)
          }, duration),
        )
      }
      return id
    },
    [dismiss],
  )

  const api = useMemo<ToastApi>(() => {
    const show = (tone: ToastTone, title: ReactNode, options?: ToastOptions): string =>
      push({ tone, title, ...options })

    return {
      show,
      success: (title, options) => show('success', title, options),
      warning: (title, options) => show('warning', title, options),
      info: (title, options) => show('info', title, options),
      error: (title, options) => show('error', title, options),
      apiError: (error, title) => {
        if (!isApiError(error)) {
          const message = error instanceof Error ? error.message : t('errors:generic.unknown')
          return show('error', title ?? t('errors:title'), { description: message })
        }

        const description =
          error.kind === 'network'
            ? t('errors:generic.network')
            : error.kind === 'timeout'
              ? t('errors:generic.timeout')
              : error.kind === 'malformed'
                ? t('errors:generic.malformed')
                : error.message || t(`errors:code.${error.code}`)

        return push({
          tone: 'error',
          title: title ?? t('errors:title'),
          description,
          meta: (
            <>
              {error.kind === 'api' ? <ErrorCodeBadge code={error.code} /> : null}
              {error.requestId ? <CopyableId value={error.requestId} length={10} /> : null}
            </>
          ),
        })
      },
      dismiss,
    }
  }, [push, dismiss, t])

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        <div className={styles.toastRegion} role="region" aria-label={t('common:toast.region')}>
          {toasts.map((toast) => (
            <div
              key={toast.id}
              className={cn(styles.toast, TONE_CLASS[toast.tone])}
              role={toast.tone === 'error' ? 'alert' : 'status'}
              aria-live={toast.tone === 'error' ? 'assertive' : 'polite'}
            >
              <span className={cn(styles.toastIcon, ICON_CLASS[toast.tone])}>
                {toast.tone === 'success' ? <CheckIcon /> : <AlertIcon />}
              </span>
              <div className={styles.toastBody}>
                <div className={styles.toastTitle}>{toast.title}</div>
                {toast.description ? (
                  <div className={styles.toastDescription}>{toast.description}</div>
                ) : null}
                {toast.meta || toast.action ? (
                  <div className={styles.toastMeta}>
                    {toast.meta}
                    {toast.action}
                  </div>
                ) : null}
              </div>
              <button
                type="button"
                className={styles.closeButton}
                aria-label={t('common:toast.dismiss')}
                onClick={() => {
                  dismiss(toast.id)
                }}
              >
                <CrossIcon size={12} />
              </button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside ToastProvider')
  return context
}
