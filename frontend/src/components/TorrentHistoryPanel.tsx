import type { TorrentArchiveItem } from '../types'
import { Badge, Panel } from './ui'

function torrentTrackerLabel(tracker?: string) {
  if (!tracker) return undefined
  try { return new URL(tracker).hostname }
  catch { return tracker }
}

function torrentHistoryGroups(items: TorrentArchiveItem[]) {
  const groups = new Map<string, TorrentArchiveItem[]>()
  items.forEach((item) => {
    const key = item.releaseId || `${item.releaseName || item.torrentName}|${item.quality || ''}|${item.edition || ''}`
    groups.set(key, [...(groups.get(key) ?? []), item])
  })
  return [...groups.values()]
}

function TorrentHistoryEntry({ torrent, nested = false }: { torrent: TorrentArchiveItem; nested?: boolean }) {
  const tracker = torrentTrackerLabel(torrent.tracker)
  return <div className={`history-row ${nested ? 'torrent-history-member' : ''}`}><span className="history-line" /><div><strong>{torrent.releaseName ?? torrent.torrentName}</strong><span>{tracker ? `${tracker} · ` : ''}{torrent.infoHash.slice(0, 12)}… · {torrent.originalDownloadClient ?? 'Unknown client'} · qBit {torrent.qbitPresent ? 'present' : 'removed'}</span></div><Badge tone={torrent.archiveState === 'archived' ? 'green' : torrent.archiveState === 'failed' ? 'red' : 'amber'}>{torrent.archiveState.replaceAll('_', ' ')}</Badge></div>
}

/** Every torrent a movie's or a show's releases were tied to, cross-seeds grouped under their release. */
export function TorrentHistoryPanel({ items, noun }: { items?: TorrentArchiveItem[]; noun: 'movie' | 'show' }) {
  const groups = torrentHistoryGroups(items ?? [])
  return <Panel title="Torrent history">{groups.length ? groups.map((group) => {
    const first = group[0]
    if (group.length === 1) return <TorrentHistoryEntry key={first.id} torrent={first} />
    const presentCount = group.filter((item) => item.qbitPresent).length
    const trackers = [...new Set(group.map((item) => torrentTrackerLabel(item.tracker)).filter(Boolean))]
    return <details className="torrent-history-group" key={first.releaseId || first.id}>
      <summary className="torrent-history-summary"><span className="history-line" /><div><strong>{first.releaseName ?? first.torrentName}</strong><span>Cross-seeded · {group.length} torrents · {presentCount} qBit present</span>{trackers.length > 0 && <small>{trackers.join(' · ')}</small>}</div><Badge tone="blue">{group.length} torrents</Badge></summary>
      <div className="torrent-history-members">{group.map((torrent) => <TorrentHistoryEntry key={torrent.id} torrent={torrent} nested />)}</div>
    </details>
  }) : <div className="history-empty">No torrent recovery history is associated with this {noun} yet.</div>}</Panel>
}
