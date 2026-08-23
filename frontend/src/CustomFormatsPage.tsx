import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { api } from './api/client'
import { Icon } from './components/Icon'
import { Badge, Button, EmptyState, Input, Select } from './components/ui'
import { Field } from './components/settings'
import { Modal } from './components/Modal'
import { PageTopbar } from './components/Shell'
import { browserSafeId } from './lib/uiState'
import { useUrlState } from './lib/urlState'
import type {
  CustomFormat,
  CustomFormatCondition,
  CustomFormatConditionEvaluation,
  CustomFormatConditionType,
  CustomFormatEvaluation,
  CustomFormatSection,
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
function freshCondition(): CustomFormatCondition {
  return {
    id: browserSafeId(),
    type: 'release_title',
    value: undefined,
    pattern: '',
    required: false,
    negate: false,
    caseSensitive: false,
    scoreOffset: 0,
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
  if (condition.pattern !== undefined) return condition.pattern
  if (Array.isArray(condition.value)) return `^(?:${condition.value.join('|')})$`
  return condition.value ?? ''
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
      <span>{evaluation.scoreOffset ? `Match offset ${evaluation.scoreOffset > 0 ? '+' : ''}${evaluation.scoreOffset}` : 'No match offset'}</span>
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
      <small>Evidence: {evidenceText(condition.evidence)} · Expected: {evidenceText(condition.expected)}{condition.scoreOffset ? ` · Offset ${condition.scoreOffset > 0 ? '+' : ''}${condition.scoreOffset}` : ''}{condition.negated ? ' · Negated' : ''}{condition.required ? ' · Required' : ' · Optional'}</small>
    </div>
  </div>
}

export default function CustomFormatsPageView() {
  const [formats, setFormats] = useState<CustomFormat[]>([])
  const [sections, setSections] = useState<CustomFormatSection[]>([])
  const [sectionDraft, setSectionDraft] = useState<CustomFormatSection[]>([])
  const [organizerOpen, setOrganizerOpen] = useState(false)
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [selectedId, setSelectedId] = useUrlState('format')
  const [editorOpen, setEditorOpen] = useState(false)
  const [editorView, setEditorView] = useState<'details' | 'conditions' | 'test'>('details')
  const [editorSectionId, setEditorSectionId] = useState('')
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
  const [togglingIds, setTogglingIds] = useState<Set<string>>(new Set())
  const importInput = useRef<HTMLInputElement>(null)

  const loadFormats = async (preferredId?: string) => {
    setLoading(true)
    try {
      const [items, profileRows, layout] = await Promise.all([api.customFormats(), api.qualityProfiles(), api.customFormatLayout()])
      setFormats(items)
      setProfiles(profileRows)
      setSections(layout)
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
  const selectedSectionId = useMemo(() => sections.find((section) => section.formatIds.includes(selectedId))?.id ?? '', [sections, selectedId])
  const dirty = useMemo(() => selected
    ? JSON.stringify({ ...selected, updatedAt: undefined }) !== JSON.stringify({ ...draft, updatedAt: undefined }) || editorSectionId !== selectedSectionId
    : Boolean(draft.name || draft.conditions.length), [selected, draft, editorSectionId, selectedSectionId])

  const chooseFormat = (format: CustomFormat) => {
    setSelectedId(format.id)
    setDraft(format)
    setEditorSectionId(sections.find((section) => section.formatIds.includes(format.id))?.id ?? sections[0]?.id ?? '')
    setTestResult(null)
    setTestAllResult(null)
    setError('')
    setMessage('')
    setEditorView('conditions')
    setEditorOpen(true)
  }

  const newFormat = () => {
    setSelectedId('')
    setDraft(freshFormat())
    setEditorSectionId(sections[0]?.id ?? '')
    setTestResult(null)
    setTestAllResult(null)
    setError('')
    setMessage('')
    setEditorView('details')
    setEditorOpen(true)
  }

  const duplicateFormat = () => {
    setSelectedId('')
    setDraft(cloneFormat(draft))
    setEditorSectionId(editorSectionId || sections[0]?.id || '')
    setTestResult(null)
    setTestAllResult(null)
    setMessage('Duplicated as an unsaved Custom Format.')
    setEditorOpen(true)
  }

  const updateCondition = (id: string, patch: Partial<CustomFormatCondition>) => {
    setDraft((current) => ({ ...current, conditions: current.conditions.map((condition) => condition.id === id ? { ...condition, ...patch } : condition) }))
    setTestResult(null)
  }

  const save = async () => {
    setSaving(true); setError(''); setMessage('')
    try {
      const saved = draft.id
        ? draft.builtin
          ? await api.setCustomFormatEnabled(draft.id, draft.enabled, draft.revision)
          : await api.updateCustomFormat(draft.id, { ...draft, expectedRevision: draft.revision })
        : await api.createCustomFormat(draft)
      if (editorSectionId) {
        const nextLayout = sections.map((section) => ({
          ...section,
          formatIds: section.id === editorSectionId
            ? [...section.formatIds.filter((id) => id !== saved.id), saved.id]
            : section.formatIds.filter((id) => id !== saved.id),
        }))
        await api.saveCustomFormatLayout(nextLayout)
      }
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
  const requiredConditions = draft.conditions.filter((condition) => condition.required).length
  const optionalConditions = draft.conditions.length - requiredConditions
  const ruleLogic = requiredConditions && optionalConditions
    ? `All ${requiredConditions} required rules and at least one of the ${optionalConditions} optional rules must match.`
    : requiredConditions
      ? `All ${requiredConditions} required rule${requiredConditions === 1 ? '' : 's'} must match.`
      : `Any one of the ${optionalConditions} rule${optionalConditions === 1 ? '' : 's'} can match.`

  const openOrganizer = () => {
    setSectionDraft(sections.map((section) => ({ ...section, formatIds: [...section.formatIds] })))
    setOrganizerOpen(true)
    setError('')
  }

  const toggleFormat = async (format: CustomFormat) => {
    if (togglingIds.has(format.id)) return
    const enabled = !format.enabled
    setTogglingIds((current) => new Set(current).add(format.id))
    setFormats((current) => current.map((item) => item.id === format.id ? { ...item, enabled } : item))
    setError('')
    try {
      const saved = await api.setCustomFormatEnabled(format.id, enabled, format.revision)
      setFormats((current) => current.map((item) => item.id === saved.id ? saved : item))
      setMessage(`${format.name} turned ${enabled ? 'on' : 'off'}.`)
    } catch (reason) {
      setFormats((current) => current.map((item) => item.id === format.id ? format : item))
      setError(reason instanceof Error ? reason.message : `Could not turn ${format.name} ${enabled ? 'on' : 'off'}.`)
    } finally {
      setTogglingIds((current) => { const next = new Set(current); next.delete(format.id); return next })
    }
  }

  const moveSection = (index: number, offset: number) => setSectionDraft((current) => {
    const target = index + offset
    if (target < 0 || target >= current.length) return current
    const next = [...current]
    ;[next[index], next[target]] = [next[target], next[index]]
    return next
  })

  const moveFormat = (sectionIndex: number, formatIndex: number, offset: number) => setSectionDraft((current) => {
    const next = current.map((section) => ({ ...section, formatIds: [...section.formatIds] }))
    const target = formatIndex + offset
    if (target < 0 || target >= next[sectionIndex].formatIds.length) return current
    ;[next[sectionIndex].formatIds[formatIndex], next[sectionIndex].formatIds[target]] = [next[sectionIndex].formatIds[target], next[sectionIndex].formatIds[formatIndex]]
    return next
  })

  const assignFormat = (formatId: string, targetSectionId: string) => setSectionDraft((current) => current.map((section) => ({
    ...section,
    formatIds: section.id === targetSectionId
      ? [...section.formatIds.filter((id) => id !== formatId), formatId]
      : section.formatIds.filter((id) => id !== formatId),
  })))

  const removeSection = (index: number) => setSectionDraft((current) => {
    if (current.length <= 1) return current
    const next = current.map((section) => ({ ...section, formatIds: [...section.formatIds] }))
    const [removed] = next.splice(index, 1)
    next[Math.max(0, index - 1)].formatIds.push(...removed.formatIds)
    return next
  })

  const addSection = () => setSectionDraft((current) => [...current, { id: `section-${browserSafeId()}`, name: 'New section', formatIds: [] }])

  const saveOrganizer = async () => {
    if (sectionDraft.some((section) => !section.name.trim())) { setError('Every section needs a name.'); return }
    setSaving(true); setError('')
    try {
      const saved = await api.saveCustomFormatLayout(sectionDraft.map((section) => ({ ...section, name: section.name.trim() })))
      setSections(saved); setOrganizerOpen(false); setMessage('Custom Format organization saved.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save Custom Format organization.') }
    finally { setSaving(false) }
  }

  return <div className="page">
    <PageTopbar title="Custom Formats" subtitle="Explainable release matching rules with profile scores and optional per-match offsets." action={<>
        <Button variant="ghost" icon="archive" onClick={() => importInput.current?.click()}>Import</Button>
        <Button variant="ghost" icon="download" onClick={() => void exportAll()} disabled={!formats.length}>Export all</Button>
        <Button variant="primary" icon="plus" onClick={newFormat}>New custom format</Button>
      </>} />
    <input ref={importInput} className="cf-file-input" type="file" accept="application/json,.json" onChange={(event) => void importBundle(event)} />

    {error && <div className="cf-banner cf-banner-error"><Icon name="alert" size={15} /><span>{error}</span></div>}
    {message && <div className="cf-banner cf-banner-success"><Icon name="check" size={15} /><span>{message}</span></div>}

    <div className="toolbar">
      <div className="search-field"><Icon name="search" size={16} /><Input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter formats and conditions…" /></div>
      <div className="toolbar-spacer" />
      <Button variant="ghost" icon="sliders" onClick={openOrganizer}>Organize sections</Button>
      <span className="selection-count">{formats.length} format{formats.length === 1 ? '' : 's'}</span>
    </div>

    {loading ? <div className="cf-list-state">Loading Custom Formats…</div>
      : !formats.length ? <EmptyState icon="sliders" title="No Custom Formats yet" detail="A Custom Format is a named set of conditions that a release either matches or does not. Scores live on Quality Profiles." action={<Button variant="primary" icon="plus" onClick={newFormat}>Create the first one</Button>} />
      : !visible.length ? <EmptyState icon="search" title="No formats match that filter" detail="Nothing here matches the text you typed. Clear the filter to see every format." />
      : <div className="cf-sections">{sections.map((section) => {
          const sectionFormats = section.formatIds.map((id) => visible.find((format) => format.id === id)).filter((format): format is CustomFormat => Boolean(format))
          if (filter.trim() && !sectionFormats.length) return null
          return <section className="cf-section" key={section.id}>
            <div className="cf-section-head"><div><h2>{section.name}</h2><span>{sectionFormats.length} format{sectionFormats.length === 1 ? '' : 's'}</span></div></div>
            {sectionFormats.length ? <div className="cf-card-grid">{sectionFormats.map((format) => <FormatCard key={format.id} format={format} toggling={togglingIds.has(format.id)} onToggle={() => void toggleFormat(format)} onOpen={() => chooseFormat(format)} />)}</div> : <div className="cf-section-empty">No formats in this section.</div>}
          </section>
        })}</div>}

    {organizerOpen && <Modal
      wide
      eyebrow="CUSTOM FORMAT LAYOUT"
      title="Organize sections"
      onClose={() => setOrganizerOpen(false)}
      footer={<><span className="footer-state">Changes affect organization only, never matching or scores.</span><Button variant="secondary" onClick={() => setOrganizerOpen(false)} disabled={saving}>Cancel</Button><Button variant="primary" onClick={() => void saveOrganizer()} disabled={saving}>{saving ? 'Saving…' : 'Save organization'}</Button></>}
    >
      <div className="cf-organizer">
        <div className="section-title"><div><h3>Sections and format order</h3><p className="cf-modal-note">Move sections, reorder cards, or assign a format to another section.</p></div><Button variant="secondary" icon="plus" onClick={addSection}>Add section</Button></div>
        {sectionDraft.map((section, sectionIndex) => <div className="cf-organizer-section" key={section.id}>
          <div className="cf-organizer-section-head">
            <Input value={section.name} onChange={(event) => setSectionDraft((current) => current.map((item, index) => index === sectionIndex ? { ...item, name: event.target.value } : item))} />
            <button className="icon-button" aria-label="Move section up" disabled={sectionIndex === 0} onClick={() => moveSection(sectionIndex, -1)}><Icon name="chevron" size={15} style={{ transform: 'rotate(-90deg)' }} /></button>
            <button className="icon-button" aria-label="Move section down" disabled={sectionIndex === sectionDraft.length - 1} onClick={() => moveSection(sectionIndex, 1)}><Icon name="chevron" size={15} style={{ transform: 'rotate(90deg)' }} /></button>
            <button className="icon-button" aria-label="Delete section" disabled={sectionDraft.length === 1} onClick={() => removeSection(sectionIndex)}><Icon name="close" size={15} /></button>
          </div>
          <div className="cf-organizer-formats">{section.formatIds.map((formatId, formatIndex) => {
            const format = formats.find((item) => item.id === formatId)
            if (!format) return null
            return <div className="cf-organizer-format" key={formatId}><strong>{format.name}</strong><Select aria-label={`Section for ${format.name}`} value={section.id} onChange={(event) => assignFormat(formatId, event.target.value)}>{sectionDraft.map((target) => <option value={target.id} key={target.id}>{target.name}</option>)}</Select><button className="icon-button" aria-label={`Move ${format.name} up`} disabled={formatIndex === 0} onClick={() => moveFormat(sectionIndex, formatIndex, -1)}><Icon name="chevron" size={14} style={{ transform: 'rotate(-90deg)' }} /></button><button className="icon-button" aria-label={`Move ${format.name} down`} disabled={formatIndex === section.formatIds.length - 1} onClick={() => moveFormat(sectionIndex, formatIndex, 1)}><Icon name="chevron" size={14} style={{ transform: 'rotate(90deg)' }} /></button></div>
          })}</div>
        </div>)}
      </div>
    </Modal>}

    {editorOpen && <Modal
      wide
      className="cf-editor-modal"
      eyebrow={draft.id ? 'EDIT CUSTOM FORMAT' : 'NEW CUSTOM FORMAT'}
      title={draft.name || 'Untitled Custom Format'}
      onClose={closeEditor}
      footer={<>
        <div className="cf-editor-footer-toggle">
          <button type="button" aria-pressed={draft.enabled} className="toggle" onClick={() => setDraft((current) => ({ ...current, enabled: !current.enabled }))}><span /></button>
          <strong>{draft.enabled ? 'Enabled' : 'Disabled'}</strong>
        </div>
        {draft.id && !draft.builtin ? <Button variant="danger" onClick={() => void remove()} disabled={saving}>Delete</Button> : null}
        {draft.id ? <Button variant="ghost" onClick={duplicateFormat} disabled={saving}>Duplicate</Button> : null}
        {draft.id ? <Button variant="ghost" icon="download" onClick={() => void exportOne()} disabled={saving}>Export</Button> : null}
        <span className={`footer-state ${dirty ? '' : 'clean'}`}>{dirty ? 'Unsaved changes' : 'All changes saved'}</span>
        <Button variant="secondary" onClick={closeEditor} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={() => void save()} disabled={saving || !dirty || !draft.name.trim() || !draft.conditions.length}>{saving ? 'Saving…' : draft.builtin ? 'Save enabled state' : 'Save format'}</Button>
      </>}
    >
      <div className="cf-editor-shell">
        <aside className="cf-editor-rail">
          <nav aria-label="Custom Format editor sections">
            <button className={editorView === 'details' ? 'active' : ''} onClick={() => setEditorView('details')}><span>1</span><div><strong>Details</strong><small>Name, scope and status</small></div><Icon name="chevron" size={14} /></button>
            <button className={editorView === 'conditions' ? 'active' : ''} onClick={() => setEditorView('conditions')}><span>2</span><div><strong>Match rules</strong><small>{draft.conditions.length} configured</small></div><Icon name="chevron" size={14} /></button>
            <button className={editorView === 'test' ? 'active' : ''} onClick={() => setEditorView('test')}><span>3</span><div><strong>Test</strong><small>Try a real release title</small></div><Icon name="chevron" size={14} /></button>
          </nav>
          {draft.builtin && <div className="cf-editor-owned"><Icon name="shield" size={15} /><span>Maintained by Medialogue</span></div>}
        </aside>

        <div className="cf-editor-content">
          {editorView === 'details' && <section className="cf-editor-panel">
            <div className="cf-editor-panel-head"><div className="eyebrow">STEP 1 OF 3</div><h3>Describe this format</h3><p>Give the rule set a clear purpose and choose where it belongs.</p></div>
            <div className="cf-editor-fields">
              <Field label="Format name" help="Shown on cards, Quality Profiles and evaluation results.">
                <Input value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} placeholder="e.g. Dolby Vision with HDR10 fallback" readOnly={draft.builtin} autoFocus={!draft.id} />
              </Field>
              <Field label="Section" help="The header this format appears under on the Custom Formats page.">
                <Select value={editorSectionId} onChange={(event) => setEditorSectionId(event.target.value)}>
                  {sections.map((section) => <option value={section.id} key={section.id}>{section.name}</option>)}
                </Select>
              </Field>
              <Field label="Description" wide>
                <textarea className="input cf-description-input" value={draft.description ?? ''} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} placeholder="e.g. Lossless audio commonly found in remuxes and high-quality releases." readOnly={draft.builtin} />
              </Field>
            </div>
            <div className="cf-editor-next"><Button variant="primary" onClick={() => setEditorView('conditions')}>Continue to match rules</Button></div>
          </section>}

          {editorView === 'conditions' && <section className="cf-editor-panel cf-editor-rules-panel">
            <div className="cf-editor-panel-head cf-editor-panel-head-compact"><h3>Match rules</h3><p>Regular expressions run against the full release title.</p></div>
            {draft.builtin && <div className="settings-note"><Icon name="shield" size={15} /><span>Built-in rules are read-only so matching improvements can arrive automatically. Duplicate this format if you need your own variation.</span></div>}
            <div className="cf-logic-summary"><Icon name="activity" size={16} /><div><strong>How these rules combine:</strong><span>{draft.conditions.length ? ruleLogic : 'Add at least one rule. A format with no rules never matches.'}</span></div></div>
            {!draft.builtin && <div className="cf-add-rule"><div><strong>Add a regex rule</strong><span>Match text anywhere in the full release title.</span></div><Button variant="secondary" icon="plus" onClick={() => setDraft((current) => ({ ...current, conditions: [...current.conditions, { ...freshCondition(), name: `Rule ${current.conditions.length + 1}` }] }))}>Add rule</Button></div>}
            {draft.conditions.length ? <div className="cf-condition-list">
              {draft.conditions.map((condition, index) => <div className="cf-condition-card cf-rule-card" key={condition.id}>
                <div className="condition-top">
                  <div className="cf-rule-number"><span>{index + 1}</span><div>{draft.builtin ? <strong>{condition.name || typeLabel(condition.type)}</strong> : <Input className="cf-rule-title-input" aria-label={`Rule ${index + 1} name`} value={condition.name ?? ''} onChange={(event) => updateCondition(condition.id, { name: event.target.value || undefined })} placeholder={`Rule ${index + 1}`} />}</div></div>
                  <div className="cf-rule-header-actions">
                    <div className="cf-rule-header-options" aria-label={`Rule ${index + 1} options`}>
                      <button type="button" className={condition.required ? 'active' : ''} aria-pressed={condition.required} title="Every required rule must pass for this format to match." onClick={() => updateCondition(condition.id, { required: !condition.required })} disabled={draft.builtin}>Required</button>
                      <button type="button" className={condition.negate ? 'active' : ''} aria-pressed={condition.negate} title="Pass this rule only when the regular expression does not match." onClick={() => updateCondition(condition.id, { negate: !condition.negate })} disabled={draft.builtin}>Negate</button>
                      <button type="button" className={condition.caseSensitive ? 'active' : ''} aria-pressed={condition.caseSensitive} title="Match uppercase and lowercase characters exactly." onClick={() => updateCondition(condition.id, { caseSensitive: !condition.caseSensitive })} disabled={draft.builtin}>Case sensitive</button>
                    </div>
                    {!draft.builtin && <button className="icon-button" aria-label={`Remove rule ${index + 1}`} onClick={() => setDraft((current) => ({ ...current, conditions: current.conditions.filter((item) => item.id !== condition.id) }))}><Icon name="close" size={15} /></button>}
                  </div>
                </div>
                <div className="cf-rule-fields">
                  <label className="cf-rule-value"><span>Regular expression</span><Input value={conditionValue(condition)} onChange={(event) => updateCondition(condition.id, { pattern: event.target.value, value: undefined })} placeholder={condition.type === 'release_title' ? '\\b(remux|bdremux)\\b' : '^(?:AMZN|NF)$'} readOnly={draft.builtin} /></label>
                  <label className="cf-rule-offset"><span>Score offset</span><Input type="number" value={condition.scoreOffset} min={-100000} max={100000} onChange={(event) => updateCondition(condition.id, { scoreOffset: Number(event.target.value) || 0 })} readOnly={draft.builtin} /></label>
                </div>
              </div>)}
            </div> : <div className="cf-test-placeholder"><Icon name="sliders" size={18} /><strong>No match rules yet</strong><span>Choose an evidence type above and add the first rule.</span></div>}
            <div className="cf-editor-next"><Button variant="secondary" onClick={() => setEditorView('details')}>Back</Button><Button variant="primary" onClick={() => setEditorView('test')} disabled={!draft.conditions.length}>Continue to test</Button></div>
          </section>}

          {editorView === 'test' && <section className="cf-editor-panel">
            <div className="cf-editor-panel-head"><div className="eyebrow">STEP 3 OF 3</div><h3>Try a real release title</h3><p>Testing uses the unsaved rules currently in this editor and does not change your library.</p></div>
            <label className="cf-release-test-input"><span>Release title</span><textarea className="input" value={releaseName} onChange={(event) => setReleaseName(event.target.value)} placeholder="Paste a full release title…" /></label>
            <div className="cf-test-actions"><Button variant="primary" icon="activity" onClick={() => void testOne()} disabled={testing || !releaseName.trim() || !draft.conditions.length}>{testing ? 'Testing…' : 'Test this format'}</Button><Button variant="ghost" onClick={() => void testAll()} disabled={testing || !releaseName.trim()}>Compare every format</Button><div className="toolbar-spacer" /><Select value={testProfileId} onChange={(event) => setTestProfileId(event.target.value)}><option value="">No profile scoring</option>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</Select></div>
            {!testResult && !testAllResult && <div className="cf-test-placeholder"><Icon name="activity" size={18} /><strong>Ready to test</strong><span>Run the title above to see every rule’s evidence and result.</span></div>}
            {testResult?.evaluation && <EvaluationRows evaluation={testResult.evaluation} />}
            {testAllResult && <div className="cf-test-all"><div className="cf-test-all-head"><span>Every format evaluated{testAllResult.qualityProfileName ? ` against ${testAllResult.qualityProfileName}` : ''}</span><span>{testAllResult.matchedCount} MATCHED{testAllResult.totalScore !== undefined ? ` · ${testAllResult.totalScore > 0 ? '+' : ''}${testAllResult.totalScore}` : ''}</span></div>{testAllResult.formats.map((evaluation) => <div className="cf-test-all-row" key={evaluation.customFormatId}><Icon name={evaluation.matched ? 'check' : 'close'} size={14} /><span>{evaluation.customFormatName}</span><small>{evaluation.contribution === undefined ? '—' : `${evaluation.contribution > 0 ? '+' : ''}${evaluation.contribution}`}</small></div>)}</div>}
            {testResult?.parsed && <details className="cf-parser-details"><summary>View parser output</summary><pre>{JSON.stringify(testResult.parsed, null, 2)}</pre></details>}
            <div className="cf-editor-next"><Button variant="secondary" onClick={() => setEditorView('conditions')}>Back to rules</Button></div>
          </section>}
        </div>
      </div>
    </Modal>}
  </div>
}

/** One format at a glance: what it is, what it matches, and where it is used. */
function FormatCard({ format, toggling, onToggle, onOpen }: { format: CustomFormat; toggling: boolean; onToggle: () => void; onOpen: () => void }) {
  return <article className={`cf-card ${format.enabled ? '' : 'cf-card-off'}`} role="button" tabIndex={0} onClick={onOpen} onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onOpen() } }}>
    <div className="cf-card-head">
      <strong>{format.name}</strong>
      <span className="cf-card-badges">
        {format.builtin && <Badge tone="blue">Built-in</Badge>}
        <button type="button" className={`badge cf-card-toggle ${format.enabled ? 'badge-green' : ''}`} aria-pressed={format.enabled} aria-label={`Turn ${format.name} ${format.enabled ? 'off' : 'on'}`} disabled={toggling} onClick={(event) => { event.stopPropagation(); onToggle() }}><span className="badge-dot" />{toggling ? 'Saving…' : format.enabled ? 'On' : 'Off'}</button>
      </span>
    </div>
    <p className="cf-card-description">{format.description || 'No description provided.'}</p>
    <div className="cf-card-conditions">
      {format.conditions.slice(0, 4).map((condition) => <span className="cf-condition-chip" key={condition.id}>
        {condition.name || typeLabel(condition.type)}
      </span>)}
      {format.conditions.length > 4 && <span className="cf-condition-chip cf-condition-more">+{format.conditions.length - 4} more</span>}
    </div>
  </article>
}
