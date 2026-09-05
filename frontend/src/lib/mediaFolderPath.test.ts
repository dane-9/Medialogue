import { describe, expect, it } from 'vitest'

import { mediaFolderPath } from '../api/mappers/downloads'

const WIN = 'D:\\Media\\Movies\\Thing (2024)'

describe('mediaFolderPath', () => {
  it('trims a media file back to its folder', () => {
    expect(mediaFolderPath('//TRUENAS/Movies/TEST/Catwoman 2004 Remux/Catwoman 2004 Remux.mkv'))
      .toBe('//TRUENAS/Movies/TEST/Catwoman 2004 Remux')
  })

  it('keeps a folder whose name merely contains dots', () => {
    expect(mediaFolderPath('/movies/Some.Movie.2024.1080p.BluRay.x264'))
      .toBe('/movies/Some.Movie.2024.1080p.BluRay.x264')
  })

  it('leaves a season folder alone', () => {
    expect(mediaFolderPath('/shows/Northwind/Season 02')).toBe('/shows/Northwind/Season 02')
  })

  it('trims an episode file back to its season folder', () => {
    expect(mediaFolderPath('/shows/Northwind/Season 02/Northwind.S02E01.1080p.mkv'))
      .toBe('/shows/Northwind/Season 02')
  })

  it('handles windows separators and a trailing slash', () => {
    expect(mediaFolderPath(`${WIN}\\`)).toBe(WIN)
    expect(mediaFolderPath(`${WIN}\\Thing.2024.mkv`)).toBe(WIN)
  })

  it('falls back when there is no path', () => {
    expect(mediaFolderPath(undefined)).toBe('Unknown path')
    expect(mediaFolderPath('')).toBe('Unknown path')
  })
})
