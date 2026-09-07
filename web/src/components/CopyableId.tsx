import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'
import { useCopy } from '@/hooks/useCopy'

import { CheckIcon, CopyIcon } from './Icons'
import styles from './data.module.css'

export interface CopyableIdProps {
  value: string | null | undefined
  /** Characters kept before truncating. 19-digit ids need at least 8 to be distinguishable. */
  length?: number
  /** Truncate in the middle, keeping both ends. Better for uuids. */
  middle?: boolean
  label?: string
  className?: string
}

function truncate(value: string, length: number, middle: boolean): string {
  if (value.length <= length) return value
  if (!middle) return `${value.slice(0, length)}…`
  const head = Math.ceil((length - 1) / 2)
  const tail = Math.floor((length - 1) / 2)
  return `${value.slice(0, head)}…${value.slice(value.length - tail)}`
}

/**
 * Ids are everywhere in this console - aweme_id, sec_user_id, task_id,
 * request_id - and comparing two 19-digit numbers in a proportional face is
 * miserable. Mono, truncated, full value on hover, one click to copy.
 */
export function CopyableId({ value, length = 12, middle = false, label, className }: CopyableIdProps) {
  const { t } = useTranslation()
  const { copied, copy } = useCopy()

  if (!value) return <span className="u-muted">—</span>

  const shown = truncate(value, length, middle)

  return (
    <button
      type="button"
      className={cn(styles.copyable, className)}
      title={value}
      aria-label={label ?? `${t('id.copy')}: ${value}`}
      onClick={(event) => {
        event.stopPropagation()
        void copy(value)
      }}
    >
      <span className={styles.copyableText}>{shown}</span>
      <span className={cn(styles.copyableIcon, copied && styles.copyableCopied)}>
        {copied ? <CheckIcon size={11} /> : <CopyIcon size={11} />}
      </span>
    </button>
  )
}
