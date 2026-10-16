import type { ReactNode } from 'react'

import styles from './CompactProgress.module.css'

export interface CompactProgressProps {
  value: number
  max: number
  label: ReactNode
  title?: string
}

/** A compact table-cell progress indicator with a human-readable value. */
export function CompactProgress({ value, max, label, title }: CompactProgressProps) {
  const safeMax = Number.isFinite(max) && max > 0 ? max : 0
  const safeValue = Number.isFinite(value) ? Math.max(0, Math.min(value, safeMax)) : 0
  const percentage = safeMax > 0 ? Math.round((safeValue / safeMax) * 100) : 0

  return (
    <span className={styles.progress} title={title}>
      <span
        className={styles.track}
        role="progressbar"
        aria-valuenow={safeValue}
        aria-valuemin={0}
        aria-valuemax={safeMax}
      >
        <span className={styles.fill} style={{ width: `${percentage}%` }} />
      </span>
      <span className="u-xs u-muted u-mono">{label}</span>
    </span>
  )
}
