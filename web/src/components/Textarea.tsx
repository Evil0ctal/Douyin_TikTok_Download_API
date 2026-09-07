import { forwardRef, useId, type ReactNode, type TextareaHTMLAttributes } from 'react'

import { cn } from '@/lib/cn'

import { Field, describedBy } from './Field'
import styles from './controls.module.css'

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: ReactNode
  description?: ReactNode
  error?: ReactNode
  showOptional?: boolean
  fieldClassName?: string
}

/** Mono by default: this is where cookie blobs, proxy lists and JSON get pasted. */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, description, error, showOptional = false, fieldClassName, className, id, required, ...rest },
  ref,
) {
  const generatedId = useId()
  const textareaId = id ?? generatedId

  return (
    <Field
      id={textareaId}
      label={label}
      description={description}
      error={error}
      required={required}
      showOptional={showOptional}
      className={fieldClassName}
    >
      <textarea
        ref={ref}
        id={textareaId}
        className={cn(
          styles.control,
          styles.textarea,
          error && styles.controlInvalid,
          className,
        )}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(textareaId, Boolean(description), Boolean(error))}
        required={required}
        spellCheck={false}
        {...rest}
      />
    </Field>
  )
})
