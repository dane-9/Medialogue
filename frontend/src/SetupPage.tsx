import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from './api/client'
import { Badge, Button, Panel } from './components/ui'
import { Icon } from './components/Icon'
import type { SetupStatus, SetupStep } from './types'

export function SetupPage({ onComplete }: { onComplete: () => void }) {
  const navigate = useNavigate()
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    try { setStatus(await api.setupStatus()); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load setup status.') }
  }
  useEffect(() => { void load() }, [])

  const openStep = (step: SetupStep) => {
    if (!step.settings_tab) return
    navigate(`/settings?tab=${encodeURIComponent(step.settings_tab)}&setup=1`)
  }
  const finish = async () => {
    setBusy(true); setError('')
    try {
      await api.completeSetup(true)
      onComplete()
      navigate('/movies', { replace: true })
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not finish setup.') }
    finally { setBusy(false) }
  }

  return <div className="setup-page">
    <div className="setup-heading"><div><div className="eyebrow">FIRST-RUN SETUP</div><h1>Configure Medialogue</h1><p>Nothing here moves or deletes media. Configure the integrations you use, then start the first scan when ready.</p></div><Badge tone="green">Leave-in-place enforced</Badge></div>
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    <Panel className="setup-panel">
      {!status ? <div className="setup-loading">Loading setup state…</div> : <div className="setup-steps">{status.steps.map((step, index) => <div className="setup-step" key={step.key}><div className={`setup-step-number ${step.complete ? 'complete' : ''}`}>{step.complete ? <Icon name="check" size={15} /> : index + 1}</div><div className="setup-step-copy"><div><strong>{step.title}</strong>{step.optional && <span>Optional</span>}</div><p>{step.detail}</p></div><Badge tone={step.complete ? 'green' : 'neutral'}>{step.complete ? 'Ready' : 'Not configured'}</Badge>{step.settings_tab && <Button variant="ghost" onClick={() => openStep(step)}>{step.complete ? 'Review' : 'Configure'}</Button>}</div>)}</div>}
    </Panel>
    <div className="setup-finish"><div><strong>Ready to continue?</strong><p>Optional items can wait. Change the default password later in Settings → Security.</p></div><Button variant="primary" onClick={finish} disabled={busy}>{busy ? 'Saving…' : 'Finish setup'}</Button></div>
  </div>
}
