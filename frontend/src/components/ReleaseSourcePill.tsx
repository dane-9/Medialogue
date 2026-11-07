import { badgeColor } from '../lib/badgeColor'

/** Where a release came from: its indexer when Medialogue sent it, otherwise
 *  the tracker its torrent announces to. Renders nothing when neither is known. */
export function ReleaseSourcePill({ indexer, color, tracker }: { indexer?: string | null; color?: string | null; tracker?: string | null }) {
  if (indexer) {
    const badge = badgeColor(color ?? undefined)
    return <span
      className="release-pill release-pill-indexer"
      style={badge ? { color: badge.text, borderColor: badge.border, background: badge.background } : undefined}
    ><i>Indexer</i>{indexer}</span>
  }
  if (tracker) return <span className="release-pill release-pill-tracker"><i>Indexer</i>{tracker}</span>
  return null
}
