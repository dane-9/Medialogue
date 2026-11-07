import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { formatBytes } from '../components/ReleaseResults'
import { Link } from 'react-router-dom'
import { Note, failed } from '../components/settings'
import type { Message } from '../components/settings'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { Badge, Button, EmptyState, Input, Panel, Progress } from '../components/ui'
import { downloadsApi } from '../api/modules/downloads'
import { applyProgressUpdate } from '../api/mappers/downloads'
import { integrationsApi } from '../api/modules/integrations'
import { contextMenuSelection, toggleIdSelection } from '../lib/uiState'
import type { Download, DownloadClient } from '../types'
import { Page, PosterImage } from './shared'
import { DeleteDownloadsModal } from '../components/DeleteDownloadsModal'
import { useLiveEvent, useLiveRefresh } from '../lib/liveRefresh'

// Events that change which rows exist or what they show, beyond progress.
const structuralDownloadEvents = [
  'download.completed', 'download.failed', 'download.cancelled', 'torrent.detected', 'torrent.removed',
  'torrent.reappeared', 'torrent.archived', 'torrent.archive_failed', 'torrent.tracker_unregistered',
  'torrent.tracker_recovered', 'upgrade.execution', 'qbittorrent.health',
]

// The Downloads status rail, in qBittorrent's spirit: one row per filter with a
// live count. Groups are ordered so the everyday activity filters come first
// and the two health filters (which the stat cards used to carry) sit apart.
type DownloadFilterKey = string

type DownloadRailGroup = 'Status' | 'Health' | 'Categories' | 'Tags' | 'Trackers'

type DownloadRailEntry = {
  key: DownloadFilterKey
  label: string
  group: DownloadRailGroup
  icon: Parameters<typeof Icon>[0]['name']
  tone: 'neutral' | 'blue' | 'green' | 'amber' | 'red' | 'violet' | 'teal'
}

const DOWNLOAD_RAIL_GROUPS: DownloadRailGroup[] = ['Status', 'Health', 'Categories', 'Tags', 'Trackers']

const DOWNLOAD_RAIL_COLLAPSED_KEY = 'medialogue.downloads.railCollapsed'

const DOWNLOAD_RAIL_SELECTION_KEY = 'medialogue.downloads.railSelection'

const DOWNLOAD_CLIENT_KEY = 'medialogue.downloads.client'

type DownloadRailSelection = Partial<Record<DownloadRailGroup, DownloadFilterKey>>

// A stored category or tracker that no longer exists simply matches no entry
// and is ignored when filtering, so a renamed tracker cannot strand the view.
function readRailSelection(): DownloadRailSelection {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(DOWNLOAD_RAIL_SELECTION_KEY) ?? 'null')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return { Status: 'all' }
    const entries = Object.entries(parsed as Record<string, unknown>)
      .filter(([group, key]) => DOWNLOAD_RAIL_GROUPS.includes(group as DownloadRailGroup) && typeof key === 'string')
    return Object.fromEntries(entries) as DownloadRailSelection
  } catch { return { Status: 'all' } }
}

// Unknown or malformed stored values fall back to everything expanded rather
// than leaving the rail in a state the user cannot explain.
function readCollapsedGroups(): Set<DownloadRailGroup> {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(DOWNLOAD_RAIL_COLLAPSED_KEY) ?? '[]')
    if (!Array.isArray(parsed)) return new Set()
    return new Set(parsed.filter((group): group is DownloadRailGroup => DOWNLOAD_RAIL_GROUPS.includes(group as DownloadRailGroup)))
  } catch { return new Set() }
}

const DOWNLOAD_FILTERS: DownloadRailEntry[] = [
  { key: 'all', label: 'All', group: 'Status', icon: 'shuffle', tone: 'amber' },
  { key: 'downloading', label: 'Downloading', group: 'Status', icon: 'chevrons-down', tone: 'green' },
  { key: 'seeding', label: 'Seeding', group: 'Status', icon: 'chevrons-up', tone: 'blue' },
  { key: 'completed', label: 'Completed', group: 'Status', icon: 'check', tone: 'violet' },
  { key: 'running', label: 'Running', group: 'Status', icon: 'play-solid', tone: 'green' },
  { key: 'stopped', label: 'Stopped', group: 'Status', icon: 'stop', tone: 'neutral' },
  // The transfer icon colours its own arrows, so the row tone is left neutral.
  { key: 'active', label: 'Active', group: 'Status', icon: 'transfer', tone: 'neutral' },
  { key: 'stalled', label: 'Stalled', group: 'Status', icon: 'clock', tone: 'amber' },
  { key: 'checking', label: 'Checking', group: 'Status', icon: 'checking', tone: 'teal' },
  // Nothing in the qBittorrent poll reports a relocation, so this stays at zero
  // until a moving state is actually ingested.
  { key: 'moving', label: 'Moving', group: 'Status', icon: 'moving', tone: 'blue' },
  { key: 'error', label: 'Errored', group: 'Status', icon: 'alert', tone: 'red' },
  { key: 'attention', label: 'Needs attention', group: 'Health', icon: 'security', tone: 'red' },
  { key: 'backed-up', label: 'Backed up', group: 'Health', icon: 'backup', tone: 'green' },
]

