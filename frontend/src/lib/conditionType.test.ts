import { describe, expect, it } from 'vitest'
import { conditionTypeLabel } from './conditionType'

describe('conditionTypeLabel', () => {
  it('names the parsed field a pattern reads', () => {
    expect(conditionTypeLabel('audio_codec')).toBe('audio codec')
    expect(conditionTypeLabel('release_title')).toBe('release title')
    expect(conditionTypeLabel('hdr_type')).toBe('HDR type')
    expect(conditionTypeLabel('web_provider')).toBe('WEB provider')
  })

  it('falls back to the release title when no type is given', () => {
    expect(conditionTypeLabel(undefined)).toBe('release title')
    expect(conditionTypeLabel('')).toBe('release title')
  })

  it('humanises an unknown type rather than mislabelling it', () => {
    expect(conditionTypeLabel('audio_bitrate')).toBe('audio bitrate')
  })
})
