import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'
import { useElementSize } from '@/hooks/useElementSize'
import { useFormatters } from '@/hooks/useFormatters'

import { EmptyState } from './EmptyState'
import { ErrorState } from './ErrorState'
import { Skeleton } from './Skeleton'
import styles from './data.module.css'

export interface ChartPoint {
  /** ISO 8601 (UTC from the server) or epoch milliseconds. */
  ts: string | number
  value: number | null
}

export interface ChartSeries {
  id: string
  label: string
  points: ChartPoint[]
  /**
   * A colour token reference, e.g. 'var(--success)'. Chart colours must match
   * the status palette: success rate is --success, risk rate is --danger,
   * volume is --accent. Categorical comparisons fall back to --series-N.
   */
  color?: string
  area?: boolean
}

export interface TimeSeriesChartProps {
  series: ChartSeries[]
  height?: number
  loading?: boolean
  error?: unknown
  onRetry?: () => void
  /** Formats the y axis and the tooltip. Defaults to a locale-aware compact number. */
  formatValue?: (value: number) => string
  yMin?: number
  yMax?: number
  showLegend?: boolean
  emptyMessage?: ReactNode
  className?: string
}

const PADDING = { top: 8, right: 8, bottom: 20, left: 44 }
const DEFAULT_COLORS = [
  'var(--series-1)',
  'var(--series-2)',
  'var(--series-3)',
  'var(--series-4)',
  'var(--series-5)',
  'var(--series-6)',
]

function toMillis(ts: string | number): number {
  return typeof ts === 'number' ? ts : new Date(ts).getTime()
}

/**
 * SVG time series, no charting library.
 *
 * Grid lines stay quieter than the data, axis labels are muted, the tooltip is
 * mono, and the axis states which time zone is being shown - the server stores
 * UTC and the console converts, so both facts have to be visible
 * (docs/design/12-design-system.md).
 */
