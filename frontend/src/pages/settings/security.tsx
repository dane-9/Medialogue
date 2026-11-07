// Security tab: administrator password and sessions.
import { useEffect, useState } from 'react'
import { ageValue, formatAge, formatUntil } from '../../lib/age'
import { Field, Note, SectionHead, Secret, SettingsGroup, failed, ok, pending } from '../../components/settings'
import type { Message } from '../../components/settings'
import { Icon } from '../../components/Icon'
import { Badge, Button } from '../../components/ui'
import { authApi } from '../../api/modules/auth'

export function SecuritySettings() {
  const [security, setSecurity] = useState<{ default_password_warning: boolean; session_expires_at?: string } | null>(null)
  const [sessions, setSessions] = useState<Array<{ id: string; created_at: string; expires_at: string; last_seen_at: string; current: boolean }>>([])
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [message, setMessage] = useState<Message>(null)
  const [busy, setBusy] = useState(false)
  const loadSessions = async () => {
    try { setSessions(await authApi.sessions()) }
    catch (reason) { setMessage(failed(reason, 'Could not load active sessions.')) }
  }
  useEffect(() => {
    authApi.security().then(setSecurity).catch((reason) => setMessage(failed(reason, 'Could not load security state.')))
    void loadSessions()
  }, [])
  const change = async () => {
    if (newPassword.length < 12) { setMessage({ tone: 'error', text: 'New password must contain at least 12 characters.' }); return }
    if (newPassword !== confirmPassword) { setMessage({ tone: 'error', text: 'New password confirmation does not match.' }); return }
    setBusy(true); setMessage(pending('Changing password…'))
    try { await authApi.changePassword(currentPassword, newPassword); setCurrentPassword(''); setNewPassword(''); setConfirmPassword(''); setSecurity((value) => value ? { ...value, default_password_warning: false } : value); await loadSessions(); setMessage(ok('Password changed. Other active sessions were revoked.')) }
    catch (reason) { setMessage(failed(reason, 'Could not change password.')) }
    finally { setBusy(false) }
  }
  const revokeSession = async (sessionId: string) => {
    setBusy(true); setMessage(pending('Revoking session…'))
    try { await authApi.revokeSession(sessionId); await loadSessions(); setMessage(ok('Session revoked.')) }
    catch (reason) { setMessage(failed(reason, 'Could not revoke session.')) }
    finally { setBusy(false) }
  }
  const revokeOthers = async () => {
    setBusy(true); setMessage(pending('Revoking other sessions…'))
    try { await authApi.revokeOtherSessions(); await loadSessions(); setMessage(ok('Other sessions revoked.')) }
    catch (reason) { setMessage(failed(reason, 'Could not revoke other sessions.')) }
    finally { setBusy(false) }
  }
  const tooShort = newPassword.length > 0 && newPassword.length < 12
  const mismatch = confirmPassword.length > 0 && newPassword !== confirmPassword
  const otherSessions = sessions.filter((session) => !session.current)
  const orderedSessions = [...sessions].sort((a, b) => Number(b.current) - Number(a.current) || ageValue(b.created_at) - ageValue(a.created_at))
  return <>
    <SectionHead title="Administrator Security"
      description="Medialogue uses a single local administrator account and secure session cookies. There is no self-service recovery, a lost password is reset from the server."
      status={security?.default_password_warning ? 'Default password active' : 'Password changed'}
      statusTone={security?.default_password_warning ? 'amber' : 'green'}
      detail={security?.default_password_warning ? 'change this before exposing the host' : undefined}
      detailTone={security?.default_password_warning ? 'err' : undefined} />
    <div className="settings-form">
      <SettingsGroup legend="Change password">
        <Field label="Current password" help="Required to prove this session belongs to you.">
          <Secret value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" />
        </Field>
        <Field label="New password" error={tooShort ? 'Must contain at least 12 characters.' : undefined}
          help={<><strong>At least 12 characters.</strong> Length matters more than symbols, a passphrase of four uncommon words beats a short complex string.</>}>
          <Secret value={newPassword} onChange={(event) => setNewPassword(event.target.value)} aria-invalid={tooShort} />
        </Field>
        <Field label="Confirm new password" error={mismatch ? 'Does not match the new password.' : undefined} help="Must match exactly.">
          <Secret value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} aria-invalid={mismatch} />
        </Field>
      </SettingsGroup>
    </div>
    <div className="settings-note"><Icon name="lock" size={15} /><span>Changing the password revokes every other active session immediately. You stay signed in on this device.</span></div>
    <Note message={message} />
    <div className="settings-footer">
      <Button variant="primary" onClick={() => void change()} disabled={busy || !currentPassword || tooShort || mismatch || !newPassword}>{busy ? 'Changing…' : 'Change password'}</Button>
    </div>
    <SectionHead title="Active Sessions" divided
      description="Every signed-in browser holds its own session cookie. Revoke any you no longer recognise. The tokens themselves are opaque and never shown here."
      status={sessions.length ? `${sessions.length} signed in` : undefined}
      statusTone={otherSessions.length ? 'amber' : 'green'}
      action={otherSessions.length > 0 && <Button variant="secondary" icon="logout" onClick={() => void revokeOthers()} disabled={busy}>
        Revoke {otherSessions.length} other{otherSessions.length === 1 ? '' : 's'}
      </Button>} />
    <div className="session-list">
      {/* Sorted so the browser you are reading this in is always the first row,
          and the rest fall in order of how recently they were used. */}
      {orderedSessions.map((session) => {
        const expired = Date.parse(session.expires_at) <= Date.now()
        return <div className={`session-row ${session.current ? 'session-row-current' : ''}`} key={session.id}>
          <span className="session-row-icon"><Icon name={session.current ? 'check' : 'security'} size={17} /></span>
          <span className="session-row-copy">
            <span className="session-row-title">
              <strong>{session.current ? 'This browser' : 'Other browser'}</strong>
              {session.current && <Badge tone="green">Current</Badge>}
              {expired && <Badge tone="red">Expired</Badge>}
            </span>
            {/* No "last used": the server freezes last_seen_at at creation so
                authenticated reads stay write-free, so it only ever repeats the
                sign-in time. */}
            <span className="session-row-facts">
              <span>Signed in {formatAge(session.created_at)}</span>
              <span>Expires {formatUntil(session.expires_at)}</span>
            </span>
          </span>
          {!session.current && <Button variant="ghost" onClick={() => void revokeSession(session.id)} disabled={busy}>Revoke</Button>}
        </div>
      })}
      {!sessions.length && <p className="provider-empty-copy">No active sessions were returned. Reload the page if you are still signed in.</p>}
    </div>
  </>
}
