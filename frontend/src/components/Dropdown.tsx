import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './Icon'
import { nextIndex, typeAheadIndex } from '../lib/dropdown'

export interface DropdownOption {
  value: string
  label: string
  disabled?: boolean

  group?: string
}

export function Dropdown({ value, options, onChange, disabled, ariaLabel, className = '' }: {
  value: string
  options: DropdownOption[]
  onChange: (value: string) => void
  disabled?: boolean
  ariaLabel?: string
  className?: string
}) {
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const typed = useRef({ query: '', at: 0 })
  const [position, setPosition] = useState({ left: 0, top: 0, width: 0 })

  const selected = options.findIndex((option) => option.value === value)
  const current = options[selected]

  const place = useCallback(() => {
    const trigger = triggerRef.current
    const menu = menuRef.current
    if (!trigger || !menu) return
    const rect = trigger.getBoundingClientRect()
    const height = menu.getBoundingClientRect().height
    const gap = 4
    const room = window.innerHeight - rect.bottom - gap
    const top = height > room && rect.top > height + gap ? rect.top - height - gap : rect.bottom + gap
    setPosition({ left: rect.left, top, width: rect.width })
  }, [])

  useLayoutEffect(() => { if (open) place() }, [open, place])

  useEffect(() => {
    if (!open) return
    const reposition = () => place()
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [open, place])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  const openMenu = () => {
    if (disabled) return
    // Nothing is highlighted until the user actually points or types: seeding
    // this with the selected index made the current value look permanently
    // hovered the instant the menu opened. Arrow keys still start from it.
    setActive(-1)
    setOpen(true)
  }

  const commit = (index: number) => {
    const option = options[index]
    if (!option || option.disabled) return
    onChange(option.value)
    setOpen(false)
    triggerRef.current?.focus()
  }

  const step = (from: number, delta: number) => {
    let index = from
    for (let attempt = 0; attempt < options.length; attempt += 1) {
      index = nextIndex(index, options.length, delta)
      if (!options[index]?.disabled) return index
    }
    return from
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (disabled) return
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
        event.preventDefault()
        openMenu()
      }
      return
    }
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); triggerRef.current?.focus(); return }
    if (event.key === 'Tab') { setOpen(false); return }
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); commit(active === -1 ? selected : active); return }
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive((index) => step(index === -1 ? selected : index, 1)); return }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActive((index) => step(index === -1 ? selected : index, -1)); return }
    if (event.key === 'Home') { event.preventDefault(); setActive(step(-1, 1)); return }
    if (event.key === 'End') { event.preventDefault(); setActive(step(0, -1)); return }

    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      const now = Date.now()
      typed.current.query = now - typed.current.at > 800 ? event.key : typed.current.query + event.key
      typed.current.at = now
      const found = typeAheadIndex(options.map((option) => option.label), typed.current.query, active)
      if (found >= 0 && !options[found].disabled) setActive(found)
    }
  }

  useEffect(() => {
    if (!open || active < 0) return
    menuRef.current?.querySelectorAll('[role="option"]')[active]?.scrollIntoView({ block: 'nearest' })
  }, [open, active])

  return <>
    <button
      ref={triggerRef}
      type="button"
      className={`dropdown-trigger ${open ? 'open' : ''} ${className}`}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => (open ? setOpen(false) : openMenu())}
      onKeyDown={onKeyDown}
    >
      <span>{current?.label ?? ''}</span>
      <Icon name="caret" size={14} />
    </button>
    {open && createPortal(
      <div
        ref={menuRef}
        className="dropdown-menu"
        role="listbox"
        aria-label={ariaLabel}
        style={{ left: position.left, top: position.top, minWidth: position.width }}
      >

        <div className="dropdown-list">
        {options.map((option, index) => <div key={`${option.value}-${index}`}>
          {option.group && (index === 0 || options[index - 1].group !== option.group) && (
            <div className="dropdown-group">{option.group}</div>
          )}
          <button
            type="button"
            role="option"
            aria-selected={option.value === value}
            aria-disabled={option.disabled}
            className={`dropdown-option ${index === active ? 'active' : ''} ${option.value === value ? 'selected' : ''}`}
            onMouseEnter={() => !option.disabled && setActive(index)}
            onClick={() => commit(index)}
          >
            {option.label}
          </button>
        </div>)}
        </div>
      </div>,
      document.body,
    )}
  </>
}
