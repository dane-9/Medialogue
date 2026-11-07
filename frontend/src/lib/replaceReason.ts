import { formatAge } from './age'

export type ReplaceReasonCode = 'not_watched' | 'no_seeders' | 'tracker_gone' | 'removed_from_tracker' | 'no_torrent'

/** Why a title could not be restored from the backup, for its Replace Media row. */
export function replaceReasonDetail(code: ReplaceReasonCode, since?: string | null): { help: string; note: string } {
  const age = since ? formatAge(since) : ''
  switch (code) {
    case 'not_watched':
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
