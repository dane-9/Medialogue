import type { MediaProfileSettings } from '../types'

export type ProfileFormState = {
  profileId: string
  minimumOverrideId: string
  minimumScoreOverride: string
}

const EMPTY: ProfileFormState = { profileId: '', minimumOverrideId: '', minimumScoreOverride: '' }

/** The form values a saved profile assignment corresponds to.
 *
 * Loading the form and deciding whether it is dirty both read this, so the two
 * cannot drift: a baseline derived separately from the loader is a Save button
 * that lights up on load, or never lights up at all.
 *
 * An inherited value is the empty string, which is what "Same as profile"
 * submits — a title only overrides what it explicitly sets.
 */
export function profileFormState(settings?: MediaProfileSettings | null): ProfileFormState {
  if (!settings) return EMPTY
  return {
    profileId: settings.qualityProfileInherited ? '' : settings.qualityProfileId ?? '',
    minimumOverrideId: settings.minimumQualityOverridden ? settings.minimumQuality?.id ?? '' : '',
    minimumScoreOverride: settings.minimumCustomFormatScoreOverridden && settings.minimumCustomFormatScore !== undefined
      ? String(settings.minimumCustomFormatScore)
      : '',
  }
}

export function profileFormChanged(settings: MediaProfileSettings | null | undefined, current: ProfileFormState): boolean {
  // Nothing is dirty until the saved state is known, or the button would be
  // live while the form is still empty and waiting on its first load.
  if (!settings) return false
  const saved = profileFormState(settings)
  return saved.profileId !== current.profileId
    || saved.minimumOverrideId !== current.minimumOverrideId
    || saved.minimumScoreOverride !== current.minimumScoreOverride
}