function categoryEntries(names: string[]): DownloadRailEntry[] {
  return [
    { key: 'category:all', label: 'All', group: 'Categories', icon: 'category', tone: 'neutral' },
    { key: 'category:none', label: 'Uncategorized', group: 'Categories', icon: 'category', tone: 'neutral' },
    ...names.map((name): DownloadRailEntry => ({ key: `category:${name}`, label: name, group: 'Categories', icon: 'category', tone: 'blue' })),
  ]
}

function tagEntries(names: string[]): DownloadRailEntry[] {
  return [
    { key: 'tag:all', label: 'All', group: 'Tags', icon: 'tag', tone: 'neutral' },
    { key: 'tag:none', label: 'Untagged', group: 'Tags', icon: 'tag', tone: 'neutral' },
    ...names.map((name): DownloadRailEntry => ({ key: `tag:${name}`, label: name, group: 'Tags', icon: 'tag', tone: 'violet' })),
  ]
}

function trackerEntries(hosts: string[]): DownloadRailEntry[] {
  return [
    { key: 'tracker:all', label: 'All', group: 'Trackers', icon: 'tracker', tone: 'blue' },
    { key: 'tracker:none', label: 'Trackerless', group: 'Trackers', icon: 'tracker', tone: 'neutral' },
    { key: 'tracker:error', label: 'Tracker error', group: 'Trackers', icon: 'tracker', tone: 'red' },
    { key: 'tracker:warning', label: 'Warning', group: 'Trackers', icon: 'tracker', tone: 'amber' },
    ...hosts.map((host): DownloadRailEntry => ({ key: `tracker:${host}`, label: host, group: 'Trackers', icon: 'tracker', tone: 'blue' })),
  ]
}

function DownloadContextMenu({ position, count, busy, allStopped, onRun }: {
  position: { x: number; y: number }
  count: number
  busy: boolean
  allStopped: boolean
  onRun: (action: Parameters<typeof downloadsApi.applyDownloadAction>[1], value?: number) => Promise<void>
}) {
  // A limit row swaps the menu for a small prompt rather than embedding inputs
  // in the list, which keeps every row a single line.
  const [prompt, setPrompt] = useState<null | 'download_limit' | 'upload_limit' | 'share_ratio'>(null)
  const [queueOpen, setQueueOpen] = useState(false)
  const [value, setValue] = useState('')
  const left = Math.max(12, Math.min(position.x, window.innerWidth - 177))
  const top = Math.max(12, Math.min(position.y, window.innerHeight - (prompt ? 140 : 260)))
  // Open the submenu leftwards when the parent menu is already near the edge.
  const flipQueue = left + 165 + 143 > window.innerWidth

  const prompts = {
    download_limit: { title: 'Limit download', hint: 'KiB/s, 0 for unlimited', mode: 'numeric' as const },
    upload_limit: { title: 'Limit upload', hint: 'KiB/s, 0 for unlimited', mode: 'numeric' as const },
    share_ratio: { title: 'Share ratio limit', hint: '-2 uses the global limit, -1 for unlimited', mode: 'decimal' as const },
  }

  const apply = () => {
    if (!prompt) return
    const parsed = Number(value)
    if (prompt === 'share_ratio') return void onRun(prompt, value.trim() && Number.isFinite(parsed) ? parsed : -2)
    void onRun(prompt, Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed * 1024) : 0)
  }

  return <div className="download-menu" style={{ left, top }} onClick={(event) => event.stopPropagation()}>
    {prompt ? <div className="download-menu-prompt">
      <div className="download-menu-label">{prompts[prompt].title}</div>
      <Input
        autoFocus
        value={value}
        inputMode={prompts[prompt].mode}
        placeholder={prompts[prompt].hint}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => { if (event.key === 'Enter') apply(); if (event.key === 'Escape') setPrompt(null) }}
      />
      <div className="download-menu-prompt-actions">
        <Button variant="ghost" onClick={() => setPrompt(null)}>Cancel</Button>
        <Button variant="primary" disabled={busy} onClick={apply}>Apply</Button>
      </div>
    </div> : <>
      <div className="download-menu-label">{count} selected</div>
      {/* One entry, like qBittorrent: it starts the selection only when every
          row in it is already stopped, and stops it otherwise. */}
      <button type="button" className={`download-menu-item ${allStopped ? 'tone-green' : 'tone-neutral'}`} disabled={busy} onClick={() => void onRun(allStopped ? 'start' : 'stop')}>
        <Icon name={allStopped ? 'play-solid' : 'stop'} size={13} />{allStopped ? 'Start' : 'Stop'}
      </button>
      <div className="download-menu-separator" />
      <button type="button" className="download-menu-item tone-green" disabled={busy} onClick={() => { setValue(''); setPrompt('download_limit') }}><Icon name="chevrons-down" size={13} />Limit download rate…</button>
      <button type="button" className="download-menu-item tone-blue" disabled={busy} onClick={() => { setValue(''); setPrompt('upload_limit') }}><Icon name="chevrons-up" size={13} />Limit upload rate…</button>
      <button type="button" className="download-menu-item tone-amber" disabled={busy} onClick={() => { setValue(''); setPrompt('share_ratio') }}><Icon name="share-ratio" size={13} />Limit share ratio…</button>
      <div className="download-menu-separator" />
      <div
        className="download-menu-sub"
        onMouseEnter={() => setQueueOpen(true)}
        onMouseLeave={() => setQueueOpen(false)}
      >
        <button type="button" className="download-menu-item tone-neutral" aria-haspopup="menu" aria-expanded={queueOpen} disabled={busy}>
          <Icon name="menu" size={13} />Queue
          <Icon name="caret" size={12} className="download-menu-arrow" />
        </button>
        {queueOpen && <div className={`download-menu-panel ${flipQueue ? 'flip' : ''}`}>
          <button type="button" className="download-menu-item tone-blue" disabled={busy} onClick={() => void onRun('queue_top')}><Icon name="queue-top" size={13} />Move to top</button>
          <button type="button" className="download-menu-item tone-blue" disabled={busy} onClick={() => void onRun('queue_up')}><Icon name="queue-up" size={13} />Move up</button>
          <button type="button" className="download-menu-item tone-blue" disabled={busy} onClick={() => void onRun('queue_down')}><Icon name="queue-down" size={13} />Move down</button>
          <button type="button" className="download-menu-item tone-blue" disabled={busy} onClick={() => void onRun('queue_bottom')}><Icon name="queue-bottom" size={13} />Move to bottom</button>
        </div>}
      </div>
      <div className="download-menu-separator" />
      <button type="button" className="download-menu-item tone-teal" disabled={busy} onClick={() => void onRun('recheck')}><Icon name="checking" size={13} />Force recheck</button>
      <button type="button" className="download-menu-item tone-blue" disabled={busy} onClick={() => void onRun('reannounce')}><Icon name="tracker" size={13} />Force reannounce</button>
      <div className="download-menu-separator" />
      <button type="button" className="download-menu-item tone-red" disabled={busy} onClick={() => void onRun('remove')}><Icon name="close" size={13} />Delete with files…</button>
    </>}
  </div>
}

