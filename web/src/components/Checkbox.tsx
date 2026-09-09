import { forwardRef, useEffect, useRef, type InputHTMLAttributes, type ReactNode } from 'react'

import { cn } from '@/lib/cn'

import { CheckIcon, MinusIcon } from './Icons'
import styles from './controls.module.css'

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: ReactNode
  hint?: ReactNode
  indeterminate?: boolean
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, hint, indeterminate = false, className, ...rest },
  forwardedRef,
) {
  const localRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (localRef.current) localRef.current.indeterminate = indeterminate
  }, [indeterminate])

  return (
    <label className={cn(styles.checkboxRow, className)}>
      <input
        ref={(node) => {
          localRef.current = node
          if (typeof forwardedRef === 'function') forwardedRef(node)
          else if (forwardedRef) forwardedRef.current = node
        }}
        type="checkbox"
        {...rest}
      />
      <span className={styles.checkboxBox} aria-hidden="true">
        {indeterminate ? <MinusIcon size={11} /> : <CheckIcon size={11} />}
      </span>
      {label || hint ? (
        <span className={styles.checkboxText}>
          {label ? <span className={styles.checkboxLabel}>{label}</span> : null}
          {hint ? <span className={cn(styles.hint, styles.description)}>{hint}</span> : null}
        </span>
      ) : null}
    </label>
  )
})
