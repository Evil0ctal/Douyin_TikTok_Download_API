import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react'

import { cn } from '@/lib/cn'

import { Field, describedBy } from './Field'
import styles from './controls.module.css'

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: ReactNode
  description?: ReactNode
  error?: ReactNode
  /** Ids, tokens and hosts are easier to compare in a mono face. */
  mono?: boolean
  showOptional?: boolean
  fieldClassName?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    label,
    description,
    error,
    mono = false,
    showOptional = false,
    fieldClassName,
    className,
    id,
    required,
    ...rest
  },
  ref,
) {
  const generatedId = useId()
  const inputId = id ?? generatedId

  return (
    <Field
      id={inputId}
      label={label}
      description={description}
      error={error}
      required={required}
      showOptional={showOptional}
      className={fieldClassName}
    >
      <input
        ref={ref}
        id={inputId}
        className={cn(styles.control, mono && styles.mono, error && styles.controlInvalid, className)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(inputId, Boolean(description), Boolean(error))}
        required={required}
        {...rest}
      />
    </Field>
  )
})