type DownloadPayloadGroup = {
  id: string
  download: Download
  members: Download[]
}

export function downloadPayloadGroups(items: Download[]): DownloadPayloadGroup[] {
  const grouped = new Map<string, Download[]>()
  for (const item of items) {
    const path = item.payloadPath?.replace(/[\\/]+$/, '').toLocaleLowerCase()
    // The physical payload is authoritative. This also groups a torrent whose
    // tracker/name prevented a release association with an already-associated
    // cross-seed using the exact same qBittorrent content path.
    const key = item.payloadGroupId ?? (path && path !== 'unknown path'
      ? `${item.kind}:payload:${path}`
      : item.releaseId
        ? `${item.kind}:release:${item.releaseId}`
        : `torrent:${item.id}`)
    grouped.set(key, [...(grouped.get(key) ?? []), item])
  }
  return [...grouped.entries()].map(([id, members]) => {
    const preferred = members.find((item) => item.trackerState === 'healthy') ?? members[0]
    const activityOrder: Download['activity'][] = ['downloading', 'metadata', 'checking', 'stalled', 'seeding', 'queued', 'paused', 'error']
    const activity = activityOrder.find((value) => members.some((item) => item.activity === value)) ?? preferred.activity
    const trackerState: Download['trackerState'] = members.some((item) => item.trackerState === 'healthy') ? 'healthy'
      : members.some((item) => item.trackerState === 'degraded') ? 'degraded'
        : members.some((item) => item.trackerState === 'unknown') ? 'unknown'
          : members.every((item) => item.trackerState === 'unregistered') ? 'unregistered' : 'unavailable'
    const state: Download['state'] = activity === 'seeding' ? 'Seeding'
      : activity === 'checking' ? 'Checking'
        : activity === 'paused' || activity === 'queued' ? 'Paused'
          : activity === 'error' ? 'Error' : 'Downloading'
    const downloadSpeedBytes = members.reduce((total, item) => total + item.downloadSpeedBytes, 0)
    const uploadSpeedBytes = members.reduce((total, item) => total + item.uploadSpeedBytes, 0)
    const archiveState = members.every((item) => item.archiveState === 'archived') ? 'archived'
      : members.some((item) => item.archiveState === 'failed') ? 'failed' : 'not_archived'
    return {
      id,
      members,
      download: {
        ...preferred,
        activity,
        state,
        trackerState,
        archiveState,
        progress: Math.max(...members.map((item) => item.progress)),
        downloadSpeedBytes,
        uploadSpeedBytes,
        speed: downloadSpeedBytes ? `${formatBytes(downloadSpeedBytes)}/s` : '-',
        uploadSpeed: uploadSpeedBytes ? `${formatBytes(uploadSpeedBytes)}/s` : '-',
        stale: members.every((item) => item.stale),
        staleReason: members.find((item) => item.stale)?.staleReason,
      },
    }
  })
}

