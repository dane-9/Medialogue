import { useCallback, useEffect, useMemo, useState } from 'react'
import { customFiltersApi } from '../api/modules/customFilters'
import { PREDICATES, defaultCondition, fieldChoices, matchesFilter, type CustomFilter, type FilterCondition, type FilterField, type FilterValue } from '../lib/customFilters'
import { useConfirm } from './Confirm'
import { Dropdown, MultiDropdown } from './Dropdown'
import { Icon } from './Icon'
import { Modal } from './Modal'
import { Button, Input, Select } from './ui'

const CUSTOM = 'custom:'
const MANAGE = '__manage'

/** The saved filters of one list, and the predicate for whichever one is selected. */
export function useCustomFilters<T>(mediaType: 'movies' | 'shows', fields: FilterField<T>[], selected: string) {
  const [filters, setFilters] = useState<CustomFilter[]>([])
  const reload = useCallback(() => customFiltersApi.list(mediaType).then(setFilters).catch(() => undefined), [mediaType])
  useEffect(() => { void reload() }, [reload])
  const active = selected.startsWith(CUSTOM) ? filters.find((filter) => filter.id === selected.slice(CUSTOM.length)) : undefined
  const matches = useCallback((item: T) => !active || matchesFilter(item, active.filters, fields), [active, fields])
  return { filters, setFilters, active, isCustom: selected.startsWith(CUSTOM), matches }
}

/** The list's Filter menu: the built-in filters, then the saved ones, then a way to edit them. */
export function LibraryFilterMenu({ ariaLabel, value, builtIn, custom, onChange, onManage }: {
  ariaLabel: string
  value: string
  builtIn: string[]
  custom: CustomFilter[]
  onChange: (value: string) => void
  onManage: () => void
}) {
  const options = [
    ...builtIn.map((option) => ({ value: option, label: option })),
    ...custom.map((filter) => ({ value: CUSTOM + filter.id, label: filter.label, group: 'Custom filters' })),
    { value: MANAGE, label: custom.length ? 'Edit custom filters…' : 'Add a custom filter…', group: 'Custom filters' },
  ]
  return <Dropdown
    className="select library-control"
    ariaLabel={ariaLabel}
    selectedPrefix="FILTER"
    value={value}
    options={options}
    onChange={(next) => (next === MANAGE ? onManage() : onChange(next))}
  />
}

