import { formatAge } from './age'

export type ReplaceReasonCode = 'not_watched' | 'no_seeders' | 'tracker_gone' | 'removed_from_tracker' | 'no_torrent'

/** Every reason, in the order the Reason filter shows them. */
export const REPLACE_REASONS: { code: ReplaceReasonCode; label: string; icon: 'qbittorrent' | 'share-ratio' | 'tracker' | 'alert' | 'folder'; tone: string }[] = [
  { code: 'removed_from_tracker', label: 'Removed from tracker', icon: 'alert', tone: 'red' },
  { code: 'tracker_gone', label: 'Tracker not responding', icon: 'tracker', tone: 'amber' },
  { code: 'no_seeders', label: 'No other seeders', icon: 'share-ratio', tone: 'amber' },
  { code: 'not_watched', label: 'Not in qBittorrent', icon: 'qbittorrent', tone: 'blue' },
  { code: 'no_torrent', label: 'No torrent', icon: 'folder', tone: 'neutral' },
]

const HIDDEN_KEY = 'medialogue.replace.hiddenReasons'

/** The reasons the viewer switched off, remembered in this browser. */
export function loadHiddenReasons(): Set<ReplaceReasonCode> {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(HIDDEN_KEY) ?? '[]')
    const known = new Set<string>(REPLACE_REASONS.map((entry) => entry.code))
    return new Set(Array.isArray(saved) ? saved.filter((code): code is ReplaceReasonCode => known.has(code)) : [])
  } catch { return new Set() }
}

export function saveHiddenReasons(hidden: Set<ReplaceReasonCode>) {
  try { window.localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hidden])) } catch { /* private window: just not remembered */ }
}

/** Whether a row passes the Reason filter. Rows without a reason (a
 *  replacement already under way) always show. */
export function reasonShown(code: string | null | undefined, hidden: Set<ReplaceReasonCode>) {
  return !code || !hidden.has(code as ReplaceReasonCode)
}

/** Why a title could not be restored from the backup, for its Replace Media row. */
export function replaceReasonDetail(code: ReplaceReasonCode, since?: string | null, readdable = true): { help: string; note: string } {
  const age = since ? formatAge(since) : ''
  switch (code) {
    case 'not_watched':
      if (!readdable) return {
        help: 'The torrent is no longer in qBittorrent and the archive has no copy of its .torrent file, so it cannot be put back. Search for a new torrent.',
        note: `${age ? `Left qBittorrent ${age}` : 'Not in any qBittorrent client'} · not archived`,
      }
      return {
        help: 'The torrent is no longer in qBittorrent, so Medialogue cannot check that its tracker still has it. Re-add it from the archive to check again.',
        note: age ? `Left qBittorrent ${age}` : 'Not in any qBittorrent client',
      }
    case 'no_seeders':
      return {
        help: 'Nobody else has seeded this torrent for two weeks. If your copy were lost, there would be nobody to download it from.',
        note: age ? `Only seeder for ${age.replace(/ ago$/, '')}` : 'Only seeder',
      }
    case 'tracker_gone':
      return {
        help: 'None of this torrent\'s trackers has answered for a week. The site may be gone.',
        note: age ? `Last answered ${age}` : 'Never answered',
      }
    case 'removed_from_tracker':
      return {
        help: 'Every tracker confirmed the torrent was deleted, so it cannot be downloaded again.',
        note: age ? `Confirmed ${age}` : 'Removed by the tracker',
      }
    case 'no_torrent':
      return {
        help: 'These files were never linked to a torrent, so the backup has nothing to bring them back from.',
        note: 'Never linked to a torrent',
      }
  }
}