export function TimeSeriesChart({
  series,
  height = 200,
  loading = false,
  error,
  onRetry,
  formatValue,
  yMin,
  yMax,
  showLegend = true,
  emptyMessage,
  className,
}: TimeSeriesChartProps) {
  const { t } = useTranslation()
  const format = useFormatters()
  const [ref, size] = useElementSize<HTMLDivElement>()
  const [hiddenSeries, setHiddenSeries] = useState<ReadonlySet<string>>(() => new Set<string>())
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)

  const visible = useMemo(
    () => series.filter((entry) => !hiddenSeries.has(entry.id)),
    [series, hiddenSeries],
  )
  const width = Math.max(size.width, 240)
  const formatY = formatValue ?? ((value: number) => format.compact(value))

  const model = useMemo(() => {
    const timestamps = new Set<number>()
    for (const entry of series) {
      for (const point of entry.points) timestamps.add(toMillis(point.ts))
    }
    const axis = [...timestamps].sort((a, b) => a - b)

    const values: number[] = []
    for (const entry of visible) {
      for (const point of entry.points) {
        if (point.value !== null && Number.isFinite(point.value)) values.push(point.value)
      }
    }

    const min = yMin ?? (values.length > 0 ? Math.min(0, ...values) : 0)
    const max = yMax ?? (values.length > 0 ? Math.max(...values) : 1)
    return { axis, min, max: max === min ? min + 1 : max, hasData: values.length > 0 }
  }, [series, visible, yMin, yMax])

  if (error) return <ErrorState error={error} onRetry={onRetry} compact />
  if (loading) return <Skeleton height={height} radius="var(--radius)" />

  const plotWidth = Math.max(width - PADDING.left - PADDING.right, 10)
  const plotHeight = Math.max(height - PADDING.top - PADDING.bottom, 10)
  const spanX = Math.max((model.axis[model.axis.length - 1] ?? 0) - (model.axis[0] ?? 0), 1)
  const spanY = model.max - model.min

  const x = (ts: number): number => PADDING.left + ((ts - (model.axis[0] ?? 0)) / spanX) * plotWidth
  const y = (value: number): number =>
    PADDING.top + plotHeight - ((value - model.min) / spanY) * plotHeight

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
    ratio,
    value: model.min + spanY * (1 - ratio),
    y: PADDING.top + plotHeight * ratio,
  }))

  const xTicks = model.axis.length > 1 ? [0, 0.5, 1].map((ratio) => {
    const index = Math.round(ratio * (model.axis.length - 1))
    return model.axis[index] ?? model.axis[0] ?? 0
  }) : model.axis

  const paths = visible.map((entry, index) => {
    const segments: string[] = []
    let current: string[] = []
    for (const point of entry.points) {
      if (point.value === null || !Number.isFinite(point.value)) {
        // A gap is a gap: never join across missing data.
        if (current.length > 0) segments.push(current.join(' '))
        current = []
        continue
      }
      const command = current.length === 0 ? 'M' : 'L'
      current.push(`${command}${x(toMillis(point.ts)).toFixed(1)} ${y(point.value).toFixed(1)}`)
    }
    if (current.length > 0) segments.push(current.join(' '))

    return {
      id: entry.id,
      d: segments.join(' '),
      color: entry.color ?? DEFAULT_COLORS[index % DEFAULT_COLORS.length] ?? DEFAULT_COLORS[0],
    }
  })

  const hoverTs = hoverIndex !== null ? model.axis[hoverIndex] : undefined
  const zone = format.timeZone()

  return (
    <div className={cn(styles.chart, className)} ref={ref}>
      {showLegend && series.length > 1 ? (
        <div className={styles.chartLegend}>
          {series.map((entry, index) => {
            const off = hiddenSeries.has(entry.id)
            return (
              <button
                key={entry.id}
                type="button"
                className={cn(styles.legendItem, off && styles.legendItemOff)}
                aria-pressed={!off}
                title={t('chart.toggleSeries')}
                onClick={() => {
                  setHiddenSeries((current) => {
                    const next = new Set(current)
                    if (next.has(entry.id)) next.delete(entry.id)
                    else next.add(entry.id)
                    return next
                  })
                }}
              >
                <span
                  className={styles.legendSwatch}
                  style={{
                    background: entry.color ?? DEFAULT_COLORS[index % DEFAULT_COLORS.length],
                  }}
                />
                {entry.label}
              </button>
            )
          })}
        </div>
      ) : null}

      {!model.hasData ? (
        <EmptyState title={emptyMessage ?? t('chart.noData')} description="" />
      ) : (
        <>
          <svg
            className={styles.chartSvg}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={series.map((entry) => entry.label).join(', ')}
            onPointerMove={(event) => {
              const bounds = event.currentTarget.getBoundingClientRect()
              const ratio = (event.clientX - bounds.left - PADDING.left) / plotWidth
              const index = Math.round(ratio * (model.axis.length - 1))
              setHoverIndex(Math.min(Math.max(index, 0), model.axis.length - 1))
            }}
            onPointerLeave={() => {
              setHoverIndex(null)
            }}
          >
            {gridLines.map((line) => (
              <g key={line.ratio}>
                <line
                  className={styles.chartGrid}
                  x1={PADDING.left}
                  x2={width - PADDING.right}
                  y1={line.y}
                  y2={line.y}
                />
                <text className={styles.chartAxisLabel} x={0} y={line.y + 3}>
                  {formatY(line.value)}
                </text>
              </g>
            ))}

            {xTicks.map((ts, index) => (
              <text
                key={`${ts}-${index}`}
                className={styles.chartAxisLabel}
                x={x(ts)}
                y={height - 6}
                textAnchor={index === 0 ? 'start' : index === xTicks.length - 1 ? 'end' : 'middle'}
              >
                {format.time(ts)}
              </text>
            ))}

            {hoverTs !== undefined ? (
              <line
                className={styles.chartCursor}
                x1={x(hoverTs)}
                x2={x(hoverTs)}
                y1={PADDING.top}
                y2={PADDING.top + plotHeight}
              />
            ) : null}

            {paths.map((path) => (
              <path key={path.id} className={styles.chartLine} d={path.d} stroke={path.color} />
            ))}
          </svg>

          {hoverTs !== undefined ? (
            <div
              className={styles.tooltip}
              style={{
                left: Math.min(Math.max(x(hoverTs) - 60, 0), Math.max(width - 140, 0)),
                top: PADDING.top,
              }}
            >
              <div className={styles.tooltipTime}>{format.dateTime(hoverTs)}</div>
              {visible.map((entry, index) => {
                const point = entry.points.find((candidate) => toMillis(candidate.ts) === hoverTs)
                return (
                  <div key={entry.id} className={styles.tooltipRow}>
                    <span style={{ color: entry.color ?? DEFAULT_COLORS[index % DEFAULT_COLORS.length] }}>
                      {entry.label}
                    </span>
                    <span className={styles.tooltipValue}>
                      {point && point.value !== null ? formatY(point.value) : '—'}
                    </span>
                  </div>
                )
              })}
            </div>
          ) : null}

          <p className={styles.chartFooter}>{t('chart.timeZoneNote', { zone: zone.iana })}</p>
        </>
      )}
    </div>
  )
}
