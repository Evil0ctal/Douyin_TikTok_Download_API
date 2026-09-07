import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'

import { cn } from '@/lib/cn'

import { SpinnerIcon } from './Icons'
import styles from './controls.module.css'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: ReactNode
  iconAfter?: ReactNode
  block?: boolean
}

/**
 * The loading state keeps the label in the layout and hides it, so a button
 * never changes width mid-click and shifts the row underneath the cursor.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading = false,
    icon,
    iconAfter,
    block = false,
    className,
    children,
    disabled,
    type = 'button',
    ...rest
  },
  ref,
) {
  const iconOnly = !children && (Boolean(icon) || Boolean(iconAfter))

  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        styles.button,
        styles[variant],
        styles[size],
        iconOnly && styles.iconOnly,
        block && styles.block,
        className,
      )}
      disabled={disabled ?? loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      <span className={cn(styles.inner, loading && styles.loadingLabel)}>
        {icon}
        {children}
        {iconAfter}
      </span>
      {loading ? (
        <span className={styles.spinnerOverlay}>
          <SpinnerIcon size={size === 'lg' ? 16 : 14} />
        </span>
      ) : null}
    </button>
  )
})
