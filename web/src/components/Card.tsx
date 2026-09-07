import { type ReactNode } from 'react'

import { cn } from '@/lib/cn'

import styles from './surfaces.module.css'

export interface CardProps {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  footer?: ReactNode
  /** Removes body padding, for tables that should meet the card border. */
  flush?: boolean
  className?: string
  bodyClassName?: string
  children?: ReactNode
}

/** On dark, a card separates from the page by background lightness and a border, not a shadow. */
export function Card({
  title,
  description,
  actions,
  footer,
  flush = false,
  className,
  bodyClassName,
  children,
}: CardProps) {
  const hasHeader = Boolean(title || description || actions)

  return (
    <section className={cn(styles.card, className)}>
      {hasHeader ? (
        <header className={styles.cardHeader}>
          <div style={{ minWidth: 0 }}>
            {title ? <h2 className={styles.cardTitle}>{title}</h2> : null}
            {description ? <p className={styles.cardDescription}>{description}</p> : null}
          </div>
          {actions ? <div className={styles.cardActions}>{actions}</div> : null}
        </header>
      ) : null}
      {children !== undefined ? (
        <div className={cn(styles.cardBody, flush && styles.cardBodyFlush, bodyClassName)}>
          {children}
        </div>
      ) : null}
      {footer ? <footer className={styles.cardFooter}>{footer}</footer> : null}
    </section>
  )
}
