import { Fragment, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { PageTopbar } from './components/Shell'
import { upgradesApi } from './api/modules/upgrades'
import { formatEta } from './api/mappers/downloads'
import { integrationsApi } from './api/modules/integrations'
import { replacementsApi, type ReplacementItem } from './api/modules/replacements'
import { Button, EmptyState, Select } from './components/ui'
import { Modal } from './components/Modal'
import { Icon } from './components/Icon'
import { Tooltip } from './components/Tooltip'
import { ReleaseSourcePill } from './components/ReleaseSourcePill'
import { UpgradePill } from './components/UpgradePill'
import { MediaHistoryModal } from './components/MediaHistoryModal'
import { useConfirm } from './components/Confirm'
import { formatBytes, ReleaseResults } from './components/ReleaseResults'
import { tmdbPosterUrl, UpgradeCompare } from './pages'
import { formatAge } from './lib/age'
import { loadHiddenReasons, reasonShown, replaceReasonDetail, REPLACE_REASONS, saveHiddenReasons, type ReplaceReasonCode } from './lib/replaceReason'
import { systemApi } from './api/modules/system'
import { badgeColor } from './lib/badgeColor'
import { diffReleaseTokens } from './lib/releaseTokens'
import { upgradeTokenReason } from './lib/upgradeTokenReason'
import { qualityLabel } from './lib/qualityLabel'
import { splitQuality } from './lib/quality'
import { formatPeers, swarmHealth } from './lib/swarm'
import { upgradeResultsToReleases } from './lib/upgradeResults'
import { parseJobStatusLiveEvent } from './validation/liveEvents'
import type { DownloadClient, UpgradeSearchResults } from './types'
import { openLiveEvents } from './lib/liveEvents'

const SEARCH_JOB = 'search_media_replacements'

/** The statuses Upgrade Media has: everything still to do, what is on its
 *  way, what waits on you, and what was set aside (ignored). */
type ReplaceStatus = 'all' | 'in_progress' | 'attention' | 'ignored'

const STATUSES: { value: ReplaceStatus; label: string; icon: 'refresh' | 'download' | 'alert' | 'eye-off'; tone: string; empty: string }[] = [
  { value: 'all', label: 'All', icon: 'refresh', tone: 'amber', empty: 'Nothing in this folder needs a new source.' },
  { value: 'in_progress', label: 'In progress', icon: 'download', tone: 'blue', empty: 'No replacements are downloading or being verified.' },
  { value: 'attention', label: 'Needs attention', icon: 'alert', tone: 'amber', empty: 'Nothing needs your attention.' },
  { value: 'ignored', label: 'Ignored', icon: 'eye-off', tone: 'neutral', empty: 'Nothing in this folder is ignored.' },
]

function statusOf(item: ReplacementItem): ReplaceStatus {
  if (item.execution_state === 'handoff_attention' || item.execution_state === 'cleanup_required') return 'attention'
  if (item.tab === 'in_progress') return 'in_progress'
  if (item.tab === 'dismissed') return 'ignored'
  return 'all'
}

const UNKNOWN_FOLDER = 'Not in the library'

type MediaKind = 'movies' | 'shows'

/** A release name that may wrap only between its dotted words. */
function breakable(name: string) {
  return name.split(/(?<=[._\-\s])/).map((part, index) => <Fragment key={index}>{index > 0 && <wbr />}{part}</Fragment>)
}

type ApiMatchedFormat = NonNullable<ReplacementItem['current_matched_formats']>[number]

/** The API's matched format, as the score popup reads it. */
function toMatchedFormat(format: ApiMatchedFormat) {
  return { id: format.custom_format_id, name: format.custom_format_name, contribution: format.contribution, group: format.group ?? undefined }
}

/** A score change, signed: +5, −10, ±0. */
function formatScoreChange(value: number) {
  return value > 0 ? `+${value}` : value < 0 ? `−${Math.abs(value)}` : '±0'
}

function Poster({ item }: { item: ReplacementItem }) {
  const src = tmdbPosterUrl(item.poster_ref ?? undefined)
  return <span className="new-media-poster">{src
    ? <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" />
    : <Icon name={item.media_type === 'shows' ? 'tv-screen' : 'clapper'} size={16} />}</span>
}

export default function ReplaceMediaPage() {
  const confirm = useConfirm()
  const [items, setItems] = useState<ReplacementItem[]>([])
  const [status, setStatus] = useState<ReplaceStatus>('all')
  const [statusChosen, setStatusChosen] = useState(false)
  const [selectedRoot, setSelectedRoot] = useState('')
  const [hiddenReasons, setHiddenReasons] = useState<Set<ReplaceReasonCode>>(loadHiddenReasons)
  // Rows ticked by hand. A row with a replacement found starts ticked for
  // Download (like Upgrade Media) unless unticked; Ignore and Search only
  // ever act on rows ticked by hand.
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [unticked, setUnticked] = useState<Set<string>>(new Set())
  const clearPicks = () => { clearPicks(); setUnticked(new Set()) }
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [bulkProgress, setBulkProgress] = useState<number | null>(null)
  // The full release list for one row, opened from its rail button.
  const [resultsTarget, setResultsTarget] = useState<ReplacementItem | null>(null)
  const [results, setResults] = useState<UpgradeSearchResults | null>(null)
  const [fullSelectedId, setFullSelectedId] = useState('')
  // Downloading asks which client takes each kind of media first.
  const [confirming, setConfirming] = useState(false)
  const [clientFor, setClientFor] = useState<Record<MediaKind, string>>({ movies: '', shows: '' })
  // A verified replacement waits on what happens to the old copy.
  const [resolveTarget, setResolveTarget] = useState<ReplacementItem | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)

  const load = async () => {
    try {
      const [queue, configured] = await Promise.all([replacementsApi.queue(), integrationsApi.downloadClients()])
      setItems(queue.items)
      setClients(configured)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load Replace Media.')
    } finally { setLoading(false) }
  }
  const loadRef = useRef(load)
  loadRef.current = load
  // Whether anything is downloading, so progress events only cost a reload
  // while there is progress to show.
  const downloadingRef = useRef(false)
  downloadingRef.current = items.some((item) => item.execution_state === 'downloading' || item.execution_state === 'submitted')

  // Live: search progress, handoff state and tracker changes refresh the list.
  useEffect(() => {
    void loadRef.current()
    let timer: number | undefined
    const reload = () => {
      if (timer !== undefined) return
      timer = window.setTimeout(() => { timer = undefined; void loadRef.current() }, 800)
    }
    const stream = openLiveEvents()
    const onJob = (event: MessageEvent<string>) => {
      const payload = parseJobStatusLiveEvent(event.data)
      if (!payload || payload.data.job_type !== SEARCH_JOB) return
      const { status, progress } = payload.data
      if (status === 'running' || status === 'queued') setBulkProgress(typeof progress.percent === 'number' ? progress.percent : 0)
      else { setBulkProgress(null); reload() }
    }
    stream.addEventListener('job.status', onJob)
    // Live progress, speed and ETA: at most one reload every five seconds.
    let progressTimer: number | undefined
    stream.addEventListener('download.progress', () => {
      if (!downloadingRef.current || progressTimer !== undefined) return
      progressTimer = window.setTimeout(() => { progressTimer = undefined; void loadRef.current() }, 5000)
    })
    ;['upgrade.execution', 'upgrade.candidate', 'torrent.tracker_unregistered', 'torrent.tracker_recovered',
      'download.completed', 'replacement.finished'].forEach((name) => stream.addEventListener(name, reload))
    const fallback = window.setInterval(() => { if (document.visibilityState !== 'hidden') void loadRef.current() }, 60_000)
    return () => {
      if (timer !== undefined) window.clearTimeout(timer)
      if (progressTimer !== undefined) window.clearTimeout(progressTimer)
      window.clearInterval(fallback)
      stream.close()
    }
  }, [])

  const act = async (key: string, run: () => Promise<void>) => {
    setBusy(key); setError(''); setMessage('')
    try { await run(); await load() } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'That action failed.')
    } finally { setBusy('') }
  }

  // Grouped by media folder within a status, as Upgrade Media is.
  const statusCounts = useMemo(() => {
    const counts: Record<ReplaceStatus, number> = { all: 0, in_progress: 0, attention: 0, ignored: 0 }
    for (const item of items) counts[statusOf(item)] += 1
    return counts
  }, [items])
  // Open on a status that has something, until the user picks one.
  const shownStatus: ReplaceStatus = statusChosen || statusCounts[status] ? status
    : (['all', 'attention', 'in_progress', 'ignored'] as ReplaceStatus[]).find((value) => statusCounts[value]) ?? 'all'
  const statusItems = useMemo(() => items.filter((item) => statusOf(item) === shownStatus), [items, shownStatus])
  // Status, then reason, then folder: each step narrows the next one's counts.
  const reasonCounts = useMemo(() => {
    const counts = new Map<ReplaceReasonCode, number>()
    for (const item of statusItems) {
      const code = item.reason_code as ReplaceReasonCode | null | undefined
      if (code) counts.set(code, (counts.get(code) ?? 0) + 1)
    }
    return counts
  }, [statusItems])
  const reasons = REPLACE_REASONS.filter((entry) => reasonCounts.get(entry.code))
  const reasonItems = useMemo(() => statusItems.filter((item) => reasonShown(item.reason_code, hiddenReasons)), [statusItems, hiddenReasons])
  const roots = useMemo(() => {
    const grouped = new Map<string, { name: string; mediaType?: string | null; count: number }>()
    for (const item of reasonItems) {
      const name = item.root_name ?? UNKNOWN_FOLDER
      const existing = grouped.get(name)
      if (existing) existing.count += 1
      else grouped.set(name, { name, mediaType: item.media_type, count: 1 })
    }
    return [...grouped.values()].sort((a, b) => a.name === UNKNOWN_FOLDER ? 1 : b.name === UNKNOWN_FOLDER ? -1 : a.name.localeCompare(b.name))
  }, [reasonItems])
  const rootName = roots.some((entry) => entry.name === selectedRoot) ? selectedRoot : roots[0]?.name ?? ''
  const rows = useMemo(() => reasonItems.filter((item) => (item.root_name ?? UNKNOWN_FOLDER) === rootName), [reasonItems, rootName])
  const searchable = (item: ReplacementItem) => (item.tab === 'needs_search' || item.tab === 'found')
  const dismissable = (item: ReplacementItem) => Boolean(item.reason_code) && item.tab !== 'in_progress'
  const downloadable = (item: ReplacementItem) => item.tab === 'found' && Boolean(item.candidate_id)
  const pickable = (item: ReplacementItem) => searchable(item) || dismissable(item) || downloadable(item)
  const checked = (item: ReplacementItem) => picked.has(item.target_key) || (downloadable(item) && !unticked.has(item.target_key))
  const chosen = rows.filter(checked)
  const handPicked = rows.filter((item) => picked.has(item.target_key))
  const without = (set: Set<string>, key: string) => { const next = new Set(set); next.delete(key); return next }
  const togglePicked = (item: ReplacementItem) => {
    const key = item.target_key
    if (checked(item)) { setPicked((current) => without(current, key)); setUnticked((current) => new Set(current).add(key)) }
    else { setPicked((current) => new Set(current).add(key)); setUnticked((current) => without(current, key)) }
  }
  const chooseStatus = (value: ReplaceStatus) => { setStatus(value); setStatusChosen(true); clearPicks() }
  const chooseRoot = (name: string) => { setSelectedRoot(name); clearPicks() }
  const setHidden = (next: Set<ReplaceReasonCode>) => { setHiddenReasons(next); saveHiddenReasons(next); clearPicks() }
  const toggleReason = (code: ReplaceReasonCode) => {
    const next = new Set(hiddenReasons)
    if (!next.delete(code)) next.add(code)
    setHidden(next)
  }
  const clientsFor = (kind: MediaKind) => clients.filter((client) => client.enabled && client.scope === kind)

  const search = (targets: ReplacementItem[] | 'all') => act('search', async () => {
    if (targets === 'all') { await replacementsApi.search(); setBulkProgress(0) }
    else for (const item of targets) await replacementsApi.search(item.target_key)
    clearPicks()
    setMessage('Searching for replacements. Results appear as they are found; nothing is downloaded automatically.')
  })

  const decide = (targets: ReplacementItem[], action: 'ignore' | 'restore') => act('decide', async () => {
    const reviewed = targets.flatMap((item) => item.reason_code ? [{ target_key: item.target_key, reason: item.reason_code }] : [])
    if (reviewed.length) await replacementsApi.review(reviewed, action)
    clearPicks()
  })

  // A torrent only missing from qBittorrent goes back from the archive, so its
  // tracker and swarm can be checked again. The restore's own plan decides
  // where it goes and refuses anything it cannot verify.
  const readd = (item: ReplacementItem) => act(`readd:${item.target_key}`, async () => {
    const plan = await systemApi.recoveryReimportPlan()
    const wanted = new Set(item.torrent_ids ?? [])
    const mine = plan.items.filter((entry) => wanted.has(entry.torrentId))
    const ready = mine.filter((entry) => entry.status === 'ready').map((entry) => entry.torrentId)
    if (!ready.length) {
      const why = mine.flatMap((entry) => entry.reasons)[0]
      throw new Error(`Could not re-add ${item.title}${why ? `: ${why}` : ': no archived torrent is ready.'}`)
    }
    const accepted = await systemApi.startRecoveryReimport(plan.confirmationToken, ready)
    const [job] = await systemApi.waitForJobs([accepted.job_id])
    setMessage(job?.state === 'completed'
      ? `${item.title} was added back to qBittorrent. It leaves this list once its tracker has been checked.`
      : `Re-adding ${item.title} ${job?.state ?? 'failed'}. Check Tasks for details.`)
  })

  // Searches the trackers again for this title, then lists every release.
  const openResults = async (item: ReplacementItem) => {
    setResultsTarget(item); setResults(null); setError('')
    try {
      const value = await replacementsApi.searchNow(item.target_key)
      setResults(value)
      setFullSelectedId(value.results.find((entry) => entry.selected)?.id ?? value.results.find((entry) => entry.recommended)?.id ?? '')
      if (value.indexerFailures) setMessage(`${value.indexerFailures} indexer${value.indexerFailures === 1 ? '' : 's'} did not answer; the list shows what the others returned.`)
      void load()
    } catch (reason) {
      setResultsTarget(null)
      setError(reason instanceof Error ? reason.message : 'Could not search the trackers.')
    }
  }

  const chooseResult = (candidateId: string, resultId: string) => act('choose', async () => {
    await upgradesApi.selectUpgradeSearchResult(candidateId, resultId)
  })

  const toDownload = chosen.filter(downloadable)
  const kindsToDownload = [...new Set(toDownload.map((item) => (item.media_type ?? 'movies') as MediaKind))]
  const openConfirm = () => {
    setClientFor({ movies: clientsFor('movies')[0]?.id ?? '', shows: clientsFor('shows')[0]?.id ?? '' })
    setConfirming(true)
  }
  // Sent one at a time, as Upgrade Media does: a rejected one does not stop the
  // rest, and each failure names its title.
  const download = () => act('download', async () => {
    setConfirming(false)
    const failures: string[] = []
    let submitted = 0
    for (const item of toDownload) {
      try {
        await upgradesApi.executeUpgrade(item.candidate_id!, clientFor[(item.media_type ?? 'movies') as MediaKind], undefined, false)
        submitted += 1
      } catch (reason) {
        failures.push(`${item.title}: ${reason instanceof Error ? reason.message : 'submission failed'}`)
      }
    }
    clearPicks()
    if (submitted) {
      chooseStatus('in_progress')
      setMessage(`${submitted === 1 ? 'Replacement' : `${submitted} replacements`} submitted. Follow them under In progress.`)
    }
    if (failures.length) throw new Error(failures.join(' · '))
  })

  const retry = (item: ReplacementItem) => act(item.target_key, async () => { await upgradesApi.retryUpgradeHandoff(item.candidate_id!) })
  const retire = (item: ReplacementItem) => act(item.target_key, async () => {
    setResolveTarget(null)
    const response = await replacementsApi.retireTorrents(item.candidate_id!)
    if (response.error) throw new Error(response.error)
    setMessage('Old torrents retired. Their files and backups are kept.')
  })
  const cleanup = (item: ReplacementItem) => act(item.target_key, async () => {
    setResolveTarget(null)
    const preview = await upgradesApi.upgradeCleanupPreview(item.candidate_id!)
    if (!preview.eligible) throw new Error(preview.blocked.join(' '))
    if (!await confirm({ title: 'Delete the old files?', body: <div><p>The replacement is verified. These old paths will be permanently deleted:</p><ul className="replace-path-list">{preview.targets.map((target) => <li key={target.path}><code>{target.path}</code></li>)}</ul></div>, confirmLabel: 'Delete listed files', tone: 'danger' })) return
    await upgradesApi.cleanupUpgrade(item.candidate_id!, preview.confirmationToken)
    setMessage('Old files deleted. Torrent backups are kept.')
  })
  const finish = (item: ReplacementItem) => act(item.target_key, async () => {
    setResolveTarget(null)
    await replacementsApi.finish(item.candidate_id!)
    setMessage('Replacement finished. The old torrents, files and backups were kept.')
  })

  const total = items.length
  const statusInfo = STATUSES.find((entry) => entry.value === shownStatus)!
  const anyFound = rows.some(downloadable)
  const fullReleases = useMemo(() => (
    resultsTarget && results ? upgradeResultsToReleases(results, results.candidateId, resultsTarget.media_type ?? 'movies') : []
  ), [resultsTarget, results])
  const toSearch = handPicked.filter(searchable)

  return <div className="page replace-page">
    <PageTopbar
      title="Replace Media"
      subtitle="Find a new source for media the torrent backup could not bring back, at equal or better quality."
      action={<Button variant="secondary" icon="clock" onClick={() => setHistoryOpen(true)}>History</Button>}
    />
    {historyOpen && <MediaHistoryModal load={replacementsApi.history} empty="No replacements have finished yet." onClose={() => setHistoryOpen(false)} />}
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {message && !error && <div className="settings-note note-ok"><Icon name="check" size={16} /><span>{message}</span></div>}

    {loading ? <div className="upgrade-media-loading">Checking tracker evidence…</div> : !total ? <EmptyState
      icon="refresh"
      title="Nothing needs replacing"
      detail="Media appears here when no torrent behind it could bring it back: it left qBittorrent, its tracker removed it or stopped answering, nobody else seeds it, or it never had a torrent. A cross-seed that still works keeps media off this list."
    /> : <>
      <div className="upgrade-v2-filters">
        <div className="upgrade-v2-status-group">
          <div className="new-media-roots-label">Status</div>
          <div className="new-media-roots">
            {STATUSES.map((entry) => <button
              type="button"
              key={entry.value}
              className={`new-media-root ${entry.value === shownStatus ? 'active' : ''}`}
              onClick={() => chooseStatus(entry.value)}
            >
              <span className="new-media-root-name">
                <span className={`kind-box upgrade-status-box tone-${entry.tone}`}><Icon name={entry.icon} size={13} /></span>
                <strong>{entry.label}</strong>
              </span>
              <small>{statusCounts[entry.value]} {statusCounts[entry.value] === 1 ? 'title' : 'titles'}</small>
            </button>)}
          </div>
        </div>
        {roots.length > 0 && <div>
          <div className="new-media-roots-label">Media folders</div>
          <div className="new-media-roots">
            {roots.map((entry) => <button
              type="button"
              key={entry.name}
              className={`new-media-root ${entry.name === rootName ? 'active' : ''}`}
              onClick={() => chooseRoot(entry.name)}
            >
              <span className="new-media-root-name">
                <span className={`kind-box kind-${entry.mediaType === 'shows' ? 'show' : 'movie'}`} aria-label={entry.mediaType === 'shows' ? 'Shows folder' : 'Movies folder'}>
                  <Icon name={entry.mediaType === 'shows' ? 'tv-screen' : 'clapper'} size={13} />
                </span>
                <strong>{entry.name}</strong>
              </span>
              <small>{entry.count} {entry.count === 1 ? 'title' : 'titles'}</small>
            </button>)}
          </div>
        </div>}
      </div>

      <div className="downloads-layout replace-layout">
      <aside className="downloads-rail" aria-label="Filter by reason">
        <div className="downloads-rail-group">
          <div className="downloads-rail-title replace-rail-title">
            <span>Reason</span>
            {reasons.some((entry) => hiddenReasons.has(entry.code)) && <button type="button" className="replace-reason-all" onClick={() => setHidden(new Set())}>All</button>}
          </div>
          {reasons.map((entry) => {
            const on = !hiddenReasons.has(entry.code)
            return <button
              type="button"
              key={entry.code}
              aria-pressed={on}
              className={`downloads-rail-item replace-reason ${on ? 'active' : ''}`}
              onClick={() => toggleReason(entry.code)}
            >
              <span className={`downloads-rail-icon tone-${on ? entry.tone : 'neutral'}`}><Icon name={entry.icon} size={14} /></span>
              <span className="rail-label">{entry.label}</span>
              <span className="rail-count">{reasonCounts.get(entry.code)}</span>
            </button>
          })}
          {!reasons.length && <div className="replace-rail-empty">No reasons in this tab</div>}
        </div>
      </aside>
      <div className="downloads-main">
      <div className="new-media-workspace upgrade-v2 upgrade-v3 replace-v3">
        <div className="new-media-scroll">
          <span className="new-media-col-head">Lost source</span>
          <span className="new-media-col-head upgrade-v2-head">
            <span>Replacement</span>
            {shownStatus === 'in_progress'
              ? <>
                <span className="upgrade-v2-live">Progress</span>
                <span className="upgrade-v2-live">Speed</span>
                <span className="upgrade-v2-live">ETA</span>
              </>
              : shownStatus === 'attention'
                ? <span className="upgrade-v2-action">Action</span>
                : <>
                <span className="upgrade-v2-seeds">Seeds</span>
                <span className="upgrade-v2-size">Size</span>
                <span className="upgrade-v2-size-change" aria-hidden="true" />
              </>}
          </span>
          <span className="upgrade-v3-rail-head" aria-hidden="true" />
          {!rows.length && <div className="upgrade-v2-empty">{statusItems.length && !reasonItems.length ? 'Every reason with titles here is switched off.' : statusInfo.empty}</div>}
          {rows.map((item) => {
            const isPickable = pickable(item)
            const on = isPickable && checked(item)
            const delta = (item.candidate_score ?? 0) - (item.current_score ?? 0)
            // How much larger or smaller the replacement is than what was lost.
            const sizeDelta = item.candidate_size != null && item.current_size != null ? item.candidate_size - item.current_size : undefined
            // Coloured by the size of the jump, as Upgrade Media does.
            const swarm = swarmHealth(item.seeders)
            const indexerBadge = badgeColor(item.indexer_color ?? undefined)
            const sub = [item.year ? `${item.year}` : undefined, item.season_number != null ? `Season ${item.season_number}` : undefined,
              qualityLabel(item.current_quality) || undefined].filter(Boolean).join(' · ')
            // With no details pane, the row itself is the checkbox.
            const open = () => { if (isPickable) togglePicked(item) }
            // Only words the quality change explains are lit: a replacement is
            // usually another group's equal copy, so merely differing is not
            // a reason to call a word better.
            const tokens = item.candidate_title && item.current_release ? diffReleaseTokens(item.candidate_title, item.current_release) : []
            const lit = tokens.map((token) => upgradeTokenReason(token, {
              upgradeTypes: [],
              addedCustomFormats: [],
              currentQuality: item.current_quality ?? undefined,
              quality: item.candidate_quality ?? undefined,
            }))
            const rowBusy = busy === item.target_key
            // How far along the download is, as Upgrade Media shows it: a sent
            // torrent has not started and a verifying one is complete.
            const running = item.execution_state === 'submitted' || item.execution_state === 'downloading' || item.execution_state === 'validating'
            const runningPercent = item.execution_state === 'validating' ? 100 : item.execution_state === 'submitted' ? 0
              : Math.round(Math.max(0, Math.min(1, item.download_progress ?? 0)) * 100)
            const runningLabel = item.execution_state === 'submitted' ? 'Sent' : item.execution_state === 'validating' ? 'Checking' : `${runningPercent}%`
            // With no replacement yet, the right side says where the search stands.
            const searchNote = item.searching ? 'Searching indexers…'
              : item.last_search_result === 'no_match' ? `Nothing equal or better found · ${formatAge(item.last_searched_at)}`
                : item.last_search_result === 'failed' ? `Every indexer failed · ${formatAge(item.last_searched_at)}`
                  : 'Not searched yet'
            return <div className="new-media-row" key={item.target_key}>
              <div className={`new-media-folder-cell upgrade-v2-current ${item.execution_state && item.execution_state !== 'handoff_attention' ? 'replacing' : ''}`} onClick={open}>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  aria-label={`Select ${item.title}`}
                  className={`new-media-check ${on ? 'on' : ''}`}
                  disabled={!isPickable}
                  onClick={(event) => { event.stopPropagation(); togglePicked(item) }}
                >{on && <Icon name="check" size={11} />}</button>
                <Poster item={item} />
                <span className="new-media-match-copy upgrade-v2-title">
                  <strong>{item.title}</strong>
                  <small>{sub || (item.media_type === 'shows' ? 'Show' : 'Movie')}</small>
                  {/* Why the title is here: the tracker that dropped it, in its own
                      words. Hover lists every lost torrent behind the title. */}
                  {item.lost_evidence?.length
                    ? <Tooltip asChild content={<ul className="replace-lost-list">{item.lost_evidence.map((entry, index) => <li key={index}>
                      <span className="release-pill release-pill-lost"><i>Indexer</i>{entry.tracker ?? 'unknown'}</span>
                      <span>{entry.message ?? 'Removed by the tracker'}{entry.last_worked_at ? ` · last worked ${formatAge(entry.last_worked_at)}` : ''}</span>
                    </li>)}</ul>}>
                      <span className="replace-lost"><span className="replace-lost-inner">
                        <span className="release-pill release-pill-lost"><i>Indexer</i>{item.lost_evidence[0].tracker ?? 'unknown'}</span>
                        <span className="replace-lost-message">{item.lost_evidence[0].message ?? 'Removed by the tracker'}{item.lost_evidence[0].last_worked_at ? ` · ${formatAge(item.lost_evidence[0].last_worked_at)}` : ''}</span>
                      </span></span>
                    </Tooltip>
                    : item.reason_code
                      ? <Tooltip asChild content={replaceReasonDetail(item.reason_code, item.reason_since, item.readdable).help}>
                        <span className="replace-lost"><span className="replace-lost-inner">
                          <span className="release-pill release-pill-lost"><i>Why</i>{item.reason}</span>
                          <span className="replace-lost-message">{replaceReasonDetail(item.reason_code, item.reason_since, item.readdable).note}</span>
                        </span></span>
                      </Tooltip>
                      : <ReleaseSourcePill indexer={item.current_indexer} color={item.current_indexer_color} tracker={item.current_tracker} />}
                </span>
                <div className="upgrade-v2-side">
                  <div className="upgrade-v2-line">
                    <span className="upgrade-v2-release">{item.current_release && item.current_release !== item.title ? breakable(item.current_release) : '—'}</span>
                  </div>
                  <small className="upgrade-v2-score">{item.current_score != null ? `CF: ${item.current_score}` : ''}</small>
                </div>
              </div>
              <div className={"new-media-match-cell upgrade-v2-change"} onClick={open}>
                <div className="upgrade-v2-side">
                  <div className="upgrade-v2-line">
                    {item.candidate_title
                      ? <span className="upgrade-v2-release">{tokens.length ? tokens.map((token, index) => <Fragment key={index}>
                        <span className="upgrade-token-sep">{token.sep}</span>{token.sep && <wbr />}
                        <span className={lit[index] ? 'upgrade-token-new quality' : 'upgrade-token-same'}>{token.text}</span>
                      </Fragment>) : breakable(item.candidate_title)}</span>
                      : <span className="upgrade-v2-release replace-v3-note">{item.searching && <span className="replace-spinner" aria-hidden="true" />}{searchNote}</span>}
                    {shownStatus === 'in_progress' || shownStatus === 'attention'
                      ? item.execution_state === 'handoff_attention'
                        ? <span className="upgrade-v2-action"><Button variant="secondary" icon="refresh" disabled={rowBusy} onClick={(event) => { event.stopPropagation(); void retry(item) }}>{rowBusy ? 'Checking…' : 'Retry'}</Button></span>
                        : item.execution_state === 'cleanup_required'
                          ? <span className="upgrade-v2-action"><Button variant="primary" icon="check" disabled={rowBusy} onClick={(event) => { event.stopPropagation(); setResolveTarget(item) }}>Resolve</Button></span>
                          : <>
                            <span className="upgrade-v2-live">{runningLabel}</span>
                            <span className="upgrade-v2-live upgrade-v2-speed">{item.download_speed ? `${formatBytes(item.download_speed)}/s` : '-'}</span>
                            <span className="upgrade-v2-live">{item.execution_state === 'downloading' ? formatEta(item.download_eta_seconds) : '-'}</span>
                          </>
                      : item.candidate_title && <>
                        <span className="upgrade-v2-seeds">{(swarm === 'dead' || swarm === 'weak')
                          ? <Tooltip content={`${item.seeders ?? 0} seeders: this release may be slow or impossible to download.`}><span className={`release-pill release-pill-swarm swarm-${swarm}`}>{formatPeers(item.seeders, item.leechers)}</span></Tooltip>
                          : <span className={`release-pill release-pill-swarm swarm-${swarm}`}>{formatPeers(item.seeders, item.leechers)}</span>}</span>
                        <span className="upgrade-v2-size upgrade-fact">{item.candidate_size != null ? formatBytes(item.candidate_size) : '-'}</span>
                        <span className="upgrade-v2-size-change upgrade-fact">{sizeDelta !== undefined && sizeDelta !== 0 && <em className={sizeDelta > 0 ? 'size-up' : 'size-down'} aria-label={`${sizeDelta > 0 ? 'Larger' : 'Smaller'} by ${formatBytes(Math.abs(sizeDelta))}`}><Icon name="caret" size={10} />{formatBytes(Math.abs(sizeDelta))}</em>}</span>
                      </>}
                  </div>
                  {item.execution_state === 'failed'
                    // A failed attempt says why; the replacement can be downloaded again.
                    ? <div className="upgrade-v2-meta"><small className="upgrade-v2-problem" title={item.error ?? undefined}><Icon name="alert" size={11} />Last attempt failed: {item.error ?? 'no reason was recorded'}</small></div>
                    : item.execution_state === 'handoff_attention'
                    ? <div className="upgrade-v2-meta"><small className="upgrade-v2-problem" title={item.error ?? undefined}><Icon name="alert" size={11} />{item.error ?? 'The download client never confirmed the torrent.'}</small></div>
                    : item.execution_state === 'cleanup_required'
                      ? <div className="upgrade-v2-meta"><small className="replace-v3-done"><Icon name="check" size={11} />Verified · choose what happens to the old copy</small></div>
                      : item.candidate_title
                        ? <div className="upgrade-v2-meta">
                          <span onClick={(event) => event.stopPropagation()}><UpgradeCompare profile={item.profile_name ?? ''} item={{
                            currentMatchedFormats: (item.current_matched_formats ?? []).map(toMatchedFormat),
                            candidateMatchedFormats: (item.candidate_matched_formats ?? []).map(toMatchedFormat),
                            currentCustomFormatScore: item.current_score ?? 0,
                            customFormatScore: item.candidate_score ?? 0,
                            scoreDelta: delta,
                          }}>
                            <small className={`upgrade-v2-score ${delta >= 100 ? 'big' : delta >= 25 ? 'medium' : 'small'}`}>CF: {item.candidate_score} <span className="cf-score-delta">{formatScoreChange(delta)}</span></small>
                          </UpgradeCompare></span>
                          {/* The download fills the space between score and indexer. */}
                          {running && <span className="upgrade-v2-bar" style={{ '--upgrade-progress': `${runningPercent}%` } as CSSProperties} aria-hidden="true" />}
                          {item.indexer && <span className="upgrade-v2-facts"><UpgradePill facts={{
                            currentQuality: item.current_quality ?? undefined,
                            quality: item.candidate_quality ?? undefined,
                            upgradeTypes: item.upgrade_types ?? [],
                            currentMatchedFormats: (item.current_matched_formats ?? []).map(toMatchedFormat),
                            candidateMatchedFormats: (item.candidate_matched_formats ?? []).map(toMatchedFormat),
                            currentCustomFormatScore: item.current_score ?? 0,
                            customFormatScore: item.candidate_score ?? 0,
                          }} /><span className="release-pill release-pill-indexer" style={indexerBadge ? { color: indexerBadge.text, borderColor: indexerBadge.border, background: indexerBadge.background } : undefined}><i>Indexer</i>{item.indexer}</span></span>}
                        </div>
                        : <div className="upgrade-v2-meta" />}
                </div>
              </div>
              {/* Outside the panel, so the two columns keep their width. */}
              <span className="upgrade-v3-rail">
                {item.readdable && item.tab !== 'in_progress' && <Tooltip asChild content="Re-add to qBittorrent from the archive">
                  <button type="button" className="icon-button" aria-label={`Re-add ${item.title} to qBittorrent`} disabled={Boolean(busy)} onClick={() => void readd(item)}><Icon name="download" size={15} /></button>
                </Tooltip>}
                {(searchable(item) || downloadable(item)) && <Tooltip asChild content="View all releases">
                  <button type="button" className="icon-button" aria-label={`View all releases for ${item.title}`} onClick={() => void openResults(item)}><Icon name="search" size={15} /></button>
                </Tooltip>}
              </span>
            </div>
          })}
        </div>

        <div className="new-media-add">
          {bulkProgress !== null
            ? <span className="replace-bulk-progress"><span className="replace-spinner" aria-hidden="true" />Searching all titles · {Math.round(bulkProgress)}%</span>
            : <span>{chosen.length ? `${chosen.length} selected` : shownStatus === 'in_progress' ? 'Replacements on their way. Progress updates live.' : shownStatus === 'attention' ? 'These replacements are waiting for you.' : 'Select titles to act on them together.'}</span>}
          <div className="toolbar-spacer" />
          {shownStatus === 'ignored'
            ? <Button variant="secondary" icon="refresh" disabled={!chosen.length || Boolean(busy)} onClick={() => void decide(chosen, 'restore')}>Return to queue</Button>
            : shownStatus === 'all' && <>
              <Button variant="ghost" disabled={!handPicked.some(dismissable) || Boolean(busy)} onClick={() => void decide(handPicked.filter(dismissable), 'ignore')}>Ignore</Button>
              {toSearch.length
                ? <Button variant={anyFound ? 'secondary' : 'primary'} icon="search" disabled={Boolean(busy)} onClick={() => void search(toSearch)}>{`Search ${toSearch.length} ${toSearch.length === 1 ? 'title' : 'titles'}`}</Button>
                : <Button variant="secondary" icon="search" disabled={Boolean(busy) || bulkProgress !== null || !statusCounts.all} onClick={() => void search('all')}>Search all</Button>}
              {anyFound && <Button variant="primary" icon="download" disabled={!toDownload.length || Boolean(busy)} onClick={openConfirm}>{`Download ${toDownload.length} ${toDownload.length === 1 ? 'replacement' : 'replacements'}`}</Button>}
            </>}
        </div>
      </div>
      </div>
      </div>
    </>}

    {confirming && <Modal
      title={`Download ${toDownload.length} ${toDownload.length === 1 ? 'replacement' : 'replacements'}?`}
      eyebrow="REPLACE MEDIA"
      onClose={() => setConfirming(false)}
      footer={<><span className="modal-foot-spacer" /><Button variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button><Button variant="primary" icon="download" disabled={kindsToDownload.some((kind) => !clientFor[kind]) || Boolean(busy)} onClick={() => void download()}>Download</Button></>}
    >
      <p className="replace-confirm-note">Each replacement is sent to the client below. The current torrents and files stay until the replacement is verified.</p>
      {kindsToDownload.map((kind) => <label key={kind} className="replace-confirm-client">
        <span>{kind === 'shows' ? 'Shows' : 'Movies'} client</span>
        <Select aria-label={`${kind === 'shows' ? 'Shows' : 'Movies'} download client`} value={clientFor[kind]} onChange={(event) => setClientFor((current) => ({ ...current, [kind]: event.target.value }))}>
          <option value="">Choose a client…</option>
          {clientsFor(kind).map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
        </Select>
      </label>)}
    </Modal>}

    {resolveTarget && <Modal
      title={resolveTarget.title}
      eyebrow="REPLACEMENT VERIFIED"
      onClose={() => setResolveTarget(null)}
      footer={<><span className="modal-foot-spacer" /><Button variant="ghost" onClick={() => setResolveTarget(null)}>Cancel</Button></>}
    >
      <div className="replace-choices">
        <p>The replacement is downloaded and verified. Choose what happens to the old copy:</p>
        <button type="button" className="replace-choice" disabled={Boolean(busy)} onClick={() => void retire(resolveTarget)}><Icon name="tracker" size={16} /><strong>Retire old torrents</strong><span>Remove them from qBittorrent. Files and backups are kept.</span></button>
        <button type="button" className="replace-choice danger" disabled={Boolean(busy)} onClick={() => void cleanup(resolveTarget)}><Icon name="close" size={16} /><strong>Delete old files</strong><span>Preview, then delete. Retire the old torrents first.</span></button>
        <button type="button" className="replace-choice" disabled={Boolean(busy)} onClick={() => void finish(resolveTarget)}><Icon name="check" size={16} /><strong>Keep and finish</strong><span>Close this replacement and keep everything.</span></button>
      </div>
    </Modal>}

    {resultsTarget && <Modal
      title={resultsTarget.title}
      titleSuffix={resultsTarget.year ? <span className="upgrade-results-title-year"> ({resultsTarget.year})</span> : undefined}
      eyebrow={resultsTarget.season_number != null ? `SEASON ${resultsTarget.season_number}` : 'REPLACE MEDIA'}
      className="upgrade-results-modal"
      onClose={() => setResultsTarget(null)}
      footer={<><span className="wizard-foot-count">{results ? `${results.results.length} Torrent${results.results.length === 1 ? '' : 's'} found` : 'Searching the trackers…'}</span><span className="modal-foot-spacer" /><Button variant="ghost" onClick={() => setResultsTarget(null)}>Cancel</Button><Button variant="primary" icon="check" disabled={!fullSelectedId || !results?.results.find((result) => result.id === fullSelectedId)?.eligible} onClick={() => { if (results?.candidateId) void chooseResult(results.candidateId, fullSelectedId); setResultsTarget(null) }}>Use this result</Button></>}
    >
      <div className="upgrade-results-current">
        {(() => {
          const { resolution, type } = splitQuality(resultsTarget.current_quality ?? undefined)
          const score = resultsTarget.current_score ?? 0
          return <div className="upgrade-results-current-row">
            <span className="release-quality">{resolution ? <strong>{resolution}</strong> : null}<small>{type || 'Unknown'}</small></span>
            <span className="release-name"><strong>{resultsTarget.current_release}</strong><span className="release-tags"><span className="release-pill upgrade-current-release-pill">Current release</span><span className="release-pill release-blocked"><Icon name="blocklist" size={11} />Source lost</span></span></span>
            <span className={`release-numeric release-score ${score < 0 ? 'negative' : score > 0 ? 'positive' : ''}`}>{score > 0 ? `+${score}` : score}</span>
          </div>
        })()}
      </div>
      {results && <div className="upgrade-results-browser">
        <ReleaseResults
          results={fullReleases}
          selectedId={fullSelectedId}
          onSelect={setFullSelectedId}
          isSelectable={(release) => Boolean(results.results.find((entry) => entry.id === release.id)?.eligible)}
          renderTags={(release) => {
            const result = results.results.find((entry) => entry.id === release.id)
            if (!result) return null
            return <>
              {result.recommended && <span className="release-pill upgrade-result-recommended">Recommended</span>}
              {result.selected && <span className="release-pill upgrade-result-selected">Selected</span>}
              {!result.eligible && !release.blocklisted && <Tooltip className="release-pill release-blocked" content={<ul>{result.rejectionReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}><Icon name="alert" size={11} />Rejected</Tooltip>}
            </>
          }}
          label={`Replacement search results for ${resultsTarget.title}`}
          empty="The trackers returned no releases for this title."
        />
      </div>}
    </Modal>}
  </div>
}
