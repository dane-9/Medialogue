// TMDB tab: metadata provider connection.
import { useEffect, useState } from 'react'
import { Field, Note, SaveFooter, SectionHead, Secret, SettingsGroup, failed, ok, pending } from '../../components/settings'
import type { Message, StatusTone } from '../../components/settings'
import { EnabledButton } from '../../components/ui'
import { integrationsApi } from '../../api/modules/integrations'

export function MetadataSettings() {
  const [configuration, setConfiguration] = useState<{ configured: boolean; api_key_configured: boolean; enabled: boolean; health: string; latency_ms?: number; last_error?: string; revision?: number } | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<Message>(null)

  const load = () => integrationsApi.tmdbConfiguration().then((value) => { setConfiguration(value); setEnabled(value.enabled); setApiKey(''); setDirty(false) })
  useEffect(() => { load().catch((reason) => setMessage(failed(reason, 'Could not load TMDB settings.'))) }, [])

  const save = async () => {
    setSaving(true); setMessage(pending('Saving TMDB settings…'))
    try {
      const value = await integrationsApi.saveTmdb({ api_key: apiKey || undefined, enabled, expected_revision: configuration?.revision })
      setConfiguration(value); setApiKey(''); setDirty(false); setMessage(ok('TMDB settings saved.'))
    } catch (reason) { setMessage(failed(reason, 'Could not save TMDB settings.')) } finally { setSaving(false) }
  }

  const healthTone: StatusTone = configuration?.health === 'healthy' ? 'green' : configuration?.health === 'unavailable' ? 'red' : 'neutral'
  return <>
    <SectionHead title="TMDB"
      description="Identity source for newly discovered titles. When off, discoveries stay unmatched."
      status={!configuration?.configured ? 'Not configured' : configuration.health} statusTone={healthTone}
      action={<EnabledButton enabled={enabled} onChange={(next) => { setEnabled(next); setDirty(true) }} />} />
    <div className="settings-form">
      <SettingsGroup>
        <Field label="API key"
          help={<>Your TMDB API Read Access Token. Held server-side. {configuration?.api_key_configured ? 'Leave blank to keep the current one.' : 'Required for automatic matching.'}</>}>
          <Secret value={apiKey} onChange={(event) => { setApiKey(event.target.value); setDirty(true) }} placeholder={configuration?.api_key_configured ? 'Stored server-side · leave blank to preserve' : 'Required for automatic matching'} />
        </Field>
      </SettingsGroup>
    </div>
    {configuration?.last_error && <Note message={{ tone: 'error', text: configuration.last_error }} />}
    <Note message={message} />
    <SaveFooter dirty={dirty} saving={saving} onSave={() => void save()} />
  </>
}
