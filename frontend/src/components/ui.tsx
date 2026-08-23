import type { ButtonHTMLAttributes, InputHTMLAttributes, PropsWithChildren, SelectHTMLAttributes } from 'react'
import { Icon } from './Icon'

export function Button({ children, variant = 'secondary', icon, ...props }: PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; icon?: Parameters<typeof Icon>[0]['name'] }>) {
  return <button className={`button button-${variant}`} {...props}>{icon && <Icon name={icon} size={16} />}{children}</button>
}

export function Badge({ children, tone = 'neutral', className = '' }: PropsWithChildren<{ tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue' | 'purple'; className?: string }>) {
  return <span className={`badge badge-${tone} ${className}`}><span className="badge-dot" />{children}</span>
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) { return <input className="input" {...props} /> }
export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) { return <select className="input select" {...props} /> }

export function Panel({ title, eyebrow, action, children, className = '' }: PropsWithChildren<{ title?: string; eyebrow?: string; action?: React.ReactNode; className?: string }>) {
  return <section className={`panel ${className}`}>
    {(title || eyebrow || action) && <div className="panel-header"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}{title && <h2>{title}</h2>}</div>{action}</div>}
    {children}
  </section>
}

export function EmptyState({ icon = 'film', title, detail, action }: { icon?: Parameters<typeof Icon>[0]['name']; title: string; detail: string; action?: React.ReactNode }) {
  return <div className="empty-state"><div className="empty-icon"><Icon name={icon} size={25} /></div><h3>{title}</h3><p>{detail}</p>{action}</div>
}

export function Progress({ value, tone = 'blue' }: { value: number; tone?: 'blue' | 'green' | 'amber' }) { return <div className={`progress progress-${tone}`}><span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div> }

export function Stat({ label, value, detail, tone = 'default' }: { label: string; value: string; detail?: string; tone?: string }) { return <div className={`stat stat-${tone}`}><div className="stat-label">{label}</div><div className="stat-value">{value}</div>{detail && <div className="stat-detail">{detail}</div>}</div> }
