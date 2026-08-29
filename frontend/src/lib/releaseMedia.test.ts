import { describe, expect, it } from 'vitest'
import { releaseAudio, releaseFlags, releaseHdr, releaseVideoCodec } from './releaseMedia'

// Shaped like the backend's ReleaseParseResult.to_dict().
const remux = {
  hdr: { dolby_vision: true, hdr: false, hdr10: true, hdr10_plus: false, hlg: false, values: ['Dolby Vision', 'HDR10'] },
  video: { codec: 'HEVC', raw_codec_token: 'x265' },
  audio: { codec: 'TrueHD', channels: '7.1', atmos: true, dts_x: false },
  attributes: { values: ['Hybrid'], hybrid: true, repack: false, proper: false },
}

describe('releaseHdr', () => {
  it('reports the formats the parser found, in its order', () => {
    expect(releaseHdr(remux)).toEqual(['Dolby Vision', 'HDR10'])
  })

  it('is empty for SDR releases and for snapshots of another shape', () => {
    expect(releaseHdr({ hdr: { values: [] } })).toEqual([])
    expect(releaseHdr(undefined)).toEqual([])
    expect(releaseHdr({ hdr: 'HDR10' })).toEqual([])
    expect(releaseHdr({ hdr: { values: [1, '', 'HLG'] } })).toEqual(['HLG'])
  })
})

describe('releaseAudio', () => {
  it('reads as one label: codec, object audio, channels', () => {
    expect(releaseAudio(remux)).toBe('TrueHD Atmos 7.1')
  })

  it('names DTS:X the same way', () => {
    expect(releaseAudio({ audio: { codec: 'DTS-HD MA', channels: '5.1', dts_x: true } })).toBe('DTS-HD MA DTS:X 5.1')
  })

  it('falls back to the flat keys and skips what the parser did not find', () => {
    expect(releaseAudio({ audio_codec: 'AC3', channels: '5.1' })).toBe('AC3 5.1')
    expect(releaseAudio({ audio: { channels: '2.0' } })).toBe('2.0')
    expect(releaseAudio({ audio: {} })).toBeUndefined()
    expect(releaseAudio(undefined)).toBeUndefined()
  })
})

describe('releaseVideoCodec', () => {
  it('prefers the parsed codec over the flat key', () => {
    expect(releaseVideoCodec(remux)).toBe('HEVC')
    expect(releaseVideoCodec({ video_codec: 'AVC' })).toBe('AVC')
    expect(releaseVideoCodec({ video: { codec: '  ' }, video_codec: 'AVC' })).toBe('AVC')
    expect(releaseVideoCodec(undefined)).toBeUndefined()
  })
})

describe('releaseFlags', () => {
  it('surfaces only the flags that explain why a copy supersedes another', () => {
    expect(releaseFlags(remux)).toEqual(['Hybrid'])
    expect(releaseFlags({ attributes: { proper: true, repack: true, hybrid: true } })).toEqual(['Proper', 'Repack', 'Hybrid'])
  })

  it('ignores anything that is not an explicit true', () => {
    expect(releaseFlags({ attributes: { proper: 'yes', repack: 1 } })).toEqual([])
    expect(releaseFlags({ attributes: {} })).toEqual([])
    expect(releaseFlags(undefined)).toEqual([])
  })
})
