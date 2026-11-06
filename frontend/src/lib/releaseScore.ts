type MatchedFormat = { id: string; name: string; contribution: number }

type ReleaseScoreBreakdown = {
  profileName?: string
  total?: number
  matched: MatchedFormat[]
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

/** Pull the Custom Format breakdown the server already stores on a release.
 *
 * Scoring writes it to `parse_snapshot.current_score_snapshot`, which reaches
 * the client whole as `release.parser`, so the tooltip needs no extra request.
 * The shape is untyped JSON, so every field is read defensively: an older
 * release scored before the snapshot existed simply has nothing to show.
 */
export function releaseScoreBreakdown(parser?: Record<string, unknown>): ReleaseScoreBreakdown {
  const snapshot = asRecord(asRecord(parser)?.current_score_snapshot)
  if (!snapshot) return { matched: [] }

  const rows = Array.isArray(snapshot.breakdown) ? snapshot.breakdown : []
  const matched: MatchedFormat[] = []
  for (const row of rows) {
    const entry = asRecord(row)
    if (!entry || entry.matched !== true) continue
    const name = typeof entry.custom_format_name === 'string' ? entry.custom_format_name : undefined
    if (!name) continue
    matched.push({
      id: String(entry.custom_format_id ?? name),
      name,
      contribution: asNumber(entry.contribution) ?? 0,
    })
  }

  // Biggest contributors first; a format scoring zero still matched, so it is
  // listed, just last.
  matched.sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution) || a.name.localeCompare(b.name))

  const profileName = typeof snapshot.profile_name === 'string' ? snapshot.profile_name : undefined
  return { profileName, total: asNumber(snapshot.total_score), matched }
}
