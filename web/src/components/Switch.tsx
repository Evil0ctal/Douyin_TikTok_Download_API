import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react'

import { cn } from '@/lib/cn'

import styles from './controls.module.css'

export interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: ReactNode
  hint?: ReactNode
}

/** A checkbox with switch semantics; used for feature flags on the settings page. */
export const Switch = forwardRef<HTMLInputElement, SwitchProps>(function Switch(
  { label, hint, className, ...rest },
  ref,
) {
  return (
    <label className={cn(styles.checkboxRow, className)}>
      <input ref={ref} type="checkbox" role="switch" {...rest} />
      <span className={styles.switchTrack} aria-hidden="true">
        <span className={styles.switchThumb} />
      </span>
      {/* Stacked, as a checkbox's are. A bare span ran the hint straight on
          from the label, so "Guests first" and the sentence explaining it read
          as one run-on line on every switch that had a hint. */}
      {label || hint ? (
        <span className={styles.checkboxText}>
          {label ? <span className={styles.checkboxLabel}>{label}</span> : null}
          {hint ? <span className={styles.description}>{hint}</span> : null}
        </span>
      ) : null}
    </label>
  )
})
