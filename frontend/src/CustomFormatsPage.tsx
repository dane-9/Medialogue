import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { settingsApi } from './api/modules/settings'
import { Icon } from './components/Icon'
import { Tooltip } from './components/Tooltip'
import { useConfirm } from './components/Confirm'
import { Badge, Button, EmptyState, EnabledButton, Input, Select } from './components/ui'
import { Field, SettingsGroup } from './components/settings'
import { Modal } from './components/Modal'
import { PageTopbar } from './components/Shell'
import { browserSafeId } from './lib/uiState'
import { copyName } from './lib/copyName'
import { conditionTypeLabel } from './lib/conditionType'
import { highlightMatch } from './lib/highlightMatch'
import { useUrlState } from './lib/urlState'
import { parseCustomFormatImportBundle } from './validation/customFormats'
import type {
  CustomFormat,
  CustomFormatCondition,
  CustomFormatConditionEvaluation,
  CustomFormatEvaluation,
  CustomFormatSection,
  CustomFormatTestAllResult,
  CustomFormatTestResult,
  QualityProfile,
} from './types'

function freshCondition(): CustomFormatCondition {
  return {
    id: browserSafeId(),
    type: 'release_title',
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
    enabled: true,
    schemaVersion: 1,
    conditions: [freshCondition()],
    supersedes: [],
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
  return condition.pattern ?? ''
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
  if (value === undefined || value === null || value === '') return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try { return JSON.stringify(value) } catch { return String(value) }
}

const signedOffset = (value: number) => `${value > 0 ? '+' : ''}${value}`

function EvaluationRows({ evaluation }: { evaluation: CustomFormatEvaluation }) {
  return <div className="cf-evaluation">
    <div className={`cf-evaluation-summary ${evaluation.matched ? 'matched' : 'not-matched'}`}>
      <Icon name={evaluation.matched ? 'check' : 'close'} size={15} />
      <strong>{evaluation.matched ? 'Matches this format' : evaluation.rawMatched ? `Suppressed by ${evaluation.suppressedByNames.join(', ')}` : 'Does not match this format'}</strong>
      {/* "No match offset" read as "no match", contradicting the verdict beside
          it. Name the thing it actually is: a score offset. */}
      <span>{evaluation.scoreOffset ? `Score offset ${signedOffset(evaluation.scoreOffset)}` : 'No score offset'}</span>
    </div>
    {evaluation.error && <div className="cf-inline-error">{evaluation.error}</div>}
    {evaluation.conditions.map((condition, index) => <ConditionEvaluationRow key={condition.conditionId} condition={condition} index={index} />)}
  </div>
}

function ConditionEvaluationRow({ condition, index }: { condition: CustomFormatConditionEvaluation; index: number }) {
  const passed = condition.effectiveResult
  // Show the text the rule actually searched, with the part it matched marked.
  // For most rules that is the release title; a typed rule reads a parsed field
  // instead, so the field is named rather than pretending it read the title.
  const field = conditionTypeLabel(condition.conditionType)
  const subject = evidenceText(condition.evidence)
  const parts = highlightMatch(subject, condition.regexMatch)

  return <div className={`cf-condition-result ${passed ? 'pass' : 'fail'}`}>
    <div className="cf-result-head">
      <span className="cf-result-icon"><Icon name={passed ? 'check' : 'close'} size={14} /></span>
      <strong>{condition.name || `Rule ${index + 1}`}</strong>
      <span className="cf-result-flags">
        {condition.negated && <span className="cf-result-flag">Negated</span>}
        {condition.required && <span className="cf-result-flag">Required</span>}
        {condition.scoreOffset ? <span className="cf-result-flag">Offset {signedOffset(condition.scoreOffset)}</span> : null}
      </span>
    </div>
    <p className="cf-result-subject">
      {condition.conditionType !== 'release_title' && <span className="cf-result-field">{field}</span>}
      {parts
        ? <code>{parts.before}<mark>{parts.hit}</mark>{parts.after}</code>
        : <code className="cf-result-nohit">{subject}</code>}
    </p>
    {/* A failure has to say why; on a pass the marked text already shows it. */}
    {!passed && condition.reason && <p className="cf-result-reason">{condition.reason}</p>}
  </div>
}

export default function CustomFormatsPageView() {
  const confirm = useConfirm()
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
  const testIndexer = ''
  const [testProfileId, setTestProfileId] = useState('')
  const [testResult, setTestResult] = useState<CustomFormatTestResult | null>(null)
  const [testAllResult, setTestAllResult] = useState<CustomFormatTestAllResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [togglingIds, setTogglingIds] = useState<Set<string>>(new Set())
  const importInput = useRef<HTMLInputElement>(null)

  const loadFormats = async (preferredId?: string) => {
    setLoading(true)
    try {
      const [items, profileRows, layout] = await Promise.all([settingsApi.customFormats(), settingsApi.qualityProfiles(), settingsApi.customFormatLayout()])
      setFormats(items)
      setProfiles(profileRows)
      setSections(layout)
      if (testProfileId && !profileRows.some((profile) => profile.id === testProfileId)) setTestProfileId('')
      const nextId = preferredId && items.some((item) => item.id === preferredId)
        ? preferredId
        : selectedId && items.some((item) => item.id === selectedId)
          ? selectedId
          : ''
      const selected = nextId ? items.find((item) => item.id === nextId) : undefined
      if (selected) {
        setSelectedId(selected.id)
        setDraft(selected)
        setEditorSectionId(layout.find((section) => section.formatIds.includes(selected.id))?.id ?? layout[0]?.id ?? '')
        if (!preferredId) setEditorOpen(true)
      } else {
        setSelectedId('')
        setDraft(freshFormat())
        setEditorSectionId(layout[0]?.id ?? '')
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

  const duplicateFormatNow = async (source: CustomFormat) => {
    setError(''); setMessage('')
    try {
      const clone = { ...cloneFormat(source), name: copyName(source.name, formats.map((item) => item.name)) }
      const saved = await settingsApi.createCustomFormat(clone)
      const section = sections.find((item) => item.formatIds.includes(source.id))
      if (section) {
        await settingsApi.saveCustomFormatLayout(sections.map((item) => item.id === section.id
          ? { ...item, formatIds: item.formatIds.flatMap((id) => id === source.id ? [id, saved.id] : [id]) }
          : item))
      }
      await loadFormats(saved.id)
      setMessage(`${saved.name} created.`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not duplicate the Custom Format.')
    }
  }

  const updateCondition = (id: string, patch: Partial<CustomFormatCondition>) => {
    setDraft((current) => ({ ...current, conditions: current.conditions.map((condition) => condition.id === id ? { ...condition, ...patch } : condition) }))
    setTestResult(null)
  }

  const save = async () => {
    if (draft.conditions.some((condition) => !conditionValue(condition).trim())) {
      setError('Every match rule needs a regular expression.')
      return
    }
    setSaving(true); setError(''); setMessage('')
    try {
      const saved = draft.id
        ? draft.builtin
          ? await settingsApi.setCustomFormatEnabled(draft.id, draft.enabled, draft.revision)
          : await settingsApi.updateCustomFormat(draft.id, { ...draft, expectedRevision: draft.revision })
        : await settingsApi.createCustomFormat(draft)
      if (editorSectionId) {
        const alreadyInDestination = sections.find((section) => section.id === editorSectionId)?.formatIds.includes(saved.id) ?? false
        const nextLayout = sections.map((section) => ({
          ...section,
          formatIds: section.id === editorSectionId
            ? alreadyInDestination
              ? section.formatIds
              : [...section.formatIds.filter((id) => id !== saved.id), saved.id]
            : section.formatIds.filter((id) => id !== saved.id),
        }))
        await settingsApi.saveCustomFormatLayout(nextLayout)
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
    if (!await confirm({
      title: `Delete "${draft.name}"?`,
      body: 'Quality Profiles using this format lose its score. No media or existing releases change.',
      confirmLabel: 'Delete format',
    })) return
    setSaving(true); setError(''); setMessage('')
    try {
      await settingsApi.deleteCustomFormat(draft.id)
      await loadFormats()
      setEditorOpen(false)
      setMessage('Custom Format deleted.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete the Custom Format.') }
    finally { setSaving(false) }
  }

  const testOne = async () => {
    setTesting(true); setError(''); setTestAllResult(null)
    try { setTestResult(await settingsApi.testCustomFormat(draft, releaseName, testIndexer || undefined)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not test the Custom Format.') }
    finally { setTesting(false) }
  }

  const testAll = async () => {
    setTesting(true); setError(''); setTestResult(null)
    try { setTestAllResult(await settingsApi.testAllCustomFormats(releaseName, draft, testIndexer || undefined, testProfileId || undefined)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not test all Custom Formats.') }
    finally { setTesting(false) }
  }

  const exportOne = async () => {
    if (!draft.id) { setError('Save this Custom Format before exporting it.'); return }
    try { formatJsonDownload(`${draft.name.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toLowerCase() || 'custom-format'}.json`, await settingsApi.exportCustomFormat(draft.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not export the Custom Format.') }
  }

  const exportAll = async () => {
    try { formatJsonDownload('medialogue-custom-formats.json', await settingsApi.exportCustomFormats()) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not export Custom Formats.') }
  }

  const importBundle = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setError(''); setMessage('')
    try {
      const raw: unknown = JSON.parse(await file.text())
      const result = await settingsApi.importCustomFormats(parseCustomFormatImportBundle(raw))
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
      || format.conditions.some((condition) => (condition.name ?? '').toLowerCase().includes(term) || conditionValue(condition).toLowerCase().includes(term)))
  }, [formats, filter])

  const closeEditor = () => { setEditorOpen(false); setSelectedId(''); setTestResult(null); setTestAllResult(null) }
  const hasEmptyRule = draft.conditions.some((condition) => !conditionValue(condition).trim())

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
      const saved = await settingsApi.setCustomFormatEnabled(format.id, enabled, format.revision)
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
      const saved = await settingsApi.saveCustomFormatLayout(sectionDraft.map((section) => ({ ...section, name: section.name.trim() })))
      setSections(saved); setOrganizerOpen(false); setMessage('Custom Format organization saved.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save Custom Format organization.') }
    finally { setSaving(false) }
  }

  return <div className="page">
    <PageTopbar title="Custom Formats" subtitle="Explainable release matching rules with profile scores and optional per-match offsets." />
    <input ref={importInput} className="cf-file-input" type="file" accept="application/json,.json" onChange={(event) => void importBundle(event)} />

    {error && <div className="cf-banner cf-banner-error"><Icon name="alert" size={15} /><span>{error}</span></div>}
    {message && <div className="cf-banner cf-banner-success"><Icon name="check" size={15} /><span>{message}</span></div>}

    <div className="toolbar">
      <div className="search-field"><Icon name="search" size={16} /><Input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={`Search ${formats.length} Custom Format${formats.length === 1 ? '' : 's'}…`} /></div>
      <Button variant="primary" icon="plus" onClick={newFormat}>New custom format</Button>
      <div className="toolbar-spacer" />
      <Button variant="ghost" icon="sliders" onClick={openOrganizer}>Organize sections</Button>
      <Button variant="ghost" icon="archive" onClick={() => importInput.current?.click()}>Import</Button>
      <Button variant="ghost" icon="download" onClick={() => void exportAll()} disabled={!formats.length}>Export all</Button>
    </div>

    {loading ? <div className="cf-list-state">Loading Custom Formats…</div>
      : !formats.length ? <EmptyState icon="custom-format" title="No Custom Formats yet" detail="A Custom Format is a named set of conditions that a release either matches or does not. Scores live on Quality Profiles." action={<Button variant="primary" icon="plus" onClick={newFormat}>Create the first one</Button>} />
      : !visible.length ? <EmptyState icon="search" title="No formats match that filter" detail="Nothing here matches the text you typed. Clear the filter to see every format." />
      : <div className="cf-sections">{sections.map((section) => {
          const sectionFormats = section.formatIds.map((id) => visible.find((format) => format.id === id)).filter((format): format is CustomFormat => Boolean(format))
          if (filter.trim() && !sectionFormats.length) return null
          return <section className="cf-section" key={section.id}>
            <div className="cf-section-head"><div><h2>{section.name}</h2><span>{sectionFormats.length} format{sectionFormats.length === 1 ? '' : 's'}</span></div></div>
            {sectionFormats.length ? <div className="cf-card-grid">{sectionFormats.map((format) => <FormatCard key={format.id} format={format} toggling={togglingIds.has(format.id)} onToggle={() => void toggleFormat(format)} onOpen={() => chooseFormat(format)} onDuplicate={() => void duplicateFormatNow(format)} />)}</div> : <div className="cf-section-empty">No formats in this section.</div>}
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
      title={draft.name || 'Untitled Custom Format'}
      onClose={closeEditor}
      titleAction={<EnabledButton enabled={draft.enabled} onChange={(next) => setDraft((current) => ({ ...current, enabled: next }))} />}
      footer={<>
        {draft.id && !draft.builtin ? <Button variant="danger" onClick={() => void remove()} disabled={saving}>Delete</Button> : null}
        {draft.id && !draft.builtin ? <Button variant="ghost" icon="download" onClick={() => void exportOne()} disabled={saving}>Export</Button> : null}
        <span className="modal-foot-spacer" />
        <Button variant="secondary" onClick={closeEditor} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={() => void save()} disabled={saving || !dirty || !draft.name.trim() || !draft.conditions.length || hasEmptyRule}>{saving ? 'Saving…' : 'Save format'}</Button>
      </>}
    >
      <div className="cf-editor-shell">
        <aside className="cf-editor-rail">
          <nav aria-label="Custom Format editor sections">
            <button className={editorView === 'details' ? 'active' : ''} onClick={() => setEditorView('details')}><span>1</span><div><strong>Details</strong><small>Name, section and takeovers</small></div><Icon name="chevron" size={14} /></button>
            <button className={editorView === 'conditions' ? 'active' : ''} onClick={() => setEditorView('conditions')}><span>2</span><div><strong>Match rules</strong><small>{draft.conditions.length} configured</small></div><Icon name="chevron" size={14} /></button>
            <button className={editorView === 'test' ? 'active' : ''} onClick={() => setEditorView('test')}><span>3</span><div><strong>Test</strong><small>Try a real release title</small></div><Icon name="chevron" size={14} /></button>
          </nav>
          {draft.builtin && <div className="cf-editor-owned"><Icon name="lock" size={15} /><span>Maintained by Medialogue</span></div>}
        </aside>

        <div className="cf-editor-content">
          {editorView === 'details' && <section className="cf-editor-panel">
            <div className="cf-editor-panel-head"><h3>Describe this format</h3><p>Give the rule set a clear purpose and choose where it belongs.</p></div>
            <div className="cf-editor-fields">
              <SettingsGroup>
                <Field label="Format name" help="Shown on cards, Quality Profiles and evaluation results.">
                  <Input value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} placeholder="e.g. Dolby Vision with HDR10 fallback" readOnly={draft.builtin} autoFocus={!draft.id} />
                </Field>
                <Field label="Section" help="The header this format appears under on the Custom Formats page.">
                  <Select value={editorSectionId} onChange={(event) => setEditorSectionId(event.target.value)}>
                    {sections.map((section) => <option value={section.id} key={section.id}>{section.name}</option>)}
                  </Select>
                </Field>
                <Field label="Description" wide help="Optional. Explains to anyone reading the card what this rule set is for.">
                  <textarea className="input cf-description-input" value={draft.description ?? ''} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} placeholder="e.g. Lossless audio commonly found in remuxes and high-quality releases." readOnly={draft.builtin} />
                </Field>
                <Field label="Supersedes" help="When this format matches, selected broader formats remain diagnostic matches but contribute no score." wide>
                  <div className="cf-supersedes-list">
                    {formats.filter((format) => format.id !== draft.id).map((format) => {
                      const checked = draft.supersedes.includes(format.id)
                      return <label className={checked ? 'selected' : ''} key={format.id}>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={draft.builtin}
                          onChange={() => setDraft((current) => ({
                            ...current,
                            supersedes: checked
                              ? current.supersedes.filter((id) => id !== format.id)
                              : [...current.supersedes, format.id],
                          }))}
                        />
                        <span>{format.name}</span>
                        {format.builtin && <small>Built-in</small>}
                      </label>
                    })}
                    {!formats.some((format) => format.id !== draft.id) && <span className="cf-supersedes-empty">No other Custom Formats are available.</span>}
                  </div>
                </Field>
              </SettingsGroup>
            </div>
            <div className="cf-editor-next"><Button variant="primary" onClick={() => setEditorView('conditions')}>Continue to match rules</Button></div>
          </section>}

          {editorView === 'conditions' && <section className="cf-editor-panel cf-editor-rules-panel">
            <div className="cf-editor-panel-head cf-editor-panel-head-compact"><h3>Match rules</h3><p>New rules run against the full release title.</p></div>
            {draft.builtin && <div className="settings-note"><Icon name="lock" size={15} /><span>Built-in rules are read-only so matching improvements can arrive automatically. Duplicate this format if you need your own variation.</span></div>}
            {!draft.builtin && <div className="cf-add-rule"><div><strong>Add a regex rule</strong><span>Match text anywhere in the full release title.</span></div><Button variant="secondary" icon="plus" onClick={() => setDraft((current) => ({ ...current, conditions: [...current.conditions, { ...freshCondition(), name: `Rule ${current.conditions.length + 1}` }] }))}>Add rule</Button></div>}
            {draft.conditions.length ? <div className="cf-condition-list">
              {draft.conditions.map((condition, index) => <div className="cf-condition-card cf-rule-card" key={condition.id}>
                <div className="condition-top">
                  <div className="cf-rule-number"><span>{index + 1}</span><div>{draft.builtin ? <strong>{condition.name || `Rule ${index + 1}`}</strong> : <Input className="cf-rule-title-input" aria-label={`Rule ${index + 1} name`} value={condition.name ?? ''} onChange={(event) => updateCondition(condition.id, { name: event.target.value || undefined })} placeholder={`Rule ${index + 1}`} />}</div></div>
                  <div className="cf-rule-header-actions">
                    <div className="cf-rule-header-options" aria-label={`Rule ${index + 1} options`}>
                      <Tooltip asChild content="Every required rule must pass for this format to match."><button type="button" className={condition.required ? 'active' : ''} aria-pressed={condition.required} onClick={() => updateCondition(condition.id, { required: !condition.required })} disabled={draft.builtin}>Required</button></Tooltip>
                      <Tooltip asChild content="Pass this rule only when the regular expression does not match."><button type="button" className={condition.negate ? 'active' : ''} aria-pressed={condition.negate} onClick={() => updateCondition(condition.id, { negate: !condition.negate })} disabled={draft.builtin}>Negate</button></Tooltip>
                      <Tooltip asChild content="Match uppercase and lowercase characters exactly."><button type="button" className={condition.caseSensitive ? 'active' : ''} aria-pressed={condition.caseSensitive} onClick={() => updateCondition(condition.id, { caseSensitive: !condition.caseSensitive })} disabled={draft.builtin}>Case sensitive</button></Tooltip>
                    </div>
                    {!draft.builtin && <button className="icon-button" aria-label={`Remove rule ${index + 1}`} onClick={() => setDraft((current) => ({ ...current, conditions: current.conditions.filter((item) => item.id !== condition.id) }))}><Icon name="close" size={15} /></button>}
                  </div>
                </div>
                <div className="cf-rule-fields">
                  <label className="cf-rule-value"><span>Regular expression</span><Input value={conditionValue(condition)} onChange={(event) => updateCondition(condition.id, { pattern: event.target.value })} placeholder="\\b(remux|bdremux)\\b" readOnly={draft.builtin} /></label>
                  <label className="cf-rule-offset"><span>Score offset</span><Input type="number" value={condition.scoreOffset} min={-100000} max={100000} onChange={(event) => updateCondition(condition.id, { scoreOffset: Number(event.target.value) || 0 })} readOnly={draft.builtin} /></label>
                </div>
              </div>)}
            </div> : <div className="cf-test-placeholder"><Icon name="custom-format" size={18} /><strong>No match rules yet</strong><span>Add the first regex rule to start matching release titles.</span></div>}
            <div className="cf-editor-next"><Button variant="secondary" onClick={() => setEditorView('details')}>Back</Button><Button variant="primary" onClick={() => setEditorView('test')} disabled={!draft.conditions.length}>Continue to test</Button></div>
          </section>}

          {editorView === 'test' && <section className="cf-editor-panel">
            <div className="cf-editor-panel-head"><h3>Try a real release title</h3><p>Testing uses the unsaved rules currently in this editor and does not change your library.</p></div>
            <div className="cf-editor-fields">
              <SettingsGroup>
                <Field label="Release title" wide help="Pasted exactly as the indexer reports it. Rules run against this whole string.">
                  <textarea className="input cf-release-test-textarea" value={releaseName} onChange={(event) => setReleaseName(event.target.value)} placeholder="Paste a full release title…" />
                </Field>
              </SettingsGroup>
            </div>
            <div className="cf-test-actions"><Button variant="primary" icon="activity" onClick={() => void testOne()} disabled={testing || !releaseName.trim() || !draft.conditions.length || hasEmptyRule}>{testing ? 'Testing…' : 'Test this format'}</Button><Button variant="secondary" onClick={() => void testAll()} disabled={testing || !releaseName.trim() || !draft.conditions.length || hasEmptyRule}>Compare every format</Button><div className="toolbar-spacer" /><Select value={testProfileId} onChange={(event) => setTestProfileId(event.target.value)}><option value="">No profile scoring</option>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</Select></div>
            {!testResult && !testAllResult && <div className="cf-test-placeholder"><Icon name="activity" size={18} /><strong>Ready to test</strong><span>Run the title above to see every rule’s evidence and result.</span></div>}
            {testResult?.evaluation && <EvaluationRows evaluation={testResult.evaluation} />}
            {testAllResult && <div className="cf-test-all"><div className="cf-test-all-head"><span>Every format evaluated{testAllResult.qualityProfileName ? ` against ${testAllResult.qualityProfileName}` : ''}</span><span>{testAllResult.matchedCount} MATCHED{testAllResult.totalScore !== undefined ? ` · ${testAllResult.totalScore > 0 ? '+' : ''}${testAllResult.totalScore}` : ''}</span></div>{testAllResult.formats.map((evaluation) => <div className={`cf-test-all-row ${evaluation.rawMatched && !evaluation.matched ? 'suppressed' : ''}`} key={evaluation.customFormatId}><Icon name={evaluation.matched ? 'check' : evaluation.rawMatched ? 'pause' : 'close'} size={14} /><span>{evaluation.customFormatName}{evaluation.rawMatched && !evaluation.matched ? ` · suppressed by ${evaluation.suppressedByNames.join(', ')}` : ''}</span><small>{evaluation.contribution === undefined ? '-' : `${evaluation.contribution > 0 ? '+' : ''}${evaluation.contribution}`}</small></div>)}</div>}
            {testResult?.parsed && <details className="cf-parser-details"><summary>View parser output</summary><pre>{JSON.stringify(testResult.parsed, null, 2)}</pre></details>}
            <div className="cf-editor-next"><Button variant="secondary" onClick={() => setEditorView('conditions')}>Back to rules</Button></div>
          </section>}
        </div>
      </div>
    </Modal>}
  </div>
}

function FormatCard({ format, toggling, onToggle, onOpen, onDuplicate }: { format: CustomFormat; toggling: boolean; onToggle: () => void; onOpen: () => void; onDuplicate: () => void }) {
  return <article className={`cf-card ${format.enabled ? '' : 'cf-card-off'}`} role="button" tabIndex={0} onClick={onOpen} onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onOpen() } }}>
    <div className="cf-card-head">
      <strong>{format.name}</strong>
      <span className="cf-card-badges">
        {format.builtin && <Badge tone="blue">Built-in</Badge>}
        <button type="button" className={`enabled-button cf-card-toggle ${format.enabled ? 'on' : 'off'}`} aria-pressed={format.enabled} aria-label={`Turn ${format.name} ${format.enabled ? 'off' : 'on'}`} disabled={toggling} onClick={(event) => { event.stopPropagation(); onToggle() }}>{toggling ? 'Saving…' : format.enabled ? 'On' : 'Off'}</button>
        <Tooltip asChild content="Create a copy of this format"><button
          type="button"
          className="badge cf-card-duplicate"
          aria-label={`Create a copy of ${format.name}`}
          onClick={(event) => { event.stopPropagation(); onDuplicate() }}
        ><Icon name="copy" size={13} /></button></Tooltip>
      </span>
    </div>
    <p className="cf-card-description">{format.description || 'No description provided.'}</p>
    <div className="cf-card-conditions cf-card-rules">
      {format.conditions.slice(0, 6).map((condition, index) => <span className="cf-condition-chip" key={condition.id}>
        {condition.name || `Rule ${index + 1}`}
      </span>)}
      {format.conditions.length > 6 && <span className="cf-condition-chip cf-condition-more">+{format.conditions.length - 6} more</span>}
      {format.supersedes.length > 0 && <span className="cf-condition-chip"><em>Supersedes</em>{format.supersedes.length}</span>}
    </div>
  </article>
}
