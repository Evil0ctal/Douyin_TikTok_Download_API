import { useId, useState, type ReactNode } from 'react'

import { cn } from '@/lib/cn'

import { ChevronDownIcon } from './Icons'
import styles from './layout.module.css'

export interface DisclosureProps {
  /** Header text. Truncates rather than wrapping, so a long name cannot reflow the row. */
  title: ReactNode
  /** Right-aligned slot in the header row: a badge, a count, a timestamp. */
  meta?: ReactNode
  /** Initial state when uncontrolled. Ignored once `open` is passed. */
  defaultOpen?: boolean
  /** Controlled state. Pass with onOpenChange to drive a set of these from one parent. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
  /** Draws the section's own border and radius. Leave off inside a Card, which already has one. */
  bordered?: boolean
  /** Removes body padding, for a table or a code block that should meet the edge. */
  flush?: boolean
  className?: string
  bodyClassName?: string
  children: ReactNode
}

/**
 * A collapsible section.
 *
 * The body is hidden with the hidden attribute, not a max-height clamp or an
 * opacity: a closed section leaves the accessibility tree, the tab order and
 * find-in-page entirely. The clamp inside CodeBlock is the opposite case on
 * purpose - it is a scroll container whose content stays reachable - and reusing
 * it here would leave a dozen "closed" request panels still tabbable.
 *
 * The header is a real button so Enter and Space toggle it for free; a div with
 * an onClick would need both keys wired by hand and would still be invisible to
 * a screen reader's forms mode. The meta slot is a sibling of that button rather
 * than a child, because the first thing a caller wants in it is a Button or a
 * copy affordance, and interactive content nested in a button is invalid.
 */
export function Disclosure({
  title,
  meta,
  defaultOpen = false,
  open,
  onOpenChange,
  bordered = false,
  flush = false,
  className,
  bodyClassName,
  children,
}: DisclosureProps) {
  const id = useId()
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen)
  const expanded = open ?? uncontrolledOpen

  const toggle = (): void => {
    const next = !expanded
    if (open === undefined) setUncontrolledOpen(next)
    onOpenChange?.(next)
  }

  return (
    <div className={cn(styles.disclosure, bordered && styles.disclosureBordered, className)}>
      <div className={styles.disclosureHeader}>
        <button
          type="button"
          id={`${id}-trigger`}
          className={styles.disclosureTrigger}
          aria-expanded={expanded}
          aria-controls={`${id}-body`}
          onClick={toggle}
        >
          <span className={cn(styles.disclosureChevron, expanded && styles.disclosureChevronOpen)}>
            <ChevronDownIcon />
          </span>
          <span className={styles.disclosureTitle}>{title}</span>
        </button>
        {meta ? <div className={styles.disclosureMeta}>{meta}</div> : null}
      </div>
      <div
        id={`${id}-body`}
        role="region"
        aria-labelledby={`${id}-trigger`}
        className={cn(styles.disclosureBody, flush && styles.disclosureBodyFlush, bodyClassName)}
        hidden={!expanded}
      >
        {children}
      </div>
    </div>
  )
}
