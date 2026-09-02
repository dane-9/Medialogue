import { describe, expect, it } from 'vitest'
import { parseCustomFormatImportBundle } from './customFormats'

const validBundle = {
  application: 'Medialogue',
  schema_version: 1,
  custom_formats: [{
    schema_version: 1,
    name: 'Prefer Proper',
    conditions: [{ pattern: 'PROPER' }],
  }],
}

describe('custom format import validation', () => {
  it('accepts a valid Medialogue bundle', () => {
    expect(parseCustomFormatImportBundle(validBundle).custom_formats[0]?.name).toBe('Prefer Proper')
  })

  it('rejects exports from another application', () => {
    expect(() => parseCustomFormatImportBundle({ ...validBundle, application: 'OtherApp' })).toThrow(/application/)
  })

  it('rejects unsupported schema versions', () => {
    expect(() => parseCustomFormatImportBundle({ ...validBundle, schema_version: 99 })).toThrow(/schema_version/)
  })

  it('requires the current schema marker on every exported format', () => {
    const { schema_version: _schemaVersion, ...formatWithoutVersion } = validBundle.custom_formats[0]
    expect(() => parseCustomFormatImportBundle({
      ...validBundle,
      custom_formats: [formatWithoutVersion],
    })).toThrow(/schema_version/)
  })

  it('rejects duplicate names case-insensitively', () => {
    expect(() => parseCustomFormatImportBundle({
      ...validBundle,
      custom_formats: [validBundle.custom_formats[0], { ...validBundle.custom_formats[0], name: 'prefer proper' }],
    })).toThrow(/Duplicate Custom Format name/)
  })

  it('rejects conditions without a pattern', () => {
    expect(() => parseCustomFormatImportBundle({
      ...validBundle,
      custom_formats: [{ schema_version: 1, name: 'Broken', conditions: [{}] }],
    })).toThrow(/require a regex pattern/)
  })
})
