import { type ReactNode } from 'react'

import { cn } from '@/lib/cn'

import styles from './surfaces.module.css'

export interface PageHeaderProps {
  title: ReactNode
  description?: ReactNode
  /** Status badge, environment tag or record count shown beside the title. */
  badge?: ReactNode
  actions?: ReactNode
  className?: string
}

export function PageHeader({ title, description, badge, actions, className }: PageHeaderProps) {
  return (
    <header className={cn(styles.pageHeader, className)}>
      <div style={{ minWidth: 0 }}>
        <div className={styles.pageTitleRow}>
          <h1 className={styles.pageTitle}>{title}</h1>
          {badge}
        </div>
        {description ? <p className={styles.pageDescription}>{description}</p> : null}
      </div>
      {actions ? <div className={styles.pageActions}>{actions}</div> : null}
    </header>
  )
}
