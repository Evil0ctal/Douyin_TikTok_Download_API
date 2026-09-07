import { type ReactNode } from 'react'

import { cn } from '@/lib/cn'
import { useTranslation } from 'react-i18next'

import { AlertIcon } from './Icons'
import styles from './controls.module.css'

export interface FieldProps {
  id: string
  label?: ReactNode
  description?: ReactNode
  error?: ReactNode
  required?: boolean
  showOptional?: boolean
  className?: string
  children: ReactNode
}

export function describedBy(id: string, hasDescription: boolean, hasError: boolean): string | undefined {
  const ids = [hasDescription ? `${id}-description` : null, hasError ? `${id}-error` : null].filter(
    Boolean,
  )
  return ids.length > 0 ? ids.join(' ') : undefined
}

/**
 * Label, help text and error message for one control.
 *
 * The error is always text wired through aria-describedby; a red border on its
 * own tells a screen reader nothing and a colour-blind user very little.
 */
export function Field({
  id,
  label,
  description,
  error,
  required = false,
  showOptional = false,
  className,
  children,
}: FieldProps) {
  const { t } = useTranslation()

  return (
    <div className={cn(styles.field, className)}>
      {label ? (
        <label className={styles.label} htmlFor={id}>
          {label}
          {required ? (
            <span className={styles.required} aria-label={t('field.required')}>
              *
            </span>
          ) : null}
          {showOptional && !required ? (
            <span className={styles.optional}>{t('field.optional')}</span>
          ) : null}
        </label>
      ) : null}
      {children}
      {description ? (
        <p className={styles.description} id={`${id}-description`}>
          {description}
        </p>
      ) : null}
      {error ? (
        <p className={styles.error} id={`${id}-error`} role="alert">
          <AlertIcon size={12} />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  )
}
