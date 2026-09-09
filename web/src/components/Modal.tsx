import { Children, useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'
import { useFocusTrap } from '@/hooks/useFocusTrap'
import { useScrollLock } from '@/hooks/useScrollLock'

import { CrossIcon } from './Icons'
import styles from './overlay.module.css'

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl'

export interface ModalProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  description?: ReactNode
  footer?: ReactNode
  size?: ModalSize
  /** Set false for a destructive flow that should not close by accident. */
  closeOnBackdrop?: boolean
  children: ReactNode
  className?: string
}

const SIZE_CLASS: Record<ModalSize, string | undefined> = {
  sm: styles.modalSm,
  md: undefined,
  lg: styles.modalLg,
  xl: styles.modalXl,
}

export function Modal({
  open,
  onClose,
  title,
  description,
  footer,
  size = 'md',
  closeOnBackdrop = true,
  children,
  className,
}: ModalProps) {
  const { t } = useTranslation()
  const panelRef = useRef<HTMLDivElement | null>(null)
  const titleId = useId()

  useFocusTrap(panelRef, open)
  useScrollLock(open)

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open, onClose])

  if (!open) return null

  // `children` is routinely an array of conditionals - {a}{b} where both are
  // null - so truthiness is not the question; whether anything survives
  // rendering is.
  const hasBody = Children.toArray(children).length > 0

  return createPortal(
    <div
      className={styles.backdrop}
      onMouseDown={(event) => {
        if (closeOnBackdrop && event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        className={cn(styles.modal, SIZE_CLASS[size], className)}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className={styles.overlayHeader}>
          <div style={{ minWidth: 0 }}>
            <h2 className={styles.overlayTitle} id={titleId}>
              {title}
            </h2>
            {description ? <p className={styles.overlayDescription}>{description}</p> : null}
          </div>
          <button
            type="button"
            className={styles.closeButton}
            aria-label={t('dialog.close')}
            onClick={onClose}
          >
            <CrossIcon />
          </button>
        </header>
        {/* A dialog with nothing to say renders no body at all. The header
            draws a rule under itself and the footer draws one over itself, so
            an empty padded div between them reads as two stray lines with a
            gap - which is exactly what a confirm with only a title and a
            description looked like. */}
        {hasBody ? <div className={styles.overlayBody}>{children}</div> : null}
        {footer ? <footer className={styles.overlayFooter}>{footer}</footer> : null}
      </div>
    </div>,
    document.body,
  )
}
