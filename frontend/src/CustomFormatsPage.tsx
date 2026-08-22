import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { api } from './api/client'
import { Icon } from './components/Icon'
import { Badge, Button, EmptyState, Input, Select } from './components/ui'
import { Field } from './components/settings'
import { Modal } from './components/Modal'
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
  const [editorOpen, setEditorOpen] = useState(false)
  const [filter, setFilter] = useState('')
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
      // The grid is the landing view, so nothing is selected by default. A
      // format is only adopted when it was just saved, or when a link named it,
      // in which case the editor opens straight onto it.
      const nextId = preferredId && items.some((item) => item.id === preferredId)
        ? preferredId
        : selectedId && items.some((item) => item.id === selectedId)
          ? selectedId
          : ''
      const selected = nextId ? items.find((item) => item.id === nextId) : undefined
      if (selected) {
        setSelectedId(selected.id)
        setDraft(selected)
        if (!preferredId) setEditorOpen(true)
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
    setEditorOpen(true)
  }

  const newFormat = () => {
    setSelectedId('')
    setDraft(freshFormat())
    setTestResult(null)
    setTestAllResult(null)
    setError('')
    setMessage('')
    setEditorOpen(true)
  }

  const duplicateFormat = () => {
    setSelectedId('')
    setDraft(cloneFormat(draft))
    setTestResult(null)
    setTestAllResult(null)
    setMessage('Duplicated as an unsaved Custom Format.')
    setEditorOpen(true)
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
      setEditorOpen(false)
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
      setEditorOpen(false)
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

  const visible = useMemo(() => {
    const term = filter.trim().toLowerCase()
    if (!term) return formats
    return formats.filter((format) =>
      format.name.toLowerCase().includes(term)
      || (format.description ?? '').toLowerCase().includes(term)
      || format.conditions.some((condition) => typeLabel(condition.type).toLowerCase().includes(term) || conditionValue(condition).toLowerCase().includes(term)))
  }, [formats, filter])

  const closeEditor = () => { setEditorOpen(false); setSelectedId(''); setTestResult(null); setTestAllResult(null) }

  return <div className="page">
    <div className="page-heading">
      <div className="heading-copy"><div className="eyebrow">MEDIALOGUE / CUSTOM FORMATS</div><h1>Custom Formats</h1><p>Explainable release matching rules. Scores belong to Quality Profiles, never to a format itself.</p></div>
      <div className="heading-actions">
        <Button variant="ghost" icon="archive" onClick={() => importInput.current?.click()}>Import</Button>
        <Button variant="ghost" icon="download" onClick={() => void exportAll()} disabled={!formats.length}>Export all</Button>
        <Button variant="primary" icon="plus" onClick={newFormat}>New custom format</Button>
      </div>
    </div>
    <input ref={importInput} className="cf-file-input" type="file" accept="application/json,.json" onChange={(event) => void importBundle(event)} />

    {error && <div className="cf-banner cf-banner-error"><Icon name="alert" size={15} /><span>{error}</span></div>}
    {message && <div className="cf-banner cf-banner-success"><Icon name="check" size={15} /><span>{message}</span></div>}

    <div className="toolbar">
      <div className="search-field"><Icon name="search" size={16} /><Input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter formats and conditions…" /></div>
      <div className="toolbar-spacer" />
      <span className="selection-count">{formats.length} format{formats.length === 1 ? '' : 's'}</span>
    </div>

    {loading ? <div className="cf-list-state">Loading Custom Formats…</div>
      : !formats.length ? <EmptyState icon="sliders" title="No Custom Formats yet" detail="A Custom Format is a named set of conditions that a release either matches or does not. Scores live on Quality Profiles." action={<Button variant="primary" icon="plus" onClick={newFormat}>Create the first one</Button>} />
      : !visible.length ? <EmptyState icon="search" title="No formats match that filter" detail="Nothing here matches the text you typed. Clear the filter to see every format." />
      : <div className="cf-card-grid">
          {visible.map((format) => <FormatCard key={format.id} format={format} onOpen={() => chooseFormat(format)} />)}
          <button className="cf-card cf-card-add" onClick={newFormat}>
            <Icon name="plus" size={22} />
            <strong>New custom format</strong>
            <span>Define a new matching rule</span>
          </button>
        </div>}

    {editorOpen && <Modal
      wide
      eyebrow={draft.id ? 'EDIT CUSTOM FORMAT' : 'NEW CUSTOM FORMAT'}
      title={draft.name || 'Untitled Custom Format'}
      onClose={closeEditor}
      footer={<>
        {draft.id && !draft.builtin ? <Button variant="danger" onClick={() => void remove()} disabled={saving}>Delete</Button> : null}
        {draft.id ? <Button variant="ghost" onClick={duplicateFormat} disabled={saving}>{draft.builtin ? 'Duplicate as editable' : 'Duplicate'}</Button> : null}
        {draft.id ? <Button variant="ghost" icon="download" onClick={() => void exportOne()} disabled={saving}>Export</Button> : null}
        <span className={`footer-state ${dirty ? '' : 'clean'}`}>{dirty ? 'Unsaved changes' : 'All changes saved'}</span>
        <Button variant="secondary" onClick={closeEditor} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={() => void save()} disabled={saving || !dirty}>{saving ? 'Saving…' : draft.builtin ? 'Save enabled state' : 'Save format'}</Button>
      </>}
    >
      <div className="cf-modal-grid">
        <Field label="Name" help="Shown wherever this format is referenced, including Quality Profile scores.">
          <Input value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} readOnly={draft.builtin} />
        </Field>
        <Field label="Applies to" help="Which library a release must belong to for this format to be evaluated at all.">
          <Select value={draft.mediaScope} onChange={(event) => setDraft((current) => ({ ...current, mediaScope: event.target.value as CustomFormatScope }))} disabled={draft.builtin}>
            <option value="both">Movies &amp; Shows</option>
            <option value="movies">Movies</option>
            <option value="shows">Shows</option>
          </Select>
        </Field>
        <Field label="Description" wide help="Optional note explaining what this format is for. Only ever shown to you.">
          <Input value={draft.description ?? ''} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} placeholder="What does this format capture?" readOnly={draft.builtin} />
        </Field>
        <Field label="Enabled" wide help="When off, this format is skipped during evaluation and contributes no score anywhere.">
          <div className="setting-control">
            <button type="button" aria-pressed={draft.enabled} className="toggle" onClick={() => setDraft((current) => ({ ...current, enabled: !current.enabled }))}><span /></button>
            <span className="toggle-label">{draft.enabled ? 'Enabled' : 'Disabled'}</span>
          </div>
        </Field>
      </div>

      <div className="cf-modal-section">
        <div className="section-title">
          <h3>Conditions</h3>
          {!draft.builtin && <Button variant="secondary" icon="plus" onClick={() => setDraft((current) => ({ ...current, conditions: [...current.conditions, freshCondition()] }))}>Add condition</Button>}
        </div>
        {draft.builtin
          ? <div className="settings-note"><Icon name="shield" size={15} /><span>Medialogue maintains this format, so improvements to its matching reach you automatically. You can enable or disable it and score it in any Quality Profile. To change what it matches, use <strong>Duplicate as editable</strong> and disable this one.</span></div>
          : <p className="cf-modal-note">Every condition marked <strong>Required</strong> must pass. If none are required, any single match is enough.</p>}
        {draft.conditions.length ? <div className="cf-condition-list">
          {draft.conditions.map((condition, index) => <div className="cf-condition-card" key={condition.id}>
            <div className="condition-top">
              <strong>Condition {index + 1}</strong>
              <div className="cf-condition-badges">
                {condition.required && <Badge tone="blue">Required</Badge>}
                {condition.negate && <Badge tone="amber">Negated</Badge>}
                {isRegexType(condition.type) && <Badge tone="neutral">Regex</Badge>}
                {!draft.builtin && <button className="icon-button" aria-label={`Remove condition ${index + 1}`} onClick={() => setDraft((current) => ({ ...current, conditions: current.conditions.filter((item) => item.id !== condition.id) }))}><Icon name="close" size={15} /></button>}
              </div>
            </div>
            <div className="cf-condition-grid">
              <label><span>Field</span><Select value={condition.type} onChange={(event) => changeConditionType(condition.id, event.target.value as CustomFormatConditionType)} disabled={draft.builtin}>{CONDITION_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</Select></label>
              <label className="cf-value-field"><span>{isRegexType(condition.type) ? 'Pattern' : 'Value'}</span><Input value={conditionValue(condition)} onChange={(event) => updateCondition(condition.id, isRegexType(condition.type) ? { pattern: event.target.value } : { value: event.target.value })} placeholder={isRegexType(condition.type) ? '\\b(remux|bdremux)\\b' : 'Bluray'} readOnly={draft.builtin} /></label>
            </div>
            <div className="cf-condition-toggles">
              <label><input type="checkbox" checked={condition.required} onChange={(event) => updateCondition(condition.id, { required: event.target.checked })} disabled={draft.builtin} />Required</label>
              <label><input type="checkbox" checked={condition.negate} onChange={(event) => updateCondition(condition.id, { negate: event.target.checked })} disabled={draft.builtin} />Negate</label>
              <label><input type="checkbox" checked={condition.caseSensitive} onChange={(event) => updateCondition(condition.id, { caseSensitive: event.target.checked })} disabled={draft.builtin} />Case sensitive</label>
            </div>
          </div>)}
        </div> : <div className="cf-test-placeholder"><Icon name="sliders" size={18} /><strong>No conditions yet</strong><span>A format with no conditions never matches anything. Add at least one.</span></div>}
      </div>

      <div className="cf-modal-section">
        <div className="section-title"><h3>Test against a release</h3></div>
        <p className="cf-modal-note">Nothing is saved by testing. This evaluates the conditions above exactly as reconciliation would.</p>
        <Input value={releaseName} onChange={(event) => setReleaseName(event.target.value)} placeholder="Paste a release name…" />
        <div className="cf-test-actions">
          <Button variant="secondary" onClick={() => void testOne()} disabled={testing || !releaseName.trim()}>{testing ? 'Testing…' : 'Test this format'}</Button>
          <Button variant="ghost" onClick={() => void testAll()} disabled={testing || !releaseName.trim()}>Test every format</Button>
          <div className="toolbar-spacer" />
          <Select value={testProfileId} onChange={(event) => setTestProfileId(event.target.value)}><option value="">No profile scoring</option>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</Select>
        </div>
        {testResult?.evaluation && <EvaluationRows evaluation={testResult.evaluation} />}
        {testAllResult && <div className="cf-test-all">
          <div className="cf-test-all-head">
            <span>Every format evaluated{testAllResult.qualityProfileName ? ` against ${testAllResult.qualityProfileName}` : ''}</span>
            <span>{testAllResult.matchedCount} MATCHED{testAllResult.totalScore !== undefined ? ` · ${testAllResult.totalScore > 0 ? '+' : ''}${testAllResult.totalScore}` : ''}</span>
          </div>
          {testAllResult.formats.map((evaluation) => <div className="cf-test-all-row" key={evaluation.customFormatId}>
            <Icon name={evaluation.matched ? 'check' : 'close'} size={14} />
            <span>{evaluation.customFormatName}</span>
            <small>{evaluation.contribution === undefined ? '—' : `${evaluation.contribution > 0 ? '+' : ''}${evaluation.contribution}`}</small>
          </div>)}
        </div>}
        {testResult?.parsed && <div className="cf-parser-block"><div className="field-label">Parser output</div><pre>{JSON.stringify(testResult.parsed, null, 2)}</pre></div>}
      </div>
    </Modal>}
  </div>
}

