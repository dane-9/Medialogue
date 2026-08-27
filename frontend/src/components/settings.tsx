import { useEffect, useState } from 'react'
import type { InputHTMLAttributes, PropsWithChildren, ReactNode } from 'react'
import { Icon } from './Icon'
import { Badge, Button } from './ui'

export function Field({ label, help, badge, error, wide, children }: PropsWithChildren<{
  label: string
  help?: ReactNode
  badge?: string
  error?: string
  wide?: boolean
}>) {
  return <div className={`field ${wide ? 'field-wide' : ''}`}>
    <div className="field-head"><span>{label}</span>{badge && <span className="field-optional">{badge}</span>}</div>
    {help && <div className="field-help">{help}</div>}
    {children}
    {error && <div className="field-error"><Icon name="alert" size={13} />{error}</div>}
  </div>
}

export function Secret({ ...props }: InputHTMLAttributes<HTMLInputElement>) {
  const [shown, setShown] = useState(false)
  return <div className="secret">
    <input className="input" type={shown ? 'text' : 'password'} autoComplete="new-password" {...props} />
    <button type="button" onClick={() => setShown((value) => !value)} aria-label={shown ? 'Hide value' : 'Reveal value'}>
      <Icon name={shown ? 'eye-off' : 'eye'} size={15} />
    </button>
  </div>
}

export type StatusTone = 'green' | 'amber' | 'red' | 'neutral'

export function SectionHead({ title, description, status, statusTone = 'neutral', detail, detailTone, action, autosave, divided }: {
  title: string
  description: ReactNode
  status?: string
  statusTone?: StatusTone
  detail?: string
  detailTone?: 'ok' | 'err'
  action?: ReactNode
  autosave?: boolean
  divided?: boolean
}) {
  return <div className={`section-head ${divided ? 'section-head-split' : ''}`}>
    <div className="section-head-copy"><h3>{title}</h3><p>{description}</p></div>
    {(status || detail) && <div className="section-head-status">
      {status && <Badge tone={statusTone}>{status}</Badge>}
      {detail && <span className={`status-detail ${detailTone ?? ''}`}>{detail}</span>}
    </div>}
    {autosave && <span className="autosave-tag"><Icon name="check" size={13} />Applies immediately</span>}
    {action}
  </div>
}

export type Message = { tone: 'ok' | 'error' | 'busy'; text: string } | null

const DISMISS_AFTER: Record<string, number> = { ok: 4000, error: 12000 }

export function Note({ message, dismissAfter }: { message: Message; dismissAfter?: number }) {
  const [expired, setExpired] = useState(false)
  const tone = message?.tone
  const text = message?.text

  useEffect(() => {
    setExpired(false)
    if (!tone || tone === 'busy') return
    const after = dismissAfter ?? DISMISS_AFTER[tone] ?? 4000
    const timer = window.setTimeout(() => setExpired(true), after)
    return () => window.clearTimeout(timer)
  }, [tone, text, dismissAfter])

  if (!message || expired) return null
  const icon = message.tone === 'error' ? 'alert' : message.tone === 'ok' ? 'check' : 'clock'
  const cls = message.tone === 'error' ? 'error-note' : message.tone === 'ok' ? 'note-ok' : ''
  return <div className={`settings-note ${cls}`}><Icon name={icon} size={15} /><span>{message.text}</span></div>
}

export const ok = (text: string): Message => ({ tone: 'ok', text })
export const failed = (reason: unknown, fallback: string): Message => ({ tone: 'error', text: reason instanceof Error ? reason.message : fallback })
export const pending = (text: string): Message => ({ tone: 'busy', text })

export function SaveFooter({ dirty, saving, onSave, onRevert, children }: PropsWithChildren<{
  dirty: boolean
  saving?: boolean
  onSave: () => void
  onRevert?: () => void
}>) {
  return <div className="settings-footer">
    {dirty && <span className="footer-state">Unsaved changes</span>}
    {children}
    {onRevert && <Button variant="ghost" onClick={onRevert} disabled={!dirty || saving}>Revert</Button>}
    <Button variant="primary" onClick={onSave} disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save changes'}</Button>
  </div>
}
