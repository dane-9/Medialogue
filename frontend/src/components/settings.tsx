import { useState } from 'react'
import type { InputHTMLAttributes, PropsWithChildren, ReactNode } from 'react'
import { Icon } from './Icon'
import { Badge, Button } from './ui'

/**
 * A labelled control with one line explaining what it actually does.
 *
 * Settings options are not guessable from their names — "Category" and "Scope"
 * in particular mean something different here than they do in comparable
 * tools — so help is permanent text rather than a tooltip you have to discover.
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
export function SectionHead({ icon, title, description, status, statusTone = 'neutral', detail, detailTone, action, autosave, divided }: {
  icon: Parameters<typeof Icon>[0]['name']
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
    <div className="rail-glyph"><Icon name={icon} size={17} /></div>
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
 * Panel feedback carries a tone. Previously every message — saved, saving and
 * failed alike — rendered in the same neutral box, so success and failure were
 * indistinguishable at a glance.
 */
export type Message = { tone: 'ok' | 'error' | 'busy'; text: string } | null

export function Note({ message }: { message: Message }) {
  if (!message) return null
  const icon = message.tone === 'error' ? 'alert' : message.tone === 'ok' ? 'check' : 'clock'
  const cls = message.tone === 'error' ? 'error-note' : message.tone === 'ok' ? 'note-ok' : ''
  return <div className={`settings-note ${cls}`}><Icon name={icon} size={15} /><span>{message.text}</span></div>
}

export const ok = (text: string): Message => ({ tone: 'ok', text })
export const failed = (reason: unknown, fallback: string): Message => ({ tone: 'error', text: reason instanceof Error ? reason.message : fallback })
export const pending = (text: string): Message => ({ tone: 'busy', text })

/**
 * A footer that always states whether what you are looking at is what is
 * stored, so a half-edited panel can never be mistaken for a saved one.
 */
export function SaveFooter({ dirty, saving, onSave, onRevert, children }: PropsWithChildren<{
  dirty: boolean
  saving?: boolean
  onSave: () => void
  onRevert?: () => void
}>) {
  return <div className="settings-footer">
    <span className={`footer-state ${dirty ? '' : 'clean'}`}>{dirty ? 'Unsaved changes' : 'All changes saved'}</span>
    {children}
    {onRevert && <Button variant="ghost" onClick={onRevert} disabled={!dirty || saving}>Revert</Button>}
    <Button variant="primary" onClick={onSave} disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save changes'}</Button>
  </div>
}
