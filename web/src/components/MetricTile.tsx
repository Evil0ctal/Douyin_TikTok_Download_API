import { useMemo, type ReactNode } from 'react'

import { cn } from '@/lib/cn'
import type { Tone } from '@/lib/status'

import { ArrowDownIcon, ArrowUpIcon, MinusIcon } from './Icons'
import { Skeleton } from './Skeleton'
import styles from './surfaces.module.css'

export interface MetricTileProps {
  label: ReactNode
  value: ReactNode
  unit?: ReactNode
  /** Period-over-period change as a ratio; the sign picks the arrow and colour. */
  delta?: number | null
  /** Set when a rising number is bad (risk rate, failures). */
  invertDelta?: boolean
  deltaLabel?: ReactNode
  /** Raw values for the sparkline; nulls are treated as gaps. */
  spark?: Array<number | null>
  tone?: Tone
  loading?: boolean
  footer?: ReactNode
  className?: string
}

const SPARK_WIDTH = 96
const SPARK_HEIGHT = 22

function sparkPath(values: Array<number | null>): string | null {
  const points = values.map((value, index) => ({ value, index })).filter((point) => point.value !== null)
  if (points.length < 2) return null

  const numbers = points.map((point) => point.value as number)
  const min = Math.min(...numbers)
  const max = Math.max(...numbers)
  const span = max - min || 1
  const stepX = SPARK_WIDTH / Math.max(values.length - 1, 1)

  return points
    .map((point, order) => {
      const x = point.index * stepX
      const y = SPARK_HEIGHT - ((point.value as number) - min) / span * SPARK_HEIGHT
      return `${order === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' ')
}

/** Big number, unit, change and a mini trend line. */
export function MetricTile({
  label,
  value,
  unit,
  delta,
  invertDelta = false,
  deltaLabel,
  spark,
  tone,
  loading = false,
  footer,
  className,
}: MetricTileProps) {
  const path = useMemo(() => (spark ? sparkPath(spark) : null), [spark])

  const direction = delta === null || delta === undefined || delta === 0 ? 'flat' : delta > 0 ? 'up' : 'down'
  const good = invertDelta ? direction === 'down' : direction === 'up'
  const deltaClass =
    direction === 'flat' ? styles.deltaFlat : good ? styles.deltaUp : styles.deltaDown

  return (
    <div className={cn(styles.tile, className)}>
      <span className={styles.tileLabel}>{label}</span>
      <div className={styles.tileValueRow}>
        {loading ? (
          <Skeleton width={72} height={28} />
        ) : (
          <span
            className={styles.tileValue}
            style={tone ? { color: `var(--${tone === 'muted' ? 'text-muted' : tone})` } : undefined}
          >
            {value}
          </span>
        )}
        {unit ? <span className={styles.tileUnit}>{unit}</span> : null}
      </div>
      <div className={styles.tileFooter}>
        {delta !== undefined && delta !== null ? (
          <span className={cn(styles.tileDelta, deltaClass)}>
            {direction === 'up' ? (
              <ArrowUpIcon size={11} />
            ) : direction === 'down' ? (
              <ArrowDownIcon size={11} />
            ) : (
              <MinusIcon size={11} />
            )}
            {deltaLabel}
          </span>
        ) : (
          <span />
        )}
        {path ? (
          <svg
            className={styles.tileSpark}
            width={SPARK_WIDTH}
            height={SPARK_HEIGHT}
            viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
            aria-hidden="true"
            focusable="false"
          >
            <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5} />
          </svg>
        ) : null}
      </div>
      {footer}
    </div>
  )
}
