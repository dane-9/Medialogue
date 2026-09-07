import type { CustomFormatConditionType } from '../types'

/** Reader-facing name for the parsed field a condition's pattern runs against.
    Mirrors the labels the evaluator uses server-side, so the tester and the
    backend's own reasons describe the same field the same way. */
const LABELS: Record<CustomFormatConditionType, string> = {
  release_title: 'release title',
  release_group: 'release group',
  quality: 'quality',
  quality_modifier: 'quality modifier',
  resolution: 'resolution',
  source: 'source',
  edition: 'edition',
  language: 'language',
  indexer: 'indexer',
  web_provider: 'WEB provider',
  video_codec: 'video codec',
  audio_codec: 'audio codec',
  audio_channels: 'audio channels',
  hdr_type: 'HDR type',
  release_attribute: 'release attribute',
}

export function conditionTypeLabel(type?: CustomFormatConditionType | string): string {
  if (!type) return LABELS.release_title
  // An unknown type means the server added a field this build predates. Showing
  // the raw key beats claiming the pattern read the release title.
  return LABELS[type as CustomFormatConditionType] ?? String(type).replaceAll('_', ' ')
}
