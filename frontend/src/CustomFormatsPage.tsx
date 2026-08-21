import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { api } from './api/client'
import { Icon } from './components/Icon'
import { Badge, Button, EmptyState, Input, Panel, Select } from './components/ui'
import { browserSafeId, isRegexConditionType } from './lib/uiState'
import { useUrlState } from './lib/urlState'
import type {
  CustomFormat,
  CustomFormatCondition,
  CustomFormatConditionEvaluation,
  CustomFormatConditionType,
  CustomFormatEvaluation,
  CustomFormatScope,
  CustomFormatTestAllResult,
  CustomFormatTestResult,
  QualityProfile,
} from './types'

const CONDITION_TYPES: Array<{ value: CustomFormatConditionType; label: string }> = [
  { value: 'release_title', label: 'Release Title' },
  { value: 'release_group', label: 'Release Group' },
  { value: 'quality', label: 'Quality' },
  { value: 'quality_modifier', label: 'Quality Modifier' },
  { value: 'resolution', label: 'Resolution' },
  { value: 'source', label: 'Source' },
  { value: 'edition', label: 'Edition' },
  { value: 'language', label: 'Language' },
  { value: 'indexer', label: 'Indexer' },
  { value: 'web_provider', label: 'WEB Provider' },
  { value: 'video_codec', label: 'Video Codec' },
  { value: 'audio_codec', label: 'Audio Codec' },
  { value: 'audio_channels', label: 'Audio Channels' },
  { value: 'hdr_type', label: 'HDR Type' },
  { value: 'release_attribute', label: 'Release Attribute' },
]

const typeLabel = (type: string) => CONDITION_TYPES.find((item) => item.value === type)?.label ?? type
const isRegexType = isRegexConditionType
const scopeLabel = (scope: CustomFormatScope) => scope === 'both' ? 'Both' : scope === 'movies' ? 'Movies' : 'Shows'

function freshCondition(type: CustomFormatConditionType = 'release_attribute'): CustomFormatCondition {
  return {
    id: browserSafeId(),
    type,
    value: isRegexType(type) ? undefined : '',
    pattern: isRegexType(type) ? '' : undefined,
    required: false,
    negate: false,
    caseSensitive: false,
  }
}

function freshFormat(): CustomFormat {
  return {
    id: '',
    name: 'New Custom Format',
    description: '',
    mediaScope: 'both',
    enabled: true,
    schemaVersion: 1,
    conditions: [freshCondition()],
    conditionCount: 1,
    usedByProfiles: 0,
    revision: 0,
  }
}

function cloneFormat(format: CustomFormat): CustomFormat {
  return {
    ...format,
    id: '',
    name: `${format.name} Copy`,
    revision: 0,
    usedByProfiles: 0,
    conditions: format.conditions.map((condition) => ({ ...condition, id: browserSafeId() })),
  }
}

function conditionValue(condition: CustomFormatCondition): string {
  if (isRegexType(condition.type)) return condition.pattern ?? ''
  return Array.isArray(condition.value) ? condition.value.join(', ') : condition.value ?? ''
}

function formatJsonDownload(name: string, payload: Record<string, unknown>) {
  const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: 'application/json' })
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = name
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(href), 0)
}

function evidenceText(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try { return JSON.stringify(value) } catch { return String(value) }
}

function EvaluationRows({ evaluation }: { evaluation: CustomFormatEvaluation }) {
  return <div className="cf-evaluation">
    <div className={`cf-evaluation-summary ${evaluation.matched ? 'matched' : 'not-matched'}`}>
      <Icon name={evaluation.matched ? 'check' : 'close'} size={15} />
      <strong>{evaluation.matched ? 'Matches this format' : 'Does not match this format'}</strong>
      <span>No score is assigned here</span>
    </div>
    {evaluation.error && <div className="cf-inline-error">{evaluation.error}</div>}
    {evaluation.conditions.map((condition) => <ConditionEvaluationRow key={condition.conditionId} condition={condition} />)}
  </div>
}

