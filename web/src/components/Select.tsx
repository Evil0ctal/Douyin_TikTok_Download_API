import { forwardRef, useId, type ReactNode, type SelectHTMLAttributes } from 'react'

import { cn } from '@/lib/cn'

import { Field, describedBy } from './Field'
import { ChevronDownIcon } from './Icons'
import styles from './controls.module.css'

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: ReactNode
  description?: ReactNode
  error?: ReactNode
  options?: SelectOption[]
  showOptional?: boolean
  fieldClassName?: string
}

/**
 * A native select, styled with tokens. Native means correct keyboard behaviour
 * and a usable picker on phones for free - which matters, because alerts arrive
 * at night and the phone is what is at hand.
 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  {
    label,
    description,
    error,
    options,
    showOptional = false,
    fieldClassName,
    className,
    id,
    required,
    children,
    ...rest
  },
  ref,
) {
  const generatedId = useId()
  const selectId = id ?? generatedId

  return (
    <Field
      id={selectId}
      label={label}
      description={description}
      error={error}
      required={required}
      showOptional={showOptional}
      className={fieldClassName}
    >
      <div className={styles.selectWrap}>
        <select
          ref={ref}
          id={selectId}
          className={cn(
            styles.control,
            styles.select,
            error && styles.controlInvalid,
            className,
          )}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(selectId, Boolean(description), Boolean(error))}
          required={required}
          {...rest}
        >
          {options
            ? options.map((option) => (
                <option key={option.value} value={option.value} disabled={option.disabled}>
                  {option.label}
                </option>
              ))
            : children}
        </select>
        <ChevronDownIcon className={styles.selectChevron} />
      </div>
    </Field>
  )
})
