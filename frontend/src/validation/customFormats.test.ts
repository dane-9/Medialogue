import { describe, expect, it } from 'vitest'
import { parseCustomFormatImportBundle } from './customFormats'

const validBundle = {
  application: 'Medialogue',
  schema_version: 1,
  custom_formats: [{
    name: 'Prefer Proper',
    conditions: [{ type: 'release_title', pattern: 'PROPER' }],
  }],
}

describe('custom format import validation', () => {
  it('accepts a valid Medialogue bundle', () => {
    expect(parseCustomFormatImportBundle(validBundle).custom_formats[0]?.name).toBe('Prefer Proper')
  })

  it('rejects exports from another application', () => {
    expect(() => parseCustomFormatImportBundle({ ...validBundle, application: 'OtherApp' })).toThrow(/not a Medialogue/)
  })

  it('rejects unsupported schema versions', () => {
    expect(() => parseCustomFormatImportBundle({ ...validBundle, schema_version: 99 })).toThrow(/not supported/)
  })

  it('rejects duplicate names case-insensitively', () => {
    expect(() => parseCustomFormatImportBundle({
      ...validBundle,
      custom_formats: [validBundle.custom_formats[0], { ...validBundle.custom_formats[0], name: 'prefer proper' }],
    })).toThrow(/Duplicate Custom Format name/)
  })

  it('rejects conditions with neither a pattern nor a legacy value', () => {
    expect(() => parseCustomFormatImportBundle({
      ...validBundle,
      custom_formats: [{ name: 'Broken', conditions: [{ type: 'release_title' }] }],
    })).toThrow(/require a regex pattern or legacy value/)
  })
})