function ConditionEvaluationRow({ condition }: { condition: CustomFormatConditionEvaluation }) {
  return <div className={`cf-condition-result ${condition.effectiveResult ? 'pass' : 'fail'}`}>
    <div className="cf-result-icon"><Icon name={condition.effectiveResult ? 'check' : 'close'} size={14} /></div>
    <div className="cf-result-copy">
      <strong>{condition.name || typeLabel(condition.conditionType)}</strong>
      <span>{condition.reason || (condition.matched ? 'Condition matched.' : 'Condition did not match.')}</span>
      <small>Evidence: {evidenceText(condition.evidence)} · Expected: {evidenceText(condition.expected)}{condition.negated ? ' · Negated' : ''}{condition.required ? ' · Required' : ' · Optional'}</small>
    </div>
  </div>
}

export default function CustomFormatsPageView() {
  const [formats, setFormats] = useState<CustomFormat[]>([])
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [selectedId, setSelectedId] = useUrlState('format')
  const [draft, setDraft] = useState<CustomFormat>(() => freshFormat())
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [releaseName, setReleaseName] = useState('Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM')
  const [testIndexer, setTestIndexer] = useState('')
  const [testProfileId, setTestProfileId] = useState('')
  const [testResult, setTestResult] = useState<CustomFormatTestResult | null>(null)
  const [testAllResult, setTestAllResult] = useState<CustomFormatTestAllResult | null>(null)
  const [testing, setTesting] = useState(false)
  const importInput = useRef<HTMLInputElement>(null)

  const loadFormats = async (preferredId?: string) => {
    setLoading(true)
    try {
      const [items, profileRows] = await Promise.all([api.customFormats(), api.qualityProfiles()])
      setFormats(items)
      setProfiles(profileRows)
      if (testProfileId && !profileRows.some((profile) => profile.id === testProfileId)) setTestProfileId('')
      const nextId = preferredId && items.some((item) => item.id === preferredId)
        ? preferredId
        : selectedId && items.some((item) => item.id === selectedId)
          ? selectedId
          : items[0]?.id ?? ''
      if (nextId) {
        const selected = items.find((item) => item.id === nextId)
        if (selected) { setSelectedId(nextId); setDraft(selected) }
      } else {
        setSelectedId('')
        setDraft(freshFormat())
      }
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load Custom Formats.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadFormats() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const selected = useMemo(() => formats.find((format) => format.id === selectedId), [formats, selectedId])
  const dirty = useMemo(() => selected ? JSON.stringify({ ...selected, updatedAt: undefined }) !== JSON.stringify({ ...draft, updatedAt: undefined }) : Boolean(draft.name || draft.conditions.length), [selected, draft])

  const chooseFormat = (format: CustomFormat) => {
    setSelectedId(format.id)
    setDraft(format)
    setTestResult(null)
    setTestAllResult(null)
    setError('')
    setMessage('')
  }

  const newFormat = () => {
    setSelectedId('')
    setDraft(freshFormat())
    setTestResult(null)
    setTestAllResult(null)
    setError('')
    setMessage('')
  }

  const duplicateFormat = () => {
    setSelectedId('')
    setDraft(cloneFormat(draft))
    setTestResult(null)
    setTestAllResult(null)
    setMessage('Duplicated as an unsaved Custom Format.')
  }

  const updateCondition = (id: string, patch: Partial<CustomFormatCondition>) => {
    setDraft((current) => ({ ...current, conditions: current.conditions.map((condition) => condition.id === id ? { ...condition, ...patch } : condition) }))
    setTestResult(null)
  }

  const changeConditionType = (id: string, type: CustomFormatConditionType) => {
    updateCondition(id, {
      type,
      value: isRegexType(type) ? undefined : '',
      pattern: isRegexType(type) ? '' : undefined,
      caseSensitive: false,
    })
  }

  const save = async () => {
    setSaving(true); setError(''); setMessage('')
    try {
      const saved = draft.id
        ? await api.updateCustomFormat(draft.id, { ...draft, expectedRevision: draft.revision })
        : await api.createCustomFormat(draft)
      await loadFormats(saved.id)
      setMessage('Custom Format saved.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save the Custom Format.')
    } finally { setSaving(false) }
  }

  const remove = async () => {
    if (!draft.id) { newFormat(); return }
    if (!window.confirm(`Delete Custom Format “${draft.name}”? This does not delete media.`)) return
    setSaving(true); setError(''); setMessage('')
    try {
      await api.deleteCustomFormat(draft.id)
      await loadFormats()
      setMessage('Custom Format deleted.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete the Custom Format.') }
    finally { setSaving(false) }
  }

  const testOne = async () => {
    setTesting(true); setError(''); setTestAllResult(null)
    try { setTestResult(await api.testCustomFormat(draft, releaseName, testIndexer || undefined)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not test the Custom Format.') }
    finally { setTesting(false) }
  }

  const testAll = async () => {
    setTesting(true); setError(''); setTestResult(null)
    try { setTestAllResult(await api.testAllCustomFormats(releaseName, draft.mediaScope, testIndexer || undefined, testProfileId || undefined)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not test all Custom Formats.') }
    finally { setTesting(false) }
  }

  const exportOne = async () => {
    if (!draft.id) { setError('Save this Custom Format before exporting it.'); return }
    try { formatJsonDownload(`${draft.name.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toLowerCase() || 'custom-format'}.json`, await api.exportCustomFormat(draft.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not export the Custom Format.') }
  }

  const exportAll = async () => {
    try { formatJsonDownload('medialogue-custom-formats.json', await api.exportCustomFormats()) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not export Custom Formats.') }
  }

  const importBundle = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setError(''); setMessage('')
    try {
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>
      const result = await api.importCustomFormats(parsed)
      await loadFormats(result.imported[0]?.id)
      setMessage(`Imported ${result.count} Custom Format${result.count === 1 ? '' : 's'}.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not import Custom Formats.') }
  }

  return <div className="page">
    <div className="page-heading">
      <div className="heading-copy"><div className="eyebrow">MEDIALOGUE / CUSTOM FORMATS</div><h1>Custom Formats</h1><p>Explainable release matching rules. Scores belong to Quality Profiles, never to the format itself.</p></div>
      <div className="heading-actions"><Button variant="primary" icon="plus" onClick={newFormat}>New custom format</Button></div>
    </div>

    {error && <div className="cf-banner cf-banner-error"><Icon name="alert" size={15} /><span>{error}</span></div>}
    {message && <div className="cf-banner cf-banner-success"><Icon name="check" size={15} /><span>{message}</span></div>}

    <div className="split">
      <Panel className="format-list" title="Formats" eyebrow={`${formats.length} CONFIGURED`}>
        {loading ? <div className="cf-list-state">Loading Custom Formats…</div> : formats.length ? <div className="format-list-items">
          {formats.map((format) => <button key={format.id} className={selectedId === format.id ? 'selected' : ''} onClick={() => chooseFormat(format)}>
            <span className="format-symbol"><Icon name="sliders" size={14} /></span>
            <span><strong>{format.name}</strong><small>{format.conditionCount} condition{format.conditionCount === 1 ? '' : 's'} · {scopeLabel(format.mediaScope)}{!format.enabled ? ' · Disabled' : ''}</small></span>
            <span className="format-score">{format.usedByProfiles} profile{format.usedByProfiles === 1 ? '' : 's'}</span>
          </button>)}
        </div> : <div className="cf-list-state">No Custom Formats yet.</div>}
        <div className="cf-list-actions"><Button variant="ghost" icon="plus" onClick={newFormat}>Add format</Button><Button variant="ghost" icon="archive" onClick={() => importInput.current?.click()}>Import JSON</Button></div>
        <input ref={importInput} className="cf-file-input" type="file" accept="application/json,.json" onChange={(event) => void importBundle(event)} />
      </Panel>

      <Panel className="format-editor" eyebrow={draft.id ? 'CUSTOM FORMAT' : 'NEW CUSTOM FORMAT'} title={draft.name || 'Untitled Custom Format'} action={<div className="cf-editor-actions"><Button variant="ghost" onClick={duplicateFormat} disabled={!draft.conditions.length}>Duplicate</Button><Button variant="primary" onClick={() => void save()} disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</Button></div>}>
        <div className="cf-editor-meta">
          <label><span className="field-label">Name</span><Input value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} /></label>
          <label><span className="field-label">Scope</span><Select value={draft.mediaScope} onChange={(event) => setDraft((current) => ({ ...current, mediaScope: event.target.value as CustomFormatScope }))}><option value="movies">Movies</option><option value="shows">Shows</option><option value="both">Both</option></Select></label>
          <label className="cf-enabled-field"><span className="field-label">Enabled</span><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))} /></label>
          <label className="cf-description-field"><span className="field-label">Description</span><Input value={draft.description ?? ''} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} placeholder="Optional description" /></label>
        </div>

        <div className="format-editor-grid">
          <div className="cf-conditions-column">
            <div className="field-label">Conditions <span>Required groups AND · optional conditions of the same type OR</span></div>
            {draft.conditions.map((condition, index) => <div className="condition-card cf-condition-card" key={condition.id}>
              <div className="condition-top">
                <div className="cf-condition-badges"><Badge tone={condition.required ? 'blue' : 'neutral'}>{condition.required ? 'Required' : 'Optional'}</Badge>{condition.negate && <Badge tone="amber">Negated</Badge>}</div>
                <button className="icon-button" title="Remove condition" onClick={() => setDraft((current) => ({ ...current, conditions: current.conditions.filter((item) => item.id !== condition.id) }))}><Icon name="close" size={15} /></button>
              </div>
              <div className="cf-condition-grid">
                <label><span>Type</span><Select value={condition.type} onChange={(event) => changeConditionType(condition.id, event.target.value as CustomFormatConditionType)}>{CONDITION_TYPES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</Select></label>
                <label><span>Name</span><Input value={condition.name ?? ''} onChange={(event) => updateCondition(condition.id, { name: event.target.value })} placeholder={`Condition ${index + 1}`} /></label>
                <label className="cf-value-field"><span>{isRegexType(condition.type) ? 'Regex' : 'Value'}</span><Input value={conditionValue(condition)} onChange={(event) => updateCondition(condition.id, isRegexType(condition.type) ? { pattern: event.target.value } : { value: event.target.value })} placeholder={isRegexType(condition.type) ? 'Case-insensitive regex by default' : `Expected ${typeLabel(condition.type)}`} /></label>
                <label><span>Group</span><Input value={condition.group ?? ''} onChange={(event) => updateCondition(condition.id, { group: event.target.value })} placeholder="Automatic by type" /></label>
              </div>
              <div className="cf-condition-toggles">
                <label><input type="checkbox" checked={condition.required} onChange={(event) => updateCondition(condition.id, { required: event.target.checked })} /> Required</label>
                <label><input type="checkbox" checked={condition.negate} onChange={(event) => updateCondition(condition.id, { negate: event.target.checked })} /> Negate</label>
                {isRegexType(condition.type) && <label><input type="checkbox" checked={condition.caseSensitive} onChange={(event) => updateCondition(condition.id, { caseSensitive: event.target.checked })} /> Case-sensitive</label>}
              </div>
            </div>)}
            {!draft.conditions.length && <EmptyState icon="sliders" title="No conditions" detail="Add at least one condition before saving this Custom Format." />}
            <Button variant="ghost" icon="plus" onClick={() => setDraft((current) => ({ ...current, conditions: [...current.conditions, freshCondition()] }))}>Add condition</Button>
            <div className="cf-definition-note">Custom Formats only answer “does this release match?”. Positive, zero, and negative scores are configured later in Quality Profiles.</div>
          </div>

          <div className="format-test">
            <div className="field-label">Test Release <span>Uses the same parser as search and filesystem evidence</span></div>
            <Input value={releaseName} onChange={(event) => setReleaseName(event.target.value)} placeholder="Paste a release name" />
            <Input value={testIndexer} onChange={(event) => setTestIndexer(event.target.value)} placeholder="Indexer name (optional)" />
            <Select value={testProfileId} onChange={(event) => setTestProfileId(event.target.value)}><option value="">Test matches only · no profile score</option>{profiles.map((profile) => <option value={profile.id} key={profile.id}>Score with {profile.name}</option>)}</Select>
            <div className="cf-test-actions"><Button variant="secondary" icon="play" onClick={() => void testOne()} disabled={testing || !releaseName || !draft.conditions.length}>Test this format</Button><Button variant="ghost" onClick={() => void testAll()} disabled={testing || !releaseName}>Test all saved</Button></div>

            {testResult && <><EvaluationRows evaluation={testResult.evaluation} /><div className="cf-parser-block"><div className="eyebrow">FULL PARSER RESULT</div><pre>{JSON.stringify(testResult.parsed, null, 2)}</pre></div></>}
            {testAllResult && <div className="cf-test-all"><div className="cf-test-all-head"><strong>{testAllResult.matchedCount} of {testAllResult.formats.length} formats matched{testAllResult.totalScore !== undefined ? ` · total ${testAllResult.totalScore > 0 ? '+' : ''}${testAllResult.totalScore}` : ''}</strong><span>{testAllResult.qualityProfileName ? `Profile: ${testAllResult.qualityProfileName}` : `Scope: ${scopeLabel(draft.mediaScope)}`}</span></div>{testAllResult.formats.map((evaluation) => <div className="cf-test-all-row" key={evaluation.customFormatId}><Badge tone={evaluation.matched ? 'green' : 'neutral'}>{evaluation.matched ? 'Match' : 'No match'}</Badge><span>{evaluation.customFormatName}</span><small>{evaluation.conditions.filter((condition) => condition.effectiveResult).length}/{evaluation.conditions.length} conditions effective{evaluation.profileScore !== undefined ? ` · Profile ${evaluation.profileScore > 0 ? '+' : ''}${evaluation.profileScore} · Contribution ${(evaluation.contribution ?? 0) > 0 ? '+' : ''}${evaluation.contribution ?? 0}` : ''}</small></div>)}<div className="cf-parser-block"><div className="eyebrow">FULL PARSER RESULT</div><pre>{JSON.stringify(testAllResult.parsed, null, 2)}</pre></div></div>}
            {!testResult && !testAllResult && <div className="cf-test-placeholder"><Icon name="spark" size={20} /><strong>Explain a release</strong><span>Run a test to see every condition pass/fail, the evidence used, and the complete structured parser result.</span></div>}
          </div>
        </div>

        <div className="cf-editor-footer">
          <div className="cf-export-actions"><Button variant="ghost" icon="archive" onClick={() => void exportAll()}>Export all</Button><Button variant="ghost" onClick={() => void exportOne()} disabled={!draft.id}>Export this format</Button></div>
          <span className="cf-dirty-state">{draft.id ? `Revision ${draft.revision}${dirty ? ' · unsaved changes' : ''}` : 'Unsaved new format'}</span>
          <Button variant="danger" onClick={() => void remove()} disabled={saving}>{draft.id ? 'Delete format' : 'Discard new format'}</Button>
        </div>
      </Panel>
    </div>
  </div>
}
