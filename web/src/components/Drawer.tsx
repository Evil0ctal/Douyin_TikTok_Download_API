import { useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'
import { useFocusTrap } from '@/hooks/useFocusTrap'
import { useScrollLock } from '@/hooks/useScrollLock'

import { CrossIcon } from './Icons'
import styles from './overlay.module.css'

export interface DrawerProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  description?: ReactNode
  footer?: ReactNode
  /** 'end' slides in from the right (details); 'start' is the mobile navigation. */
  side?: 'start' | 'end'
  children: ReactNode
  className?: string
}

/** Side panel for row details and, under 768px, the navigation itself. */
export function Drawer({
  open,
  onClose,
  title,
  description,
  footer,
  side = 'end',
  children,
  className,
}: DrawerProps) {
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

  return createPortal(
    <div
      className={cn(styles.backdrop, styles.backdropDrawer)}
      style={side === 'start' ? { justifyContent: 'flex-start' } : undefined}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        className={cn(styles.modal, styles.drawer, side === 'start' && styles.drawerStart, className)}
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
        <div className={styles.overlayBody}>{children}</div>
        {footer ? <footer className={styles.overlayFooter}>{footer}</footer> : null}
      </div>
    </div>,
    document.body,
  )
}
