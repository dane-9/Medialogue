/**
 * Custom filters for the Movies and Shows lists, as Radarr has them.
 *
 * A filter is a list of conditions. Every condition must match; a condition
 * with several values matches when any of them does. The lists filter what they
 * already loaded, so a condition is applied here against the loaded item.
 */

export type FilterPredicate =
  | 'equal' | 'notEqual'
  | 'contains' | 'notContains'
  | 'startsWith' | 'notStartsWith' | 'endsWith' | 'notEndsWith'
  | 'greaterThan' | 'greaterThanOrEqual' | 'lessThan' | 'lessThanOrEqual'
  | 'inLast' | 'notInLast'

export type FilterValue = string | number | boolean

export interface FilterCondition {
  key: string
  type: FilterPredicate
  value: FilterValue[]
}

export interface CustomFilter {
  id: string
  mediaType: 'movies' | 'shows'
  label: string
  filters: FilterCondition[]
}

/** What a field holds, which decides the predicates it offers and how its value is entered. */
export type FieldKind = 'string' | 'number' | 'bool' | 'exact' | 'array' | 'date'

export interface FilterField<T> {
  key: string
  label: string
  kind: FieldKind
  /** Choices an exact or array field always offers; values the loaded items take are added. */
  choices?: string[]
  get: (item: T) => FilterValue | FilterValue[] | undefined | null
}

export const PREDICATES: Record<FieldKind, Array<{ type: FilterPredicate; label: string }>> = {
  string: [
    { type: 'contains', label: 'contains' },
    { type: 'notContains', label: 'does not contain' },
    { type: 'equal', label: 'is' },
    { type: 'notEqual', label: 'is not' },
    { type: 'startsWith', label: 'starts with' },
    { type: 'notStartsWith', label: 'does not start with' },
    { type: 'endsWith', label: 'ends with' },
    { type: 'notEndsWith', label: 'does not end with' },
  ],
  number: [
    { type: 'equal', label: 'equals' },
    { type: 'notEqual', label: 'does not equal' },
    { type: 'greaterThan', label: 'is greater than' },
    { type: 'greaterThanOrEqual', label: 'is greater than or equal to' },
    { type: 'lessThan', label: 'is less than' },
    { type: 'lessThanOrEqual', label: 'is less than or equal to' },
  ],
  bool: [
    { type: 'equal', label: 'is' },
  ],
  exact: [
    { type: 'equal', label: 'is' },
    { type: 'notEqual', label: 'is not' },
  ],
  array: [
    { type: 'contains', label: 'contains' },
    { type: 'notContains', label: 'does not contain' },
  ],
  date: [
    { type: 'lessThan', label: 'is before' },
    { type: 'greaterThan', label: 'is after' },
    { type: 'inLast', label: 'is in the last' },
    { type: 'notInLast', label: 'is not in the last' },
  ],
}

const DAY = 24 * 60 * 60 * 1000
const text = (value: unknown) => String(value ?? '').toLowerCase()

/** A field's fixed choices plus any other value the loaded items take, for its choice list. */
export function fieldChoices<T>(field: FilterField<T>, items: T[]): string[] {
  const seen = new Set<string>(field.choices ?? [])
  for (const item of items) {
    const raw = field.get(item)
    for (const value of Array.isArray(raw) ? raw : [raw]) if (value !== undefined && value !== null && value !== '') seen.add(String(value))
  }
  return [...seen].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
}

function matchesOne(kind: FieldKind, type: FilterPredicate, actual: FilterValue | FilterValue[] | undefined | null, wanted: FilterValue, now: number): boolean {
  if (kind === 'array') {
    const values = (Array.isArray(actual) ? actual : actual == null ? [] : [actual]).map(text)
    const has = values.includes(text(wanted))
    return type === 'notContains' ? !has : has
  }
  if (kind === 'date') {
    const at = actual ? Date.parse(String(actual)) : NaN
    if (type === 'inLast' || type === 'notInLast') {
      const inside = !Number.isNaN(at) && at >= now - Number(wanted) * DAY && at <= now
      return type === 'inLast' ? inside : !inside
    }
    const limit = Date.parse(String(wanted))
    if (Number.isNaN(at) || Number.isNaN(limit)) return false
    return type === 'lessThan' ? at < limit : at > limit
  }
  if (kind === 'number') {
    if (actual === undefined || actual === null || actual === '') return type === 'notEqual'
    const left = Number(actual)
    const right = Number(wanted)
    switch (type) {
      case 'equal': return left === right
      case 'notEqual': return left !== right
      case 'greaterThan': return left > right
      case 'greaterThanOrEqual': return left >= right
      case 'lessThan': return left < right
      case 'lessThanOrEqual': return left <= right
      default: return false
    }
  }
  if (kind === 'bool') return Boolean(actual) === (wanted === true || wanted === 'true')
  const left = text(actual)
  const right = text(wanted)
  switch (type) {
    case 'equal': return left === right
    case 'notEqual': return left !== right
    case 'contains': return left.includes(right)
    case 'notContains': return !left.includes(right)
    case 'startsWith': return left.startsWith(right)
    case 'notStartsWith': return !left.startsWith(right)
    case 'endsWith': return left.endsWith(right)
    case 'notEndsWith': return !left.endsWith(right)
    default: return false
  }
}

/** Negative predicates must hold for every value; the rest for any of them. */
const NEGATIVE: FilterPredicate[] = ['notEqual', 'notContains', 'notStartsWith', 'notEndsWith', 'notInLast']

export function matchesCondition<T>(item: T, condition: FilterCondition, fields: FilterField<T>[], now = Date.now()): boolean {
  const field = fields.find((candidate) => candidate.key === condition.key)
  // A condition on a field this page no longer knows cannot narrow anything.
  if (!field || !condition.value.length) return true
  const actual = field.get(item)
  const check = (wanted: FilterValue) => matchesOne(field.kind, condition.type, actual, wanted, now)
  return NEGATIVE.includes(condition.type) ? condition.value.every(check) : condition.value.some(check)
}

export function matchesFilter<T>(item: T, conditions: FilterCondition[], fields: FilterField<T>[], now = Date.now()): boolean {
  return conditions.every((condition) => matchesCondition(item, condition, fields, now))
}

/** The value a fresh condition starts with for a field. */
export function defaultCondition<T>(field: FilterField<T>): FilterCondition {
  return { key: field.key, type: PREDICATES[field.kind][0].type, value: field.kind === 'bool' ? [true] : [] }
}