const ACTIVITY_LABELS: Record<Download['activity'], string> = {
  downloading: 'Downloading',
  stalled: 'Stalled',
  metadata: 'Fetching metadata',
  checking: 'Checking',
  moving: 'Moving',
  queued: 'Queued',
  paused: 'Paused',
  seeding: 'Seeding',
  error: 'Error',
  missing: 'Missing files',
}

/** What each status means and, where useful, what to do about it. */
const ACTIVITY_HINTS: Record<Download['activity'], string> = {
  downloading: 'Receiving data from peers.',
  stalled: 'Waiting for peers: nobody is sending data right now. It resumes on its own when a source appears.',
  metadata: 'Fetching the torrent metadata from peers before the download can start.',
  checking: 'qBittorrent is verifying the files on disk.',
  moving: 'qBittorrent is moving the files to a new location.',
  queued: 'Waiting in the qBittorrent queue for a free slot.',
  paused: 'Stopped in qBittorrent.',
  seeding: 'Complete and uploading to peers.',
  error: 'qBittorrent reported an I/O error, for example a full disk or a permission problem. Check its log.',
  missing: 'The files are no longer where qBittorrent expects them. Restore or remap the folder, then force a recheck.',
}

/** A selected cross-seed member, as opposed to a whole payload group. */
const MEMBER_PREFIX = 'member:'

