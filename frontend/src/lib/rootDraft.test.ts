import { describe, expect, it } from 'vitest'
import { rootBrowseStart, rootDraftChanged, rootPathAfterBrowse } from './rootDraft'
import type { RootDraft } from './rootDraft'

const base: RootDraft = {
  name: 'Movies', path: '\\TRUENAS\Movies', mediaType: 'movies',
  accessMode: 'read_only', approveNewMedia: false, missingGraceChecks: 2,
}
const draft = (over: Partial<RootDraft> = {}): RootDraft => ({ ...base, ...over })

describe('rootDraftChanged', () => {
  it('is unchanged when nothing was edited', () => {
    expect(rootDraftChanged(draft(), base)).toBe(false)
  })

  it('is unchanged when the browser writes back the same path it opened at', () => {
    expect(rootDraftChanged(draft({ path: '\\TRUENAS\Movies' }), base)).toBe(false)
  })

  it('notices a navigated path', () => {
    expect(rootDraftChanged(draft({ path: '\\TRUENAS\Movies\Cartoons' }), base)).toBe(true)
  })

  it('notices each editable field', () => {
    expect(rootDraftChanged(draft({ name: 'Films' }), base)).toBe(true)
    expect(rootDraftChanged(draft({ mediaType: 'shows' }), base)).toBe(true)
    expect(rootDraftChanged(draft({ accessMode: 'read_write' }), base)).toBe(true)
    expect(rootDraftChanged(draft({ approveNewMedia: true }), base)).toBe(true)
    expect(rootDraftChanged(draft({ missingGraceChecks: 3 }), base)).toBe(true)
  })

  it('ignores surrounding whitespace, which saving trims anyway', () => {
    expect(rootDraftChanged(draft({ name: '  Movies  ' }), base)).toBe(false)
  })

  it('is unchanged before a form has been opened', () => {
    expect(rootDraftChanged(draft({ name: 'Anything' }), null)).toBe(false)
  })
})

describe('rootBrowseStart', () => {
  it('keeps POSIX, drive-letter, and UNC roots intact', () => {
    expect(rootBrowseStart('/media/movies')).toBe('/media/movies')
    expect(rootBrowseStart('D:\\Movies')).toBe('D:\\Movies')
    expect(rootBrowseStart('\\\\TRUENAS\\Movies')).toBe('\\\\TRUENAS\\Movies')
  })

  it('uses the filesystem top level for a non-absolute value', () => {
    expect(rootBrowseStart('movies')).toBe('')
  })
})

describe('rootPathAfterBrowse', () => {
  it('does not change an existing draft during its initial browse', () => {
    expect(rootPathAfterBrowse('\\\\TRUENAS\\Movies', '\\\\truenas\\Movies\\', false, false, true))
      .toBe('\\\\TRUENAS\\Movies')
  })

  it('updates the draft when the user navigates to another folder', () => {
    expect(rootPathAfterBrowse('/media', '/media/movies', false)).toBe('/media/movies')
  })

  it('preserves an existing path when browsing it fails', () => {
    expect(rootPathAfterBrowse('\\\\TRUENAS\\Movies', '', true, true, true)).toBe('\\\\TRUENAS\\Movies')
  })
})
