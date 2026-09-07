import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

import { cn } from '@/lib/cn'
import { useOnClickOutside } from '@/hooks/useOnClickOutside'

import styles from './overlay.module.css'

/** Lets a MenuItem dismiss the panel it lives in without prop drilling. */
const MenuCloseContext = createContext<(() => void) | null>(null)

export interface MenuProps {
  /** Rendered inside a button that toggles the panel. */
  trigger: ReactNode
  children: ReactNode
  label: string
  align?: 'start' | 'end'
  role?: 'menu' | 'group'
  className?: string
  panelClassName?: string
  triggerClassName?: string
}

/**
 * Small popover used for the column picker, the language switcher and the
 * account menu. Closes on Esc and on a click outside, and returns focus to the
 * trigger, so the console stays keyboard-complete.
 */
export function Menu({
  trigger,
  children,
  label,
  align = 'end',
  role = 'menu',
  className,
  panelClassName,
  triggerClassName,
}: MenuProps) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  useOnClickOutside(
    containerRef,
    () => {
      setOpen(false)
    },
    open,
  )

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return
      setOpen(false)
      triggerRef.current?.focus()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className={cn(styles.menuRoot, className)} ref={containerRef}>
      <button
        ref={triggerRef}
        type="button"
        className={cn(styles.menuTrigger, triggerClassName)}
        aria-haspopup={role === 'menu' ? 'menu' : 'dialog'}
        aria-expanded={open}
        aria-label={label}
        onClick={() => {
          setOpen((value) => !value)
        }}
      >
        {trigger}
      </button>
      {open ? (
        <div
          className={cn(styles.menuPanel, align === 'start' && styles.menuPanelStart, panelClassName)}
          role={role}
          aria-label={label}
        >
          <MenuCloseContext.Provider
            value={() => {
              setOpen(false)
              triggerRef.current?.focus()
            }}
          >
            {children}
          </MenuCloseContext.Provider>
        </div>
      ) : null}
    </div>
  )
}

export interface MenuItemProps {
  onSelect?: () => void
  selected?: boolean
  icon?: ReactNode
  danger?: boolean
  /** Keep the panel open after selecting; for multi-choice panels. */
  keepOpen?: boolean
  children: ReactNode
  className?: string
}

export function MenuItem({
  onSelect,
  selected,
  icon,
  danger,
  keepOpen = false,
  children,
  className,
}: MenuItemProps) {
  const close = useContext(MenuCloseContext)

  return (
    <button
      type="button"
      role="menuitem"
      aria-current={selected || undefined}
      className={cn(styles.menuItem, danger && styles.menuItemDanger, selected && styles.menuItemSelected, className)}
      onClick={() => {
        onSelect?.()
        // Picking an option dismisses the panel; a menu that lingers over the
        // content it just changed hides the result of the action.
        if (!keepOpen) close?.()
      }}
    >
      {icon ? <span className={styles.menuItemIcon}>{icon}</span> : null}
      <span className="u-truncate">{children}</span>
    </button>
  )
}

export function MenuSection({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <div className={styles.menuSection}>
      {title ? <p className={styles.menuSectionTitle}>{title}</p> : null}
      {children}
    </div>
  )
}
