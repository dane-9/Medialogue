const PARSER_WARNING_MESSAGES: Record<string, string> = {
  season_episode_boundary_ambiguous: 'Season or episode numbering could not be parsed confidently.',
  quality_not_detected: 'Quality could not be detected.',
  release_group_inferred_nogroup: 'No release group was identified; treated as NoGroup.',
}

/** Convert parser-owned warning codes without rewriting already-readable warnings. */
export function parserWarningMessage(value: string): string {
  const warning = value.trim()
  if (!warning) return ''
  const known = PARSER_WARNING_MESSAGES[warning]
  if (known) return known
  if (!/^[a-z0-9]+(?:_[a-z0-9]+)+$/.test(warning)) return warning
  const readable = warning.replaceAll('_', ' ')
  return `${readable.charAt(0).toUpperCase()}${readable.slice(1)}.`
}
