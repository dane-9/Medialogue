import { useEffect, useRef } from 'react'
import { formatEta } from '../api/mappers/downloads'
import { downloadsApi } from '../api/modules/downloads'
import { useLiveEvent } from '../lib/liveRefresh'
import type { IncomingDownload } from '../types'
import { Icon } from './Icon'
import { Badge, Progress } from './ui'

/** How often a page following a download asks qBittorrent for its progress. */
const LIVE_DOWNLOAD_MS = 2000

/** A download on its way: it becomes a release when it finishes. */
export function IncomingReleaseRow({ incoming }: { incoming: IncomingDownload }) {
  const replacing = incoming.kind === 'replacement'
  // "sent": qBittorrent took the torrent and no poll has reported it yet.
  const state = incoming.state === 'sent' ? 'Sent to qBittorrent, waiting for its first update' : incoming.state
  const facts = [incoming.client, state, incoming.eta ? `ETA ${incoming.eta}` : undefined, incoming.quality].filter(Boolean).join(' · ')
  return <div className="release-evidence-row release-incoming-row">
    <div className="release-icon release-incoming-icon"><Icon name="download" size={18} /></div>
    <div className="release-main">
      <strong title={incoming.name}>{incoming.name}</strong>
      <span>{facts}</span>
      <Progress value={incoming.progress} tone="blue" />
    </div>
    <strong className="release-incoming-percent">{Math.round(incoming.progress)}%</strong>
    <Badge tone="blue">{replacing ? 'Replacing' : 'Downloading'}</Badge>
  </div>
}

/** A live reading of one torrent: progress 0-100, its state and ETA. */
export interface LiveDownloadUpdate {
  torrentId: string
  progress?: number
  state?: string
  eta?: string
}

/** Apply a live reading to a download, when it is that download's torrent. */
export function withLiveUpdate<T extends IncomingDownload>(download: T, update: LiveDownloadUpdate): T {
  if (download.torrentId !== update.torrentId) return download
  return { ...download, progress: update.progress ?? download.progress, state: update.state ?? download.state, eta: update.eta }
}

function readUpdate(data: Record<string, unknown>): LiveDownloadUpdate | null {
  if (typeof data.torrent_id !== 'string') return null
  return {
    torrentId: data.torrent_id,
    progress: typeof data.progress === 'number' && Number.isFinite(data.progress) ? data.progress * 100 : undefined,
    state: typeof data.state === 'string' ? data.state : undefined,
    eta: typeof data.eta_seconds === 'number' && data.eta_seconds > 0 && data.eta_seconds < 8_640_000 ? formatEta(data.eta_seconds) : undefined,
  }
}

/** Keep the given downloads moving in real time.
 *
 * The client poll runs on its own interval (30 seconds by default), so while
 * a page shows downloads it asks qBittorrent for each one every couple of
 * seconds, and also takes the poll's own live events. Only while the tab is
 * visible.
 */
export function useLiveDownloads(torrentIds: string[], onUpdate: (update: LiveDownloadUpdate) => void) {
  const apply = useRef(onUpdate)
  apply.current = onUpdate
  useLiveEvent(['download.progress'], (event) => {
    let payload: { data?: Record<string, unknown> }
    try { payload = JSON.parse(event.data) } catch { return }
    const update = payload?.data ? readUpdate(payload.data) : null
    if (update && torrentIds.includes(update.torrentId)) apply.current(update)
  })
  const key = [...torrentIds].sort().join(',')
  useEffect(() => {
    if (!key) return
    const ids = key.split(',')
    let alive = true
    let timer = 0
    const tick = async () => {
      if (document.visibilityState === 'visible') {
        await Promise.all(ids.map(async (id) => {
          try {
            const update = readUpdate(await downloadsApi.liveDownload(id) as unknown as Record<string, unknown>)
            if (alive && update) apply.current(update)
          } catch { /* The poll and live events still move the bar. */ }
        }))
      }
      if (alive) timer = window.setTimeout(() => void tick(), LIVE_DOWNLOAD_MS)
    }
    void tick()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [key])
}
