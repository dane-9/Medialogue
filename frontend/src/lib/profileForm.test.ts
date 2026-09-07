import { describe, expect, it } from 'vitest'
import { profileFormChanged, profileFormState } from './profileForm'
import type { MediaProfileSettings } from '../types'

const settings = (patch: Partial<MediaProfileSettings> = {}): MediaProfileSettings => ({
  mediaType: 'movies',
  entityId: 'movie-1',
  qualityProfileInherited: true,
  minimumQualityOverridden: false,
  minimumCustomFormatScoreOverridden: false,
  customFormatScores: [],
  revision: 3,
  ...patch,
})

describe('profileFormState', () => {
  it('reads an inherited assignment as empty, which is what the form submits', () => {
    expect(profileFormState(settings({ qualityProfileId: 'profile-1' }))).toEqual({
      profileId: '',
      minimumOverrideId: '',
      minimumScoreOverride: '',
    })
  })

  it('reads values the title actually overrides', () => {
    expect(profileFormState(settings({
      qualityProfileInherited: false,
      qualityProfileId: 'profile-1',
      minimumQualityOverridden: true,
      minimumQuality: { id: 'q-1', name: '1080p Encode' } as MediaProfileSettings['minimumQuality'],
      minimumCustomFormatScoreOverridden: true,
      minimumCustomFormatScore: 40,
    }))).toEqual({ profileId: 'profile-1', minimumOverrideId: 'q-1', minimumScoreOverride: '40' })
  })

  it('keeps a zero minimum score, which is a real override', () => {
    expect(profileFormState(settings({
      minimumCustomFormatScoreOverridden: true,
      minimumCustomFormatScore: 0,
    })).minimumScoreOverride).toBe('0')
  })

  it('is empty before anything has loaded', () => {
    expect(profileFormState(null)).toEqual({ profileId: '', minimumOverrideId: '', minimumScoreOverride: '' })
  })
})

describe('profileFormChanged', () => {
  const pristine = { profileId: '', minimumOverrideId: '', minimumScoreOverride: '' }

  it('is false for a freshly loaded form', () => {
    expect(profileFormChanged(settings(), pristine)).toBe(false)
  })

  it('is false while the saved state is still unknown', () => {
    // Otherwise Save would be live over an empty form during the first load.
    expect(profileFormChanged(null, pristine)).toBe(false)
    expect(profileFormChanged(undefined, { ...pristine, profileId: 'profile-1' })).toBe(false)
  })

  it('notices each field changing', () => {
    expect(profileFormChanged(settings(), { ...pristine, profileId: 'profile-1' })).toBe(true)
    expect(profileFormChanged(settings(), { ...pristine, minimumOverrideId: 'q-1' })).toBe(true)
    expect(profileFormChanged(settings(), { ...pristine, minimumScoreOverride: '40' })).toBe(true)
  })

  it('notices an override being cleared back to the profile', () => {
    const saved = settings({ minimumCustomFormatScoreOverridden: true, minimumCustomFormatScore: 40 })
    expect(profileFormChanged(saved, { ...pristine, minimumScoreOverride: '40' })).toBe(false)
    expect(profileFormChanged(saved, pristine)).toBe(true)
  })
})
