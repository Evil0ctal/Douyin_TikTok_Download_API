import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'
import { useCopy } from '@/hooks/useCopy'

import { CheckIcon, CopyIcon } from './Icons'
import styles from './data.module.css'

export interface CopyableIdProps {
  value: string | null | undefined
  /**
   * What this value is - "SHA-256", "Request id". Rendered in front of it, and
   * used as the button's accessible name.
   *
   * Worth passing wherever the surrounding text does not already say. A bare
   * 64-character hex string is not self-describing: it could be a sha256, a
   * sha1 doubled up or a key, and "which hash is this" is exactly the question
   * somebody verifying a file has.
   */
  label?: string
  /**
   * Let a long value wrap instead of ellipsising. For a sha256 in a column
   * narrower than 64 characters, which is most of them.
   */
  wrap?: boolean
  className?: string
}

/**
 * Ids are everywhere in this console - aweme_id, sec_user_id, task_id,
 * request_id, sha256 - and comparing two of them in a proportional face is
 * miserable. Mono, whole, one click to copy.
 *
 * Whole is the part worth defending. This used to cut every value to twelve
 * characters and put the rest in a tooltip, which is a strange trade: the only
 * thing anybody does with an id is compare it to another one, and a prefix
 * cannot settle that. There is no `length` prop to bring the cut back, because
 * a component that can truncate is one that will be asked to.
 *
 * Fitting is CSS's job instead. The text ellipsises at whatever width it
 * actually has, so a drawer shows the whole thing and a crowded column shows
 * as much as it can - the difference being that the second one gets wider when
 * the window does, which a fixed character count never did.
 */
export function CopyableId({ value, label, wrap = false, className }: CopyableIdProps) {
  const { t } = useTranslation()
  const { copied, copy } = useCopy()

  if (!value) return <span className="u-muted">—</span>

  return (
    <button
      type="button"
      className={cn(styles.copyable, wrap && styles.copyableWrap, className)}
      title={value}
      aria-label={label ? `${label}: ${value}` : `${t('id.copy')}: ${value}`}
      onClick={(event) => {
        event.stopPropagation()
        void copy(value)
      }}
    >
      {label ? (
        <span className={styles.copyableLabel} aria-hidden="true">
          {label}
        </span>
      ) : null}
      <span className={cn(styles.copyableText, wrap && styles.copyableTextWrap)}>{value}</span>
      <span className={cn(styles.copyableIcon, copied && styles.copyableCopied)}>
        {copied ? <CheckIcon size={11} /> : <CopyIcon size={11} />}
      </span>
    </button>
  )
}
