import { describe, expect, it } from 'vitest'
import { parserWarningMessage } from './parserWarnings'

describe('parserWarningMessage', () => {
  it('explains every parser warning code shown on search results', () => {
    expect(parserWarningMessage('release_group_inferred_nogroup')).toBe(
      'No release group was identified; treated as NoGroup.',
    )
    expect(parserWarningMessage('quality_not_detected')).toBe('Quality could not be detected.')
    expect(parserWarningMessage('season_episode_boundary_ambiguous')).toBe(
      'Season or episode numbering could not be parsed confidently.',
    )
  })

  it('preserves warnings that are already written for users', () => {
    expect(parserWarningMessage('No downloadable torrent URL was supplied by the indexer.')).toBe(
      'No downloadable torrent URL was supplied by the indexer.',
    )
  })

  it('humanizes an unknown internal warning code as a safe fallback', () => {
    expect(parserWarningMessage('future_parser_warning')).toBe('Future parser warning.')
  })
})
