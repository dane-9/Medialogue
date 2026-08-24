import { describe, expect, it } from 'vitest'
import { badgeColor, luminance } from './badgeColor'

const rgbOf = (value: string): [number, number, number] => {
  const [r, g, b] = value.match(/\d+/g)!.slice(0, 3).map(Number)
  return [r, g, b]
}

describe('badgeColor', () => {
  it('keeps a colour that is already legible', () => {
    const badge = badgeColor('#7C8CF8')!
    expect(badge.text).toBe('rgb(124, 140, 248)')
  })

  it('derives border and background from the same hue', () => {
    const badge = badgeColor('#7C8CF8')!
    expect(badge.border).toBe('rgba(124, 140, 248, 0.38)')
    expect(badge.background).toBe('rgba(124, 140, 248, 0.12)')
  })

  it('lightens a colour too dark to read on the dark theme', () => {
    const badge = badgeColor('#000000')!
    expect(luminance(rgbOf(badge.text))).toBeGreaterThanOrEqual(0.28)
  })

  it('lightens every dark hue it is given, keeping none unreadable', () => {
    for (const dark of ['#000000', '#101010', '#0a1f0a', '#1a0033', '#003366', '#220000']) {
      const badge = badgeColor(dark)!
      expect(luminance(rgbOf(badge.text)), dark).toBeGreaterThanOrEqual(0.28)
    }
  })

  it('preserves the chosen hue while lightening it', () => {
    // A dark red must brighten into a light red, not wash out to grey.
    const [r, g, b] = rgbOf(badgeColor('#330000')!.text)
    expect(r).toBeGreaterThan(g)
    expect(r).toBeGreaterThan(b)
  })

  it('accepts a hex with or without the leading hash, in any case', () => {
    expect(badgeColor('#ff0000')!.text).toBe(badgeColor('FF0000')!.text)
  })

  it('returns null for anything it cannot use, so the default badge applies', () => {
    expect(badgeColor(null)).toBeNull()
    expect(badgeColor(undefined)).toBeNull()
    expect(badgeColor('')).toBeNull()
    expect(badgeColor('red')).toBeNull()
    expect(badgeColor('#fff')).toBeNull()
    expect(badgeColor('#12345g')).toBeNull()
  })
})