/** One format at a glance: what it is, what it matches, and where it is used. */
function FormatCard({ format, onOpen }: { format: CustomFormat; onOpen: () => void }) {
  return <button className={`cf-card ${format.enabled ? '' : 'cf-card-off'}`} onClick={onOpen}>
    <div className="cf-card-head">
      <strong>{format.name}</strong>
      <span className="cf-card-badges">
        {format.builtin && <Badge tone="blue">Built-in</Badge>}
        <Badge tone={format.enabled ? 'green' : 'neutral'}>{format.enabled ? 'On' : 'Off'}</Badge>
      </span>
    </div>
    {format.description && <p className="cf-card-description">{format.description}</p>}
    <div className="cf-card-conditions">
      {format.conditions.slice(0, 4).map((condition) => <span className="cf-condition-chip" key={condition.id}>
        {condition.negate && <em>not</em>}
        {typeLabel(condition.type)}
        <code>{conditionValue(condition) || '—'}</code>
      </span>)}
      {format.conditions.length > 4 && <span className="cf-condition-chip cf-condition-more">+{format.conditions.length - 4} more</span>}
    </div>
    <div className="cf-card-foot">
      <span>{scopeLabel(format.mediaScope)}</span>
      <span>{format.conditionCount} condition{format.conditionCount === 1 ? '' : 's'}</span>
      <span>{format.usedByProfiles} profile{format.usedByProfiles === 1 ? '' : 's'}</span>
    </div>
  </button>
}
