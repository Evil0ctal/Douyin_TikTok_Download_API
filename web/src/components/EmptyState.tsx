import { type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'

import { MinusIcon } from './Icons'
import styles from './surfaces.module.css'

export interface EmptyStateProps {
  title?: ReactNode
  description?: ReactNode
  icon?: ReactNode
  action?: ReactNode
  className?: string
}

/** Empty is a first-class state here: an empty grid with no words looks broken. */
export function EmptyState({ title, description, icon, action, className }: EmptyStateProps) {
  const { t } = useTranslation()

  return (
    <div className={cn(styles.state, className)}>
      <span className={styles.stateIcon}>{icon ?? <MinusIcon size={16} />}</span>
      <p className={styles.stateTitle}>{title ?? t('empty.title')}</p>
      <p className={styles.stateDescription}>{description ?? t('empty.description')}</p>
      {action ? <div className={styles.stateActions}>{action}</div> : null}
    </div>
  )
}
