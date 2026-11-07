// Plex tab: server connection used for verification.
import { useEffect, useState } from 'react'
import { Field, Note, SaveFooter, SectionHead, Secret, SettingsGroup, failed, ok, pending } from '../../components/settings'
import type { Message, StatusTone } from '../../components/settings'
import { Button, EnabledButton, Input } from '../../components/ui'
import { integrationsApi } from '../../api/modules/integrations'
import type { PlexConfiguration } from '../../types'

export function PlexSettings() {
  const [configuration, setConfiguration] = useState<PlexConfiguration | null>(null)
  const [url, setUrl] = useState('http://plex:32400')
  const [token, setToken] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [message, setMessage] = useState<Message>(null)
  const [working, setWorking] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = () => integrationsApi.plexConfiguration().then((value) => {
    setConfiguration(value)
    if (value.url) setUrl(value.url)
    setEnabled(value.enabled)
    setToken(''); setDirty(false)
  })
  useEffect(() => { load().catch((reason) => setMessage(failed(reason, 'Could not load Plex settings.'))).finally(() => setLoading(false)) }, [])

  const save = async () => {
    if (!url.trim()) { setMessage({ tone: 'error', text: 'Enter a Plex server URL.' }); return }
    setWorking(true); setMessage(pending('Saving Plex settings…'))
    try {
      const value = await integrationsApi.savePlex({ url: url.trim(), token: token || undefined, enabled, expected_revision: configuration?.revision })
      setConfiguration(value); setToken(''); setDirty(false); setMessage(ok('Plex settings saved.'))
      window.dispatchEvent(new Event('medialogue-plex-configuration-changed'))
    } catch (reason) { setMessage(failed(reason, 'Could not save Plex settings.')) }
    finally { setWorking(false) }
  }
  const syncLibrary = async () => {
    setWorking(true); setMessage(pending('Starting Plex library verification…'))
    try {
      const result = await integrationsApi.syncPlexLibrary()
      setMessage(ok(`Plex verification queued as job ${result.job_id.slice(0, 8)}…. It reads Plex's existing library; it does not trigger a Plex scan.`))
    } catch (reason) { setMessage(failed(reason, 'Could not start Plex verification.')) }
    finally { setWorking(false) }
  }

  const healthTone: StatusTone = configuration?.health === 'healthy' ? 'green' : configuration?.health === 'unavailable' ? 'red' : 'neutral'
  const live = configuration?.configured && configuration.enabled
  return <>
    <SectionHead title="Plex"
      description="Read-only verification. Nothing inside Plex is ever modified."
      status={loading ? 'Loading' : !configuration?.configured ? 'Not configured' : configuration.health} statusTone={healthTone}
      action={<EnabledButton enabled={enabled} onChange={(next) => { setEnabled(next); setDirty(true) }} />} />
    <div className="settings-form">
      <SettingsGroup>
        <Field label="URL" help={<>Plex Media Server address, including port. Example: <code>http://plex:32400</code></>}>
          <Input placeholder="http://plex:32400" value={url} onChange={(event) => { setUrl(event.target.value); setDirty(true) }} />
        </Field>
        <Field label="API token"
          help={<>Your <code>X-Plex-Token</code>. Held server-side. {configuration?.token_configured ? 'Leave blank to keep the current one.' : 'Required.'}</>}>
          <Secret placeholder={configuration?.token_configured ? 'Stored server-side · leave blank to preserve' : 'Required'} value={token} onChange={(event) => { setToken(event.target.value); setDirty(true) }} />
        </Field>
      </SettingsGroup>
    </div>
    {configuration?.last_error && <Note message={{ tone: 'error', text: configuration.last_error }} />}
    <Note message={message} />
    <SaveFooter dirty={dirty} saving={working} onSave={() => void save()}>
      {live && <span className="muted">Health is checked automatically every minute.</span>}
      {live && <Button variant="secondary" onClick={() => void syncLibrary()} disabled={working}>Sync library verification</Button>}
    </SaveFooter>
  </>
}