/** Lists a page's saved filters with edit and delete, and opens the builder for a new one. */
export function CustomFiltersModal<T>({ mediaType, noun, filters, fields, items, onChanged, onApply, onClose }: {
  mediaType: 'movies' | 'shows'
  noun: string
  filters: CustomFilter[]
  fields: FilterField<T>[]
  items: T[]
  onChanged: (filters: CustomFilter[]) => void
  /** Select a filter, as the menu would, once it is saved. */
  onApply: (value: string) => void
  onClose: () => void
}) {
  const confirm = useConfirm()
  const [editing, setEditing] = useState<CustomFilter | 'new' | null>(filters.length ? null : 'new')
  const [error, setError] = useState('')

  const remove = async (filter: CustomFilter) => {
    if (!await confirm({ title: `Delete “${filter.label}”?`, body: 'The filter is removed for good. No titles are changed.', confirmLabel: 'Delete', tone: 'danger' })) return
    try { await customFiltersApi.remove(filter.id); onChanged(filters.filter((row) => row.id !== filter.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete the filter.') }
  }

  if (editing) return <FilterBuilderModal
    mediaType={mediaType}
    noun={noun}
    filter={editing === 'new' ? undefined : editing}
    fields={fields}
    items={items}
    onSaved={(saved) => {
      onChanged([...filters.filter((row) => row.id !== saved.id), saved].sort((a, b) => a.label.localeCompare(b.label)))
      onApply(CUSTOM + saved.id)
      onClose()
    }}
    onClose={() => (filters.length ? setEditing(null) : onClose())}
  />

  return <Modal title="Custom filters" eyebrow={noun.toUpperCase()} icon="sliders" onClose={onClose} footer={<>
    <span className="modal-foot-spacer" />
    <Button variant="ghost" onClick={onClose}>Close</Button>
    <Button variant="primary" icon="plus" onClick={() => setEditing('new')}>Add custom filter</Button>
  </>}>
    <div className="custom-filter-list">
      {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
      {filters.map((filter) => <div className="custom-filter-row" key={filter.id}>
        <div><strong>{filter.label}</strong><span>{filter.filters.length} condition{filter.filters.length === 1 ? '' : 's'}</span></div>
        <Button variant="ghost" onClick={() => setEditing(filter)}>Edit</Button>
        <Button variant="ghost" onClick={() => void remove(filter)}>Delete</Button>
      </div>)}
    </div>
  </Modal>
}

function FilterBuilderModal<T>({ mediaType, noun, filter, fields, items, onSaved, onClose }: {
  mediaType: 'movies' | 'shows'
  noun: string
  filter?: CustomFilter
  fields: FilterField<T>[]
  items: T[]
  onSaved: (filter: CustomFilter) => void
  onClose: () => void
}) {
  const [label, setLabel] = useState(filter?.label ?? '')
  const [conditions, setConditions] = useState<FilterCondition[]>(() => filter?.filters.length ? filter.filters : [defaultCondition(fields[0])])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const complete = conditions.every((condition) => condition.value.length > 0)
  const matching = useMemo(() => complete ? items.filter((item) => matchesFilter(item, conditions, fields)).length : undefined, [complete, items, conditions, fields])

  const update = (index: number, next: FilterCondition) => setConditions((rows) => rows.map((row, at) => (at === index ? next : row)))

  const save = async () => {
    setSaving(true); setError('')
    try {
      onSaved(filter
        ? await customFiltersApi.update(filter.id, mediaType, label.trim(), conditions)
        : await customFiltersApi.create(mediaType, label.trim(), conditions))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save the filter.')
      setSaving(false)
    }
  }

  return <Modal wide title={filter ? 'Edit custom filter' : 'Add custom filter'} eyebrow={noun.toUpperCase()} icon="sliders" onClose={onClose} footer={<>
    <span className="wizard-foot-count">{matching === undefined ? 'Fill in every condition' : `${matching} ${matching === 1 ? noun.replace(/s$/, '') : noun} match`}</span>
    <span className="modal-foot-spacer" />
    <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
    <Button variant="primary" disabled={!label.trim() || !complete || saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save'}</Button>
  </>}>
    <div className="filter-builder">
      <label><span className="field-label">Label</span><Input value={label} maxLength={128} autoFocus placeholder="e.g. Monitored 4K" onChange={(event) => setLabel(event.target.value)} /></label>
      <div className="filter-builder-rows">
        <span className="field-label">Matches every condition</span>
        {conditions.map((condition, index) => {
          const field = fields.find((candidate) => candidate.key === condition.key) ?? fields[0]
          return <div className="filter-builder-row" key={index}>
            <Select aria-label="Field" value={field.key} onChange={(event) => update(index, defaultCondition(fields.find((candidate) => candidate.key === event.target.value) ?? fields[0]))}>
              {fields.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
            </Select>
            <Select aria-label="Condition" value={condition.type} onChange={(event) => update(index, { ...condition, type: event.target.value as FilterCondition['type'], value: field.kind === 'date' ? [] : condition.value })}>
              {PREDICATES[field.kind].map((option) => <option key={option.type} value={option.type}>{option.label}</option>)}
            </Select>
            <ConditionValue field={field} condition={condition} items={items} onChange={(value) => update(index, { ...condition, value })} />
            <Button variant="ghost" icon="close" aria-label="Remove condition" disabled={conditions.length === 1} onClick={() => setConditions((rows) => rows.filter((_, at) => at !== index))} />
          </div>
        })}
        <div><Button variant="secondary" icon="plus" onClick={() => setConditions((rows) => [...rows, defaultCondition(fields[0])])}>Add condition</Button></div>
      </div>
      {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
    </div>
  </Modal>
}

function ConditionValue<T>({ field, condition, items, onChange }: {
  field: FilterField<T>
  condition: FilterCondition
  items: T[]
  onChange: (value: FilterValue[]) => void
}) {
  const single = condition.value[0]
  if (field.kind === 'bool') {
    return <Select aria-label="Value" value={single === false ? 'false' : 'true'} onChange={(event) => onChange([event.target.value === 'true'])}>
      <option value="true">Yes</option><option value="false">No</option>
    </Select>
  }
  if (field.kind === 'exact' || field.kind === 'array') {
    const choices = fieldChoices(field, items)
    if (!choices.length) return <span className="filter-builder-empty">No values in this list yet</span>
    // Several values: the condition matches any of them.
    return <MultiDropdown ariaLabel="Values" placeholder="Pick one or more…" options={choices} values={condition.value.map(String)} onChange={onChange} />
  }
  const set = (raw: string, numeric: boolean) => onChange(raw === '' ? [] : [numeric ? Number(raw) : raw])
  if (field.kind === 'date' && (condition.type === 'inLast' || condition.type === 'notInLast')) {
    return <label className="filter-builder-days"><Input aria-label="Days" type="number" min={1} value={single === undefined ? '' : String(single)} onChange={(event) => set(event.target.value, true)} /><span>days</span></label>
  }
  if (field.kind === 'date') return <Input aria-label="Date" type="date" value={single === undefined ? '' : String(single)} onChange={(event) => set(event.target.value, false)} />
  if (field.kind === 'number') return <Input aria-label="Value" type="number" value={single === undefined ? '' : String(single)} onChange={(event) => set(event.target.value, true)} />
  return <Input aria-label="Value" value={single === undefined ? '' : String(single)} onChange={(event) => set(event.target.value, false)} />
}
