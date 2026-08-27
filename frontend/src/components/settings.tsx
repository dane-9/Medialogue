import { useEffect, useState } from 'react'
import type { InputHTMLAttributes, PropsWithChildren, ReactNode } from 'react'
import { Icon } from './Icon'
import { Badge, Button } from './ui'

/**
 * A labelled control with one line explaining what it actually does.
 *
 * Settings options are not guessable from their names, "Category" and "Scope"
 * in particular mean something different here than they do in comparable
 * tools, so help is permanent text rather than a tooltip you have to discover.
 */
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

/** A password field you can reveal, so a mistyped key is catchable before saving. */
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

/**
 * One header shape for every settings panel. Replaces the three separate
 * variants (integration-hero, qbit-editor-head, storage-head) that all did
 * this job differently. A connection test writes its result into `detail`,
 * beside the thing that was tested, rather than into a shared message line.
 */
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

/**
 * Panel feedback carries a tone. Previously every message, saved, saving and
 * failed alike, rendered in the same neutral box, so success and failure were
 * indistinguishable at a glance.
 */
export type Message = { tone: 'ok' | 'error' | 'busy'; text: string } | null

/**
 * A one-line result of something you just did.
 *
 * It clears itself: a confirmation that outlives the moment it confirms turns
 * into furniture, and the next action pushes a new one anyway. A pending
 * message never expires, because the work it describes is what ends it, and an
 * error is given longer than a success since it is the one you may need to read
 * twice or copy out.
 */
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

/** A footer that calls out edits that still need saving. */
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
