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

  return <Dropdown
    className={`select ${className}`}
    value={String(value ?? '')}
    options={options}
    disabled={disabled}
    ariaLabel={rest['aria-label']}
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