export function DownloadsPage() {
  const [items, setItems] = useState<Download[]>([])
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(0)
  const [page, setPage] = useState(1)
  const [facets, setFacets] = useState<{ counts: Record<string, number>; categories: string[]; tags: string[]; trackers: string[] }>(
    { counts: {}, categories: [], tags: [], trackers: [] },
  )
  const [clients, setClients] = useState<DownloadClient[]>([])
  // One qBittorrent instance is shown at a time, so every count, category and
  // tracker below is derived from that client's rows alone.
  const [clientId, setClientId] = useState(() => {
    try { return window.localStorage.getItem(DOWNLOAD_CLIENT_KEY) ?? '' } catch { return '' }
  })
  // One selection per group, intersected: a status, a category and a tracker can
  // all be active at once. Clicking the active row again clears that group.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [context, setContext] = useState<{ x: number; y: number } | null>(null)
  // Deleting goes through its own dialog: what goes from disk is shown first.
  const [deleting, setDeleting] = useState<string[] | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [anchorId, setAnchorId] = useState('')
  const [selection, setSelection] = useState<DownloadRailSelection>(readRailSelection)
  const chooseEntry = (entry: DownloadRailEntry) => setSelection((current) => {
    const next: DownloadRailSelection = { ...current }
    if (next[entry.group] === entry.key) delete next[entry.group]
    else next[entry.group] = entry.key
    setPage(1)
    try { window.localStorage.setItem(DOWNLOAD_RAIL_SELECTION_KEY, JSON.stringify(next)) } catch { /* private mode */ }
    return next
  })
  const [collapsedGroups, setCollapsedGroups] = useState<Set<DownloadRailGroup>>(readCollapsedGroups)
  const toggleGroup = (group: DownloadRailGroup) => setCollapsedGroups((current) => {
    const next = new Set(current)
    if (!next.delete(group)) next.add(group)
    try { window.localStorage.setItem(DOWNLOAD_RAIL_COLLAPSED_KEY, JSON.stringify([...next])) } catch { /* private mode */ }
    return next
  })
  const [sort, setSort] = useState<{ key: keyof Download; direction: 'asc' | 'desc' }>({ key: 'name', direction: 'asc' })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // Action outcomes are reported beside the table; `error` replaces the whole
  // table and is reserved for failing to load it.
  const [actionNotice, setActionNotice] = useState<Message>(null)
  // `useLiveLibraryRefresh` captures `load` once on mount, so reading the query
  // straight from state would pin the 5s poll to whatever was selected then.
  // Each part of the query therefore travels through a ref, assigned during
  // render below.
  //
  // Adding another query parameter means three edits, not one: declare a ref
  // here, assign it beside the others, and read it in `load`. Miss the
  // assignment and the manual refetch will use the new value while the poll
  // silently keeps using the old one - which looks like the table reverting on
  // its own every five seconds.
  const activeClientRef = useRef('')
  const activeFiltersRef = useRef<string[]>([])
  const sortRef = useRef<{ key: string; direction: 'asc' | 'desc' }>({ key: 'name', direction: 'asc' })
  const pageRef = useRef(1)

  const load = async (showLoading = true) => {
    if (showLoading) setLoading(true)
    try {
      const payload = await downloadsApi.downloads({
        clientId: activeClientRef.current,
        filters: activeFiltersRef.current,
        sort: sortRef.current.key,
        direction: sortRef.current.direction,
        page: pageRef.current,
      })
      setItems(payload.items)
      setTotal(payload.total)
      setPages(payload.pages)
      // A torrent removed in qBittorrent must not stay in the selection and
      // reappear in the next action's payload.
      const currentGroups = downloadPayloadGroups(payload.items)
      setSelected((current) => new Set([...current].filter((id) => (
        id.startsWith(MEMBER_PREFIX)
          ? payload.items.some((row) => row.id === id.slice(MEMBER_PREFIX.length))
          : currentGroups.some((group) => group.id === id)
      ))))
      setError('')
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not load downloads.') }
    finally { setLoading(false) }
  }
  useEffect(() => { void integrationsApi.downloadClients().then(setClients).catch(() => setClients([])) }, [])
  // Live updates without re-reading the page on every tick: progress events
  // patch rows in place, structural events trigger one debounced reload, and
  // a slow timer picks up speeds/ETA (which events do not carry).
  const refreshFacetsRef = useRef<() => Promise<void>>(async () => undefined)
  useLiveEvent(['download.progress'], (event) => {
    let payload: { data?: { torrent_id?: unknown; client_id?: unknown; state?: unknown; progress?: unknown } }
    try { payload = JSON.parse(event.data) } catch { return }
    const data = payload?.data
    if (!data || typeof data.torrent_id !== 'string') return
    setItems((current) => current.map((item) => (
      item.torrentId === data.torrent_id && (typeof data.client_id !== 'string' || item.clientId === data.client_id)
        ? applyProgressUpdate(item, {
          state: typeof data.state === 'string' ? data.state : undefined,
          progress: typeof data.progress === 'number' ? data.progress : undefined,
        })
        : item
    )))
  })
  useLiveRefresh(() => { void load(false); void refreshFacetsRef.current() }, { events: structuralDownloadEvents, coalesceMs: 1000 })
  useLiveRefresh(() => load(false), { pollMs: 10_000, refreshOnVisible: false })
  useLiveRefresh(() => refreshFacetsRef.current(), { pollMs: 30_000, refreshOnVisible: false })

  // Fall back to the first configured client when the stored one is gone.
  const activeClientId = clients.some((client) => client.id === clientId) ? clientId : clients[0]?.id ?? ''
  const chooseClient = (id: string) => {
    setClientId(id)
    setSelected(new Set())
    setPage(1)
    try { window.localStorage.setItem(DOWNLOAD_CLIENT_KEY, id) } catch { /* private mode */ }
  }
  const activeFilters = useMemo(
    () => DOWNLOAD_RAIL_GROUPS.map((group) => selection[group]).filter((key): key is string => Boolean(key)),
    [selection],
  )

  // Assigned during render, not in an effect: `load` can fire before effects
  // flush, and a stale ref here would send the previous query. Every ref
  // declared above must be assigned in this block.
  // The effect below refetches whenever any part of the query changes.
  activeClientRef.current = activeClientId
  activeFiltersRef.current = activeFilters
  sortRef.current = { key: sort.key, direction: sort.direction }
  pageRef.current = page
  useEffect(() => { void load(false) }, [activeClientId, activeFilters.join('|'), sort.key, sort.direction, page])
  const refreshFacets = async () => {
    try {
      const payload = await downloadsApi.downloadFacets(activeClientRef.current)
      setFacets({ counts: payload.counts, categories: payload.categories, tags: payload.tags, trackers: payload.trackers })
    } catch { setFacets({ counts: {}, categories: [], tags: [], trackers: [] }) }
  }
  refreshFacetsRef.current = refreshFacets
  // Counts follow the client and structural changes, not every row refresh.
  useEffect(() => { void refreshFacets() }, [activeClientId])
  // A narrower filter can leave the current page past the end of the results.
  useEffect(() => { if (pages > 0 && page > pages) setPage(1) }, [pages, page])

  const railEntries = useMemo(
    () => [...DOWNLOAD_FILTERS, ...categoryEntries(facets.categories), ...tagEntries(facets.tags), ...trackerEntries(facets.trackers)],
    [facets],
  )
  const filtered = useMemo(() => downloadPayloadGroups(items), [items])

  // Plain click replaces, ctrl/cmd toggles, shift extends from the last anchor
  // across the rows as currently sorted and filtered.
  const rowClick = (event: React.MouseEvent, id: string) => {
    if (event.shiftKey && anchorId) {
      const order = filtered.map((item) => item.id)
      const from = order.indexOf(anchorId)
      const to = order.indexOf(id)
      if (from >= 0 && to >= 0) {
        const [start, end] = from < to ? [from, to] : [to, from]
        setSelected(new Set(order.slice(start, end + 1)))
        return
      }
    }
    if (event.ctrlKey || event.metaKey) { setSelected((current) => toggleIdSelection(current, id)); setAnchorId(id); return }
    setSelected((current) => (current.size === 1 && current.has(id) ? new Set() : new Set([id])))
    setAnchorId(id)
  }

  // Escape clears the selection; Ctrl/Cmd+A selects every row shown. Typing in
  // a field or a dialog keeps its own keys.
  const visibleOrder = useRef<string[]>([])
  visibleOrder.current = filtered.map((item) => item.id)
  useEffect(() => {
    if (context || deleting) return
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && (target.closest('input, textarea, select, [contenteditable="true"]') || target.closest('[role="dialog"], .modal'))) return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
        event.preventDefault()
        setSelected(new Set(visibleOrder.current))
      } else if (event.key === 'Escape') {
        setSelected((current) => current.size ? new Set() : current)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [context, deleting])

  const openContext = (event: React.MouseEvent, id: string) => {
    event.preventDefault()
    event.stopPropagation()
    setSelected((current) => contextMenuSelection(current, id))
    setAnchorId(id)
    setContext({ x: event.clientX, y: event.clientY })
  }

  // Whole groups act on every member; a selected member acts on itself only.
  const selectedDownloads = () => {
    const byId = new Map(items.map((item) => [item.id, item]))
    const result = new Map<string, Download>()
    for (const id of selected) {
      if (id.startsWith(MEMBER_PREFIX)) {
        const member = byId.get(id.slice(MEMBER_PREFIX.length))
        if (member) result.set(member.id, member)
      } else {
        filtered.find((group) => group.id === id)?.members.forEach((member) => result.set(member.id, member))
      }
    }
    return [...result.values()]
  }

  const runAction = async (action: Parameters<typeof downloadsApi.applyDownloadAction>[1], value?: number) => {
    if (!selected.size) return
    setActionBusy(true)
    try {
      const targets = selectedDownloads()
      const downloadIds = targets.map((item) => item.id)
      if (action === 'remove') {
        setContext(null)
        setDeleting(downloadIds)
        return
      }
      const result = await downloadsApi.applyDownloadAction(downloadIds, action, value)
      const problems = [
        ...(result.failures ?? []).map((item) => `${item.client_name}: ${item.error}`),
        ...(result.skipped ? [`${result.skipped} torrent${result.skipped === 1 ? '' : 's'} skipped because the client is disabled`] : []),
      ]
      setActionNotice(problems.length
        ? { tone: 'error', text: `Applied to ${result.applied} torrent${result.applied === 1 ? '' : 's'}, but: ${problems.join('; ')}` }
        : null)
      setContext(null)
      // The endpoint refreshes the affected rows before responding, so this
      // reload shows the new state rather than waiting for the next poll.
      await load(false)
      await refreshFacets()
    } catch (reason) { setActionNotice(failed(reason, 'Could not apply the action.')) }
    finally { setActionBusy(false) }
  }

  const setSortKey = (key: keyof Download) => { setPage(1); setSort((current) => current.key === key ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' } : { key, direction: 'asc' }) }
  const sortLabel = (key: keyof Download, label: string) => <button className="table-sort" onClick={() => setSortKey(key)}>{label}{sort.key === key && <span>{sort.direction === 'asc' ? ' ↑' : ' ↓'}</span>}</button>
  const activityLabel = (item: Download) => item.stale ? 'Stale' : ACTIVITY_LABELS[item.activity]
  const activityTone = (item: Download): 'neutral' | 'green' | 'amber' | 'red' | 'blue' => item.stale ? 'neutral' : item.state === 'Error' ? 'red' : item.state === 'Seeding' ? 'green' : item.state === 'Paused' ? 'neutral' : item.activity === 'stalled' ? 'amber' : 'blue'
  // The row is a single line, so what used to sit on a second line moves into
  // the hover tooltip for that cell rather than being dropped.
  const releaseNote = (item: Download) => <>
    {item.mediaTitle ? <div className="download-tip-media">
      <span className="download-tip-poster"><PosterImage reference={item.mediaPosterRef} title={item.mediaTitle} /></span>
      <span className="download-tip-copy">
        <strong>{item.mediaTitle}</strong>
        {item.mediaYear !== undefined && <span>{item.mediaYear}</span>}
        {item.mediaRootName && <span className="download-tip-root"><Icon name="folder" size={11} />{item.mediaRootName}</span>}
      </span>
    </div> : <p>Reconciling · no media association yet</p>}
    <p>{[item.quality, item.edition, item.client].filter(Boolean).join(' · ') || item.client}</p>
  </>
  const progressNote = (item: Download) => item.amountLeft ? `${item.amountLeft} left of ${item.size}` : `${item.size} complete`
  // qBittorrent renders peer counts as "connected (swarm total)". The total is
  // only shown when the tracker actually reported one.
  const peerCount = (connected?: number, total?: number) => total === undefined ? `${connected ?? 0}` : `${connected ?? 0} (${total})`
  return <Page title="Downloads" subtitle="Live Medialogue-managed qBittorrent activity. Historical and archived-only torrents stay in release history and Backups.">
    {deleting && <DeleteDownloadsModal
      downloadIds={deleting}
      onClose={() => setDeleting(null)}
      onDone={(text, tone = 'ok') => {
        setDeleting(null)
        setSelected(new Set())
        setActionNotice({ tone, text })
        void load(false)
        void refreshFacets()
      }}
    />}
    <div className="downloads-layout">
      <aside className="downloads-rail">
        {DOWNLOAD_RAIL_GROUPS.map((group) => <div className="downloads-rail-group" key={group}>
          <button
            type="button"
            className="downloads-rail-title"
            aria-expanded={!collapsedGroups.has(group)}
            onClick={() => toggleGroup(group)}
          >
            <Icon name="caret" size={11} />
            <span>{group}</span>
          </button>
          <div className={`downloads-rail-items ${collapsedGroups.has(group) ? 'collapsed' : ''}`}>
            <div>
              {railEntries.filter((entry) => entry.group === group).map((entry) => <button
                type="button"
                key={entry.key}
                className={`downloads-rail-item ${selection[entry.group] === entry.key ? 'active' : ''}`}
                aria-pressed={selection[entry.group] === entry.key}
                onClick={() => chooseEntry(entry)}
              >
                <span className={`downloads-rail-icon tone-${entry.tone}`}><Icon name={entry.icon} size={14} /></span>
                <span className="rail-label">{entry.label}</span>
                <span className="rail-count">{facets.counts[entry.key] ?? 0}</span>
              </button>)}
            </div>
          </div>
        </div>)}
        <Link className="button button-ghost downloads-rail-settings" to="/settings"><Icon name="settings" size={16} />Client settings</Link>
      </aside>
      <div className="downloads-main">
    {clients.length > 0 && <div className="new-media-roots downloads-clients">
      {clients.map((client) => <button
        type="button"
        key={client.id}
        className={`new-media-root ${client.id === activeClientId ? 'active' : ''}`}
        onClick={() => chooseClient(client.id)}
      >
        <span className="new-media-root-name">
          <span className={`kind-box kind-${client.scope === 'movies' ? 'movie' : 'show'}`} aria-label={client.scope === 'movies' ? 'Movies client' : 'Shows client'}>
            <Icon name={client.scope === 'movies' ? 'clapper' : 'tv-screen'} size={13} />
          </span>
          <strong>{client.name}</strong>
        </span>
        <small>{(() => {
          const count = downloadPayloadGroups(items.filter((item) => item.clientId === client.id)).length
          return `${count} payload${count === 1 ? '' : 's'}`
        })()}</small>
      </button>)}
    </div>}
    {actionNotice && <Note message={actionNotice} />}
    {error && <EmptyState icon="download" title="Could not load downloads" detail={error} action={<Button variant="ghost" icon="refresh" onClick={() => void load()}>Try again</Button>} />}
    {!error && !loading && !filtered.length && <EmptyState icon="download" title={(facets.counts.all ?? 0) ? 'No activity matches this filter' : 'No managed torrents are currently loaded'} detail={(facets.counts.all ?? 0) ? 'Try another activity filter.' : 'Historical and archived-only torrents are intentionally kept out of this live view.'} />}
    {!error && (loading || filtered.length > 0) && <Panel className="table-panel"><table className="data-table downloads-table"><thead><tr><th>{sortLabel('name', 'Release')}</th><th>{sortLabel('sizeBytes', 'Size')}</th><th>{sortLabel('progress', 'Progress')}</th><th>{sortLabel('state', 'Status')}</th><th>{sortLabel('seeds', 'Seeds')}</th><th>{sortLabel('leeches', 'Peers')}</th><th>{sortLabel('downloadSpeedBytes', 'Down speed')}</th><th>{sortLabel('uploadSpeedBytes', 'Up speed')}</th><th>{sortLabel('etaSeconds', 'ETA')}</th><th>{sortLabel('trackerState', 'Tracker')}</th><th>{sortLabel('archiveState', 'Local archive')}</th></tr></thead><tbody>{loading ? <tr><td colSpan={11} className="table-loading">Reading live qBittorrent activity…</td></tr> : filtered.map((group) => {
      const download = group.download
      const isCrossSeeded = group.members.length > 1
      const remainingSources = group.members.filter((member) => member.trackerState !== 'unregistered' && member.trackerState !== 'unavailable').length
      return <Fragment key={group.id}><tr
      className={[selected.has(group.id) ? 'row-selected' : '', download.stale ? 'row-stale' : ''].filter(Boolean).join(' ')}
      onClick={(event) => rowClick(event, group.id)}
      onMouseDown={(event) => { if (event.shiftKey) event.preventDefault() }}
      onContextMenu={(event) => openContext(event, group.id)}
    >
      <td><span className="download-name">{isCrossSeeded && <button type="button" className={`download-cross-seed-toggle ${expanded.has(group.id) ? 'open' : ''}`} aria-label={`${expanded.has(group.id) ? 'Collapse' : 'Expand'} ${download.name} cross-seeds`} aria-expanded={expanded.has(group.id)} onClick={(event) => { event.stopPropagation(); setExpanded((current) => { const next = new Set(current); if (!next.delete(group.id)) next.add(group.id); return next }) }}><Icon name="caret" size={11} /></button>}<Tooltip className="download-kind-tip" delay={250} content={releaseNote(download)}>{(download.movieId || download.showId)
        ? <Link className={`kind-box download-kind kind-${download.kind === 'Show' ? 'show' : 'movie'}`} to={download.movieId ? `/movies/${download.movieId}` : `/shows/${download.showId}`} aria-label={`Open ${download.mediaTitle ?? download.name}`}><Icon name={download.kind === 'Show' ? 'tv-screen' : 'clapper'} size={11} /></Link>
        : <span className={`kind-box download-kind kind-${download.kind === 'Show' ? 'show' : 'movie'} unmatched`}><Icon name={download.kind === 'Show' ? 'tv-screen' : 'clapper'} size={11} /></span>}</Tooltip><Tooltip className="download-name-tip" delay={250} content={<span className="download-path">{download.path}</span>}><strong>{download.name}</strong></Tooltip>{isCrossSeeded && <span className="download-cross-seed-count">Cross-seeded · {group.members.length} torrents</span>}</span></td>
      <td className="download-size">{download.size}</td>
      <td><Tooltip className="download-progress" content={progressNote(download)}><Progress value={download.progress} tone={download.state === 'Seeding' ? 'green' : download.state === 'Error' ? 'amber' : 'blue'} /><span className="download-progress-value">{Math.round(download.progress)}%</span></Tooltip></td>
      <td>{download.stale
        ? <Tooltip content={download.staleReason ?? 'This client has not reported recently.'}><Badge tone="neutral">Stale</Badge></Tooltip>
        : <Tooltip content={ACTIVITY_HINTS[download.activity]}><Badge tone={activityTone(download)}>{activityLabel(download)}</Badge></Tooltip>}</td>
      <td className="download-count">{peerCount(download.seeds, download.seedsTotal)}</td>
      <td className="download-count">{peerCount(download.leeches, download.leechesTotal)}</td>
      <td className="download-speed down">{download.speed}</td>
      <td className="download-speed up">{download.uploadSpeed}</td>
      <td className="download-eta">{download.eta}</td>
      <td><Tooltip content={[isCrossSeeded ? `${remainingSources} of ${group.members.length} torrent sources are not confirmed lost` : `Tracker ${download.trackerState}`, download.availability === undefined ? '' : `availability ${download.availability.toFixed(2)}`, download.ratio === undefined ? '' : `ratio ${download.ratio.toFixed(2)}`].filter(Boolean).join(' · ')}><Badge tone={download.trackerState === 'unregistered' || download.trackerState === 'unavailable' ? 'red' : download.trackerState === 'degraded' ? 'amber' : download.trackerState === 'healthy' ? 'green' : 'neutral'}>{isCrossSeeded ? `${remainingSources}/${group.members.length} sources` : download.trackerState === 'unknown' ? 'No tracker data' : download.trackerState}</Badge></Tooltip></td>
      <td><Tooltip content={download.archiveState === 'archived' ? 'The .torrent and recovery manifest passed integrity verification.' : download.archiveState === 'failed' ? 'Recovery data could not be archived or failed verification.' : 'Recovery data has not been archived yet.'}><Badge tone={download.archiveState === 'archived' ? 'green' : download.archiveState === 'failed' ? 'red' : 'amber'}>{download.archiveState === 'archived' ? 'Archived locally' : download.archiveState === 'failed' ? 'Failed' : 'Pending'}</Badge></Tooltip></td>
    </tr>{isCrossSeeded && expanded.has(group.id) && <tr className="download-cross-seed-detail"><td colSpan={11}><div className="download-cross-seed-members">{group.members.map((member) => <div
        key={member.id}
        role="row"
        className={`download-cross-seed-member ${selected.has(MEMBER_PREFIX + member.id) ? 'row-selected' : ''}`}
        onClick={(event) => { event.stopPropagation(); rowClick(event, MEMBER_PREFIX + member.id) }}
        onContextMenu={(event) => openContext(event, MEMBER_PREFIX + member.id)}
      ><span><Icon name="tracker" size={13} /><strong>{member.trackerHost ?? 'Unknown tracker'}</strong></span><code>{member.infoHash?.slice(0, 12) ?? 'unknown hash'}…</code><span>{member.client}</span><Badge tone={member.trackerState === 'healthy' ? 'green' : member.trackerState === 'degraded' ? 'amber' : member.trackerState === 'unknown' ? 'neutral' : 'red'}>{member.trackerState}</Badge><span>{activityLabel(member)}</span><span className="download-speed up">{member.uploadSpeed}</span></div>)}</div></td></tr>}</Fragment>
    })}</tbody></table></Panel>}
      {pages > 1 && <div className="pagination-bar">
        <Button variant="ghost" disabled={page <= 1 || loading} onClick={() => setPage((current) => current - 1)}>Previous</Button>
        <span>Page {page} of {pages} · {total} payload{total === 1 ? '' : 's'}</span>
        <Button variant="ghost" disabled={page >= pages || loading} onClick={() => setPage((current) => current + 1)}>Next</Button>
      </div>}
      </div>
    </div>
    {context && <>
      <div className="context-dismiss-layer" onClick={() => setContext(null)} onContextMenu={(event) => { event.preventDefault(); setContext(null) }} />
      <DownloadContextMenu
        position={context}
        count={selected.size}
        busy={actionBusy}
        allStopped={selectedDownloads().every((item) => item.state === 'Paused')}
        onRun={runAction}
      />
    </>}
  </Page>
}
