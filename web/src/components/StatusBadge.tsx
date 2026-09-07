import { useTranslation } from 'react-i18next'

import type { ErrorCode } from '@/lib/api'
import { cn } from '@/lib/cn'
import { errorTone, statusMeta, type StatusKind, type StatusMeta, type Tone } from '@/lib/status'
import { useValueChangeFlash } from '@/hooks/useValueChangeFlash'

import { STATUS_ICONS } from './Icons'
import styles from './data.module.css'

const TONE_CLASS: Record<Tone, string> = {
  success: styles.toneSuccess ?? '',
  warning: styles.toneWarning ?? '',
  caution: styles.toneCaution ?? '',
  danger: styles.toneDanger ?? '',
  neutral: styles.toneNeutral ?? '',
  muted: styles.toneMuted ?? '',
  accent: styles.toneAccent ?? '',
}

export function toneClass(tone: Tone): string {
  return TONE_CLASS[tone]
}

export interface StatusBadgeProps {
  kind: StatusKind
  value: string | null | undefined
  /** Also print the raw enum value in mono. Useful in log tables. */
  showValue?: boolean
  size?: 'sm' | 'md'
  /** Highlight once when the status actually changes. Never on a plain refresh. */
  flash?: boolean
  className?: string
  title?: string
}

/**
 * Colour plus icon plus text, always all three.
 *
 * Users paste these tables into chat, where only the text survives; they screenshot
 * them into issues, where colour can shift. The label is the payload, the colour
 * is the accelerator (docs/design/12-design-system.md).
 */
export function StatusBadge({
  kind,
  value,
  showValue = false,
  size = 'md',
  flash = true,
  className,
  title,
}: StatusBadgeProps) {
  const { t } = useTranslation()
  const meta: StatusMeta = statusMeta(kind, value)
  const Icon = STATUS_ICONS[meta.icon]
  const flashing = useValueChangeFlash(value)

  return (
    <span
      className={cn(
        styles.badge,
        toneClass(meta.tone),
        size === 'sm' && styles.badgeSm,
        flash && flashing && 'u-flash',
        className,
      )}
      title={title}
    >
      <span className={styles.badgeIcon}>
        <Icon size={11} />
      </span>
      <span>{t(meta.labelKey)}</span>
      {showValue && value ? <span className={styles.badgeValue}>{value}</span> : null}
    </span>
  )
}

export interface ErrorCodeBadgeProps {
  code: ErrorCode | string | null | undefined
  className?: string
}

/**
 * Error codes are machine identifiers and are never translated, so the badge
 * prints the code itself in mono and carries only the tone.
 */
export function ErrorCodeBadge({ code, className }: ErrorCodeBadgeProps) {
  if (!code) return null
  const tone = errorTone(code as ErrorCode)

  return (
    <span className={cn(styles.badge, toneClass(tone), className)}>
      <span className={styles.badgeIcon}>
        <span aria-hidden="true">·</span>
      </span>
      <span style={{ fontFamily: 'var(--font-mono)' }}>{code}</span>
    </span>
  )
}
