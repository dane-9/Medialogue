import type { ButtonHTMLAttributes, ChangeEvent, CSSProperties, InputHTMLAttributes, PropsWithChildren, ReactNode, SelectHTMLAttributes } from 'react'
import { Children, isValidElement } from 'react'
import { Icon } from './Icon'
import { Dropdown } from './Dropdown'
import type { DropdownOption } from './Dropdown'

export function Button({ children, variant = 'secondary', icon, ...props }: PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; icon?: Parameters<typeof Icon>[0]['name'] }>) {
  return <button className={`button button-${variant}`} {...props}>{icon && <Icon name={icon} size={16} />}{children}</button>
}

export function Badge({ children, tone = 'neutral', className = '' }: PropsWithChildren<{ tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue' | 'purple'; className?: string }>) {
  return <span className={`badge badge-${tone} ${className}`}><span className="badge-dot" />{children}</span>
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) { return <input className="input" {...props} /> }
/**
 * A select that keeps its markup and loses its operating-system menu.
 *
 * Every call site still writes <option> children and reads event.target.value,
 * so nothing had to change to get a themed listbox: this reads those children
 * and hands them to Dropdown. Converting thirty-five call sites by hand would
 * have been thirty-five chances to change behaviour by accident.
 */
/**
 * A two-or-three way choice laid out as adjacent buttons.
 *
 * A dropdown hides every option but one behind a click, which is the wrong
 * trade when there are only two: the choice and its alternative both fit on
 * screen, so showing them costs nothing and reading them costs one glance.
 *
 * `tone` lets the selected button carry the meaning of what was picked rather
 * than a uniform accent, so a Movies root and a Shows root are distinguishable
 * at the same distance as their badges elsewhere.
 */
export function Segmented<T extends string>({ value, options, onChange, ariaLabel, disabled }: {
  value: T
  options: { value: T; label: string; tone?: 'movie' | 'show' }[]
  onChange: (value: T) => void
  ariaLabel?: string
  disabled?: boolean
}) {
  return <div className="segmented" role="group" aria-label={ariaLabel}>
    {options.map((option) => <button
      key={option.value}
      type="button"
      disabled={disabled}
      aria-pressed={option.value === value}
      data-tone={option.tone}
      className={`segmented-option ${option.value === value ? 'selected' : ''}`}
      onClick={() => onChange(option.value)}
    >{option.label}</button>)}
  </div>
}

/**
 * A range slider for a small bounded integer.
 *
 * The filled part of the track is painted with a gradient rather than a second
 * element, because a range input has no child of its own to size: everything
 * left of the thumb has to come from the background.
 */
export function Slider({ value, min, max, step = 1, onChange, ariaLabel, format, disabled }: {
  value: number
  min: number
  max: number
  step?: number
  onChange: (value: number) => void
  ariaLabel?: string
  format?: (value: number) => string
  disabled?: boolean
}) {
  const progress = max === min ? 0 : (value - min) / (max - min)
  return <div className="slider">
    <input
      type="range"
      className="slider-input"
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      aria-label={ariaLabel}
      style={{ '--slider-progress': `${progress * 100}%` } as CSSProperties}
      onChange={(event) => onChange(Number(event.target.value))}
    />
    <span className="slider-value">{format ? format(value) : value}</span>
  </div>
}

/**
 * The enabled state of a thing, as a button that says which state it is in.
 *
 * A switch shows its position and leaves you to infer the meaning from a label
 * beside it; this says the word and colours it, so the state survives a glance
 * with no second element to read. Red is used deliberately: a disabled client,
 * indexer or media folder is inert, and that should look like a stopped thing
 * rather than a neutral preference.
 */
export function EnabledButton({ enabled, onChange, disabled, labels }: {
  enabled: boolean
  onChange: (enabled: boolean) => void
  disabled?: boolean
  labels?: [on: string, off: string]
}) {
  const [on, off] = labels ?? ['Enabled', 'Disabled']
  return <button
    type="button"
    className={`enabled-button ${enabled ? 'on' : 'off'}`}
    aria-pressed={enabled}
    disabled={disabled}
    onClick={() => onChange(!enabled)}
  ><span className="badge-dot" />{enabled ? on : off}</button>
}

export function Select({ children, value, onChange, disabled, className = '', ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  const options: DropdownOption[] = []
  const collect = (nodes: ReactNode, group?: string) => {
    Children.forEach(nodes, (node) => {
      if (!isValidElement(node)) return
      if (node.type === 'optgroup') {
        const props = node.props as { label?: string; children?: ReactNode }
        collect(props.children, props.label)
        return
      }
      if (node.type !== 'option') return
      const props = node.props as { value?: string | number; disabled?: boolean; children?: ReactNode }
      options.push({
        value: String(props.value ?? ''),
        label: Children.toArray(props.children).join(''),
        disabled: props.disabled,
        group,
      })
    })
  }
  collect(children)

  // Keeps the `select` class on the trigger: layout rules across the app size
  // these by that selector, and losing it would silently collapse them.
  return <Dropdown
    className={`select ${className}`}
    value={String(value ?? '')}
    options={options}
    disabled={disabled}
    ariaLabel={rest['aria-label']}
    // Call sites read event.target.value, so hand them exactly that rather than
    // rewriting every handler.
    onChange={(next) => onChange?.({ target: { value: next } } as ChangeEvent<HTMLSelectElement>)}
  />
}

export function Panel({ title, eyebrow, action, children, className = '' }: PropsWithChildren<{ title?: string; eyebrow?: string; action?: React.ReactNode; className?: string }>) {
  return <section className={`panel ${className}`}>
    {(title || eyebrow || action) && <div className="panel-header"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}{title && <h2>{title}</h2>}</div>{action}</div>}
    {children}
  </section>
}

export function EmptyState({ icon = 'clapper', title, detail, action }: { icon?: Parameters<typeof Icon>[0]['name']; title: string; detail: string; action?: React.ReactNode }) {
  return <div className="empty-state"><div className="empty-icon"><Icon name={icon} size={25} /></div><h3>{title}</h3><p>{detail}</p>{action}</div>
}

export function Progress({ value, tone = 'blue' }: { value: number; tone?: 'blue' | 'green' | 'amber' }) { return <div className={`progress progress-${tone}`}><span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div> }

export function Stat({ label, value, detail, tone = 'default' }: { label: string; value: string; detail?: string; tone?: string }) { return <div className={`stat stat-${tone}`}><div className="stat-label">{label}</div><div className="stat-value">{value}</div>{detail && <div className="stat-detail">{detail}</div>}</div> }
