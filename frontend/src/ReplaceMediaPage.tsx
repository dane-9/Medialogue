import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { PageTopbar } from './components/Shell'
import { upgradesApi } from './api/modules/upgrades'
import { integrationsApi } from './api/modules/integrations'
import { replacementsApi, type ReplacementItem, type ReplacementTab } from './api/modules/replacements'
import { Badge, Button, EmptyState, Select } from './components/ui'
import { Modal } from './components/Modal'
import { Icon } from './components/Icon'
import { Tooltip } from './components/Tooltip'
import { useConfirm } from './components/Confirm'
import { formatBytes, ReleaseResults } from './components/ReleaseResults'
import { tmdbPosterUrl } from './pages'
import { formatAge } from './lib/age'
import { badgeColor } from './lib/badgeColor'
import { diffReleaseTokens } from './lib/releaseTokens'
import { qualityLabel } from './lib/qualityLabel'
import { splitQuality } from './lib/quality'
import { formatPeers, swarmHealth } from './lib/swarm'
import { upgradeResultsToReleases } from './lib/upgradeResults'
import { parseJobStatusLiveEvent } from './validation/liveEvents'
import type { DownloadClient, UpgradeSearchResults } from './types'

const SEARCH_JOB = 'search_media_replacements'

const TABS: { value: ReplacementTab; label: string; icon: 'search' | 'check' | 'download' | 'clock'; tone: string; noun: string; empty: string }[] = [
  { value: 'needs_search', label: 'Needs search', icon: 'search', tone: 'amber', noun: 'title', empty: 'Nothing is waiting for a search.' },
  { value: 'found', label: 'Replacement found', icon: 'check', tone: 'ok', noun: 'title', empty: 'No replacements are waiting for review.' },
  { value: 'in_progress', label: 'In progress', icon: 'download', tone: 'blue', noun: 'title', empty: 'No replacements are downloading or being verified.' },
  { value: 'dismissed', label: 'Snoozed & ignored', icon: 'clock', tone: 'neutral', noun: 'title', empty: 'Nothing is snoozed or ignored.' },
]

function stateLabel(state?: string | null) {
  switch (state) {
    case 'submitted': return 'Submitted'
    case 'downloading': return 'Downloading'
    case 'validating': return 'Verifying'
    case 'handoff_attention': return 'Needs attention'
    case 'cleanup_required': return 'Verified'
    default: return null
  }
}

function stateTone(state?: string | null): 'neutral' | 'green' | 'amber' | 'red' | 'blue' {
  if (state === 'handoff_attention') return 'red'
  if (state === 'cleanup_required') return 'green'
  if (state === 'submitted' || state === 'downloading' || state === 'validating') return 'blue'
  return 'neutral'
}

/** One short chip for the right-hand column, the row's single most useful fact. */
function rowChip(item: ReplacementItem): { label: string; tone: string } | null {
  if (item.blocked_reason) return { label: 'Needs identity', tone: 'amber' }
  if (item.searching) return { label: 'Searching', tone: 'blue' }
  if (item.execution_state === 'handoff_attention') return { label: 'Needs attention', tone: 'red' }
  if (item.execution_state === 'cleanup_required') return { label: 'Verified', tone: 'ok' }
  if (item.candidate_title) {
    const better = (item.candidate_score ?? 0) > (item.current_score ?? 0) || (item.candidate_quality && item.candidate_quality !== item.current_quality)
    return better ? { label: 'Better', tone: 'ok' } : { label: 'Equivalent', tone: 'blue' }
  }
  if (item.last_search_result === 'no_match') return { label: 'No match', tone: 'amber' }
  if (item.last_search_result === 'failed') return { label: 'Search failed', tone: 'red' }
  return { label: 'Not searched', tone: 'neutral' }
}

function Poster({ item, large = false }: { item: ReplacementItem; large?: boolean }) {
  const src = tmdbPosterUrl(item.poster_ref ?? undefined)
  return <span className={large ? 'upgrade-details-poster' : 'new-media-poster'}>{src
    ? <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" />
    : <Icon name={item.media_type === 'shows' ? 'tv-screen' : 'clapper'} size={large ? 22 : 16} />}</span>
}

export default function ReplaceMediaPage() {
  const confirm = useConfirm()
  const [items, setItems] = useState<ReplacementItem[]>([])
  const [counts, setCounts] = useState<Record<ReplacementTab, number>>({ needs_search: 0, found: 0, in_progress: 0, dismissed: 0 })
  const [tab, setTab] = useState<ReplacementTab>('needs_search')
  const [tabChosen, setTabChosen] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [selectedKey, setSelectedKey] = useState('')
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [selectedClient, setSelectedClient] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [bulkProgress, setBulkProgress] = useState<number | null>(null)
  const [results, setResults] = useState<UpgradeSearchResults | null>(null)
  const [fullList, setFullList] = useState(false)
  const [fullSelectedId, setFullSelectedId] = useState('')

  const load = async () => {
    try {
      const [queue, configured] = await Promise.all([replacementsApi.queue(), integrationsApi.downloadClients()])
      setItems(queue.items)
      const nextCounts = queue.counts as Record<ReplacementTab, number>
      setCounts(nextCounts)
      setClients(configured)
      if (!tabChosen) {
        const first = TABS.find((entry) => nextCounts[entry.value] > 0)
        if (first) setTab(first.value)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load Replace Media.')
    } finally { setLoading(false) }
  }
  const loadRef = useRef(load)
  loadRef.current = load

  // Live: search progress, handoff state and tracker changes refresh the list.
  useEffect(() => {
    void loadRef.current()
    let timer: number | undefined
    const reload = () => {
      if (timer !== undefined) return
      timer = window.setTimeout(() => { timer = undefined; void loadRef.current() }, 800)
    }
    const stream = new EventSource('/api/v1/events/stream', { withCredentials: true })
    const onJob = (event: MessageEvent<string>) => {
      const payload = parseJobStatusLiveEvent(event.data)
      if (!payload || payload.data.job_type !== SEARCH_JOB) return
      const { status, progress } = payload.data
      if (status === 'running' || status === 'queued') setBulkProgress(typeof progress.percent === 'number' ? progress.percent : 0)
      else { setBulkProgress(null); reload() }
    }
    stream.addEventListener('job.status', onJob)
    ;['upgrade.execution', 'upgrade.candidate', 'torrent.tracker_unregistered', 'torrent.tracker_recovered',
      'download.completed', 'replacement.finished'].forEach((name) => stream.addEventListener(name, reload))
    const fallback = window.setInterval(() => { if (document.visibilityState !== 'hidden') void loadRef.current() }, 60_000)
    return () => {
      if (timer !== undefined) window.clearTimeout(timer)
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

  const rows = useMemo(() => items.filter((item) => item.tab === tab), [items, tab])
  const selected = rows.find((item) => item.target_key === selectedKey) ?? null
  const chosen = rows.filter((item) => picked.has(item.target_key))
  const searchable = (item: ReplacementItem) => (item.tab === 'needs_search' || item.tab === 'found') && !item.blocked_reason
  const dismissable = (item: ReplacementItem) => (item.torrent_ids ?? []).length > 0 && item.tab !== 'in_progress'
  const pickable = (item: ReplacementItem) => searchable(item) || dismissable(item)
  const togglePicked = (key: string) => setPicked((current) => {
    const next = new Set(current)
    if (!next.delete(key)) next.add(key)
    return next
  })
  const chooseTab = (value: ReplacementTab) => { setTab(value); setTabChosen(true); setPicked(new Set()); setSelectedKey('') }

  // The pane lists the releases the last search found for the selected row.
  useEffect(() => {
    setResults(null)
    if (!selected?.candidate_id || selected.tab !== 'found') return
    const eligible = clients.filter((client) => client.enabled && client.scope === selected.media_type)
    setSelectedClient((current) => eligible.some((client) => client.id === current) ? current : eligible[0]?.id ?? '')
    let alive = true
    upgradesApi.upgradeSearchResults(selected.candidate_id).then((value) => { if (alive) setResults(value) }).catch(() => undefined)
    return () => { alive = false }
  }, [selected?.candidate_id, selected?.candidate_title, selected?.tab])

  const search = (targets: ReplacementItem[] | 'all') => act('search', async () => {
    if (targets === 'all') { await replacementsApi.search(); setBulkProgress(0) }
    else for (const item of targets) await replacementsApi.search(item.target_key)
    setPicked(new Set())
    setMessage('Searching for replacements. Results appear as they are found; nothing is downloaded automatically.')
  })

  const decide = (targets: ReplacementItem[], action: 'ignore' | 'snooze' | 'restore') => act('decide', async () => {
    const torrentIds = targets.flatMap((item) => item.torrent_ids ?? [])
    if (torrentIds.length) await replacementsApi.review(torrentIds, action)
    setPicked(new Set())
    setSelectedKey('')
  })

  const chooseResult = (item: ReplacementItem, resultId: string) => act(item.target_key, async () => {
    await upgradesApi.selectUpgradeSearchResult(item.candidate_id!, resultId)
    setResults(await upgradesApi.upgradeSearchResults(item.candidate_id!))
  })

  const download = (item: ReplacementItem) => act(item.target_key, async () => {
    const client = clients.find((entry) => entry.id === selectedClient)
    if (!await confirm({ title: 'Download this replacement?', body: <p><strong>{item.candidate_title}</strong> will be sent to {client?.name ?? 'qBittorrent'}. The current torrent and files stay until the replacement is verified.</p>, confirmLabel: 'Download replacement', tone: 'primary' })) return
    await upgradesApi.executeUpgrade(item.candidate_id!, selectedClient, undefined, false)
    chooseTab('in_progress')
    setMessage('Replacement submitted. Follow it under In progress.')
  })

  const retire = (item: ReplacementItem) => act(item.target_key, async () => {
    const response = await replacementsApi.retireTorrents(item.candidate_id!)
    if (response.error) throw new Error(response.error)
    setMessage('Old torrents retired. Their files and backups are kept.')
  })
  const cleanup = (item: ReplacementItem) => act(item.target_key, async () => {
    const preview = await upgradesApi.upgradeCleanupPreview(item.candidate_id!)
    if (!preview.eligible) throw new Error(preview.blocked.join(' '))
    if (!await confirm({ title: 'Delete the old files?', body: <div><p>The replacement is verified. These old paths will be permanently deleted:</p><ul className="replace-path-list">{preview.targets.map((target) => <li key={target.path}><code>{target.path}</code></li>)}</ul></div>, confirmLabel: 'Delete listed files', tone: 'danger' })) return
    await upgradesApi.cleanupUpgrade(item.candidate_id!, preview.confirmationToken)
    setMessage('Old files deleted. Torrent backups are kept.')
  })
  const finish = (item: ReplacementItem) => act(item.target_key, async () => {
    await replacementsApi.finish(item.candidate_id!)
    setSelectedKey('')
    setMessage('Replacement finished. The old torrents, files and backups were kept.')
  })

  const total = counts.needs_search + counts.found + counts.in_progress + counts.dismissed
  const tabInfo = TABS.find((entry) => entry.value === tab)!
  const fullReleases = useMemo(() => (
    fullList && selected && results ? upgradeResultsToReleases(results, selected.candidate_id ?? '', selected.media_type ?? 'movies') : []
  ), [fullList, selected, results])

  return <div className="page replace-page">
    <PageTopbar title="Replace Media" subtitle="Find a new source for media whose torrents were removed by their trackers, at equal or better quality." />
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {message && !error && <div className="settings-note note-ok"><Icon name="check" size={16} /><span>{message}</span></div>}

    {loading ? <div className="upgrade-media-loading">Checking tracker evidence…</div> : !total ? <EmptyState
      icon="refresh"
      title="Nothing needs replacing"
      detail='Media appears here when every torrent behind it has been removed by its tracker (for example "unregistered torrent"). A cross-seed that still works keeps media off this list.'
    /> : <>
      <div className="new-media-roots-label">Status</div>
      <div className="new-media-roots">
        {TABS.map((entry) => <button type="button" key={entry.value} className={`new-media-root ${entry.value === tab ? 'active' : ''}`} onClick={() => chooseTab(entry.value)}>
          <span className="new-media-root-name">
            <span className={`kind-box upgrade-status-box tone-${entry.tone}`}><Icon name={entry.icon} size={13} /></span>
            <strong>{entry.label}</strong>
          </span>
          <small>{counts[entry.value]} {counts[entry.value] === 1 ? entry.noun : `${entry.noun}s`}</small>
        </button>)}
      </div>

      <div className="new-media-workspace upgrade-v2">
        <div className="new-media-scroll">
          <span className="new-media-col-head">Lost source</span>
          <span className="new-media-col-head">Replacement</span>
          {!rows.length && <div className="upgrade-v2-empty">{tabInfo.empty}</div>}
          {rows.map((item) => {
            const active = item.target_key === selectedKey
            const isPickable = pickable(item)
            const on = isPickable && picked.has(item.target_key)
            const chip = rowChip(item)
            const delta = (item.candidate_score ?? 0) - (item.current_score ?? 0)
            const swarm = swarmHealth(item.seeders)
            const sub = [item.year ? `${item.year}` : undefined, item.season_number != null ? `Season ${item.season_number}` : undefined,
              qualityLabel(item.current_quality) || undefined].filter(Boolean).join(' · ')
            const open = () => setSelectedKey(active ? '' : item.target_key)
            return <div className="new-media-row" key={item.target_key}>
              <div className={`new-media-folder-cell upgrade-v2-current ${active ? 'active' : ''}`} onClick={open}>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  aria-label={`Select ${item.title}`}
                  className={`new-media-check ${on ? 'on' : ''}`}
                  disabled={!isPickable}
                  onClick={(event) => { event.stopPropagation(); togglePicked(item.target_key) }}
                >{on && <Icon name="check" size={11} />}</button>
                <Poster item={item} />
                <span className="new-media-match-copy upgrade-v2-title">
                  <strong>{item.title}</strong>
                  <small>{sub || (item.blocked_reason ? 'Not identified' : item.media_type === 'shows' ? 'Show' : 'Movie')}</small>
                </span>
                {item.current_release && item.current_release !== item.title && <Tooltip asChild content={item.current_release}><span className="upgrade-v2-release">{item.current_release}</span></Tooltip>}
              </div>
              <div className={`new-media-match-cell upgrade-v2-change ${active ? 'active' : ''}`} onClick={open}>
                <span className="new-media-match-copy">
                  {item.candidate_title ? <>
                    <strong>{qualityLabel(item.candidate_quality) || item.candidate_quality || 'Unknown quality'}</strong>
                    {stateLabel(item.execution_state) && item.execution_state !== 'cleanup_required' && item.execution_state !== 'handoff_attention'
                      ? <span className="upgrade-v2-progress"><Badge tone={stateTone(item.execution_state)}>{stateLabel(item.execution_state)}</Badge></span>
                      : <small>CF {item.candidate_score}{delta !== 0 && <em className={delta > 0 ? 'cf-plus' : 'cf-minus'}> {delta > 0 ? '+' : ''}{delta}</em>}</small>}
                  </> : <>
                    <strong className="replace-v2-none">{item.blocked_reason ? 'Cannot search yet' : '—'}</strong>
                    <small>{item.last_searched_at ? `Searched ${formatAge(item.last_searched_at)}` : item.blocked_reason ? 'Identity unresolved' : 'No search yet'}</small>
                  </>}
                </span>
                {item.candidate_title && (swarm === 'dead' || swarm === 'weak') && <Tooltip content={`${item.seeders ?? 0} seeders: this release may be slow or impossible to download.`}><span className={`upgrade-v2-swarm swarm-${swarm}`} /></Tooltip>}
                {chip && <span className={`new-media-state upgrade-why why-${chip.tone}`}>{item.searching && <span className="replace-spinner" aria-hidden="true" />}{chip.label}</span>}
              </div>
            </div>
          })}
        </div>

        <div className="new-media-add">
          {bulkProgress !== null
            ? <span className="replace-bulk-progress"><span className="replace-spinner" aria-hidden="true" />Searching all titles · {Math.round(bulkProgress)}%</span>
            : <span>{chosen.length ? `${chosen.length} selected` : 'Select titles to act on them together.'}</span>}
          <div className="toolbar-spacer" />
          {tab === 'dismissed'
            ? <Button variant="secondary" icon="refresh" disabled={!chosen.length || Boolean(busy)} onClick={() => void decide(chosen, 'restore')}>Return to queue</Button>
            : tab !== 'in_progress' && <>
              <Button variant="ghost" disabled={!chosen.some(dismissable) || Boolean(busy)} onClick={() => void decide(chosen.filter(dismissable), 'ignore')}>Ignore</Button>
              <Button variant="ghost" disabled={!chosen.some(dismissable) || Boolean(busy)} onClick={() => void decide(chosen.filter(dismissable), 'snooze')}>Snooze 7 days</Button>
              {chosen.some(searchable)
                ? <Button variant="primary" icon="search" disabled={Boolean(busy)} onClick={() => void search(chosen.filter(searchable))}>{`Search ${chosen.filter(searchable).length} ${chosen.filter(searchable).length === 1 ? 'title' : 'titles'}`}</Button>
                : <Button variant="secondary" icon="search" disabled={Boolean(busy) || bulkProgress !== null || !(counts.needs_search + counts.found)} onClick={() => void search('all')}>Search all</Button>}
            </>}
        </div>

        {selected ? <ReplaceDetails
          key={selected.target_key}
          item={selected}
          results={results}
          busy={busy === selected.target_key || busy === 'search' || busy === 'decide'}
          clients={clients.filter((client) => client.enabled && client.scope === selected.media_type)}
          selectedClient={selectedClient}
          onClient={setSelectedClient}
          onSearch={() => void search([selected])}
          onDecide={(action) => void decide([selected], action)}
          onChooseResult={(resultId) => void chooseResult(selected, resultId)}
          onOpenAll={() => { setFullSelectedId(results?.results.find((result) => result.selected)?.id ?? ''); setFullList(true) }}
          onDownload={() => void download(selected)}
          onRetry={() => void act(selected.target_key, async () => { await upgradesApi.retryUpgradeHandoff(selected.candidate_id!) })}
          onRetire={() => void retire(selected)}
          onCleanup={() => void cleanup(selected)}
          onFinish={() => void finish(selected)}
        /> : <aside className="new-media-alt-pane new-media-alt-idle">
          <div className="new-media-col-head new-media-alt-title">Details</div>
          <div className="new-media-alt-empty">
            <Icon name="refresh" size={28} />
            <p>Select a title to see its lost source and replacement.</p>
          </div>
        </aside>}
      </div>
    </>}

    {fullList && selected && results && <Modal
      title={selected.title}
      titleSuffix={selected.year ? <span className="upgrade-results-title-year"> ({selected.year})</span> : undefined}
      eyebrow={selected.season_number != null ? `SEASON ${selected.season_number}` : 'REPLACE MEDIA'}
      className="upgrade-results-modal"
      onClose={() => setFullList(false)}
      footer={<><span className="wizard-foot-count">{`${results.results.length} Torrent${results.results.length === 1 ? '' : 's'} found`}</span><span className="modal-foot-spacer" /><Button variant="ghost" onClick={() => setFullList(false)}>Cancel</Button><Button variant="primary" icon="check" disabled={!fullSelectedId || !results.results.find((result) => result.id === fullSelectedId)?.eligible} onClick={() => { void chooseResult(selected, fullSelectedId); setFullList(false) }}>Use this result</Button></>}
    >
      <div className="upgrade-results-current">
        {(() => {
          const { resolution, type } = splitQuality(selected.current_quality ?? undefined)
          const score = selected.current_score ?? 0
          return <div className="upgrade-results-current-row">
            <span className="release-quality">{resolution ? <strong>{resolution}</strong> : null}<small>{type || 'Unknown'}</small></span>
            <span className="release-name"><strong>{selected.current_release}</strong><span className="release-tags"><span className="release-pill upgrade-current-release-pill">Current release</span><span className="release-pill release-blocked"><Icon name="blocklist" size={11} />Source lost</span></span></span>
            <span className={`release-numeric release-score ${score < 0 ? 'negative' : score > 0 ? 'positive' : ''}`}>{score > 0 ? `+${score}` : score}</span>
          </div>
        })()}
      </div>
      <div className="upgrade-results-browser">
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
          label={`Replacement search results for ${selected.title}`}
          empty="No cached releases were returned. Search again to refresh them."
        />
      </div>
    </Modal>}
  </div>
}

function ReplaceDetails({ item, results, busy, clients, selectedClient, onClient, onSearch, onDecide, onChooseResult, onOpenAll, onDownload, onRetry, onRetire, onCleanup, onFinish }: {
  item: ReplacementItem
  results: UpgradeSearchResults | null
  busy: boolean
  clients: DownloadClient[]
  selectedClient: string
  onClient: (id: string) => void
  onSearch: () => void
  onDecide: (action: 'ignore' | 'snooze' | 'restore') => void
  onChooseResult: (resultId: string) => void
  onOpenAll: () => void
  onDownload: () => void
  onRetry: () => void
  onRetire: () => void
  onCleanup: () => void
  onFinish: () => void
}) {
  const tokens = item.candidate_title && item.current_release ? diffReleaseTokens(item.candidate_title, item.current_release) : []
  const indexerBadge = badgeColor(item.indexer_color ?? undefined)
  const delta = (item.candidate_score ?? 0) - (item.current_score ?? 0)
  const others = (results?.results ?? []).slice(0, 8)
  const canDismiss = (item.torrent_ids ?? []).length > 0 && item.tab !== 'in_progress'
  const searchable = (item.tab === 'needs_search' || item.tab === 'found') && !item.blocked_reason

  return <aside className="new-media-alt-pane upgrade-details">
    <div className="new-media-col-head new-media-alt-title">Details</div>
    <div className="upgrade-details-body">
      <div className="upgrade-details-identity">
        <Poster item={item} large />
        <span className="upgrade-details-identity-copy">
          <strong>{item.title}</strong>
          <small>{[item.year ? `${item.year}` : undefined, item.season_number != null ? `Season ${item.season_number}` : undefined].filter(Boolean).join(' · ') || (item.media_type === 'shows' ? 'Show' : 'Movie')}</small>
          <small>{item.media_present === false ? 'Current files are missing or incomplete' : item.media_present ? 'Current files are still on disk' : 'Not matched to library media'}</small>
        </span>
      </div>

      {item.blocked_reason && <div className="settings-note"><Icon name="info" size={16} /><span>{item.blocked_reason}</span></div>}

      {stateLabel(item.execution_state) && <div className="upgrade-details-state">
        <Badge tone={stateTone(item.execution_state)}>{stateLabel(item.execution_state)}</Badge>
        {item.error && <p>{item.error}</p>}
        {item.execution_state === 'handoff_attention' && <span className="upgrade-details-state-actions"><Button variant="secondary" icon="refresh" disabled={busy} onClick={onRetry}>Retry verification</Button></span>}
        {item.execution_state === 'cleanup_required' && <div className="replace-choices">
          <p>The replacement is downloaded and verified. Choose what happens to the old copy:</p>
          <button type="button" className="replace-choice" disabled={busy} onClick={onRetire}><Icon name="tracker" size={16} /><strong>Retire old torrents</strong><span>Remove them from qBittorrent. Files and backups are kept.</span></button>
          <button type="button" className="replace-choice danger" disabled={busy} onClick={onCleanup}><Icon name="close" size={16} /><strong>Delete old files</strong><span>Preview, then delete. Retire the old torrents first.</span></button>
          <button type="button" className="replace-choice" disabled={busy} onClick={onFinish}><Icon name="check" size={16} /><strong>Keep and finish</strong><span>Close this replacement and keep everything.</span></button>
        </div>}
      </div>}

      {item.current_release && <section className="upgrade-details-side">
        <span className="upgrade-details-label">Lost source</span>
        <code className="upgrade-details-name">{item.current_release}</code>
        <span className="upgrade-details-facts">
          <span className="release-pill">{qualityLabel(item.current_quality) || 'Unknown'}</span>
          {item.current_score != null && <span className="upgrade-score"><b>CF {item.current_score}</b></span>}
          <Tooltip content="Every tracker behind this release reported the torrent as removed."><span className="release-pill release-blocked"><Icon name="blocklist" size={11} />Source lost</span></Tooltip>
        </span>
      </section>}

      {item.candidate_title ? <section className="upgrade-details-side upgrade-details-new">
        <span className="upgrade-details-label">Replacement</span>
        <code className="upgrade-details-name">{tokens.length ? tokens.map((token, index) => <Fragment key={index}>
          <span className="upgrade-token-sep">{token.sep}</span>
          <span className={token.changed ? 'upgrade-token-new' : 'upgrade-token-same'}>{token.text}</span>
        </Fragment>) : item.candidate_title}</code>
        <span className="upgrade-details-facts">
          <span className="release-pill upgrade-quality-new">{qualityLabel(item.candidate_quality) || 'Unknown'}</span>
          <span className="upgrade-score"><b>CF {item.candidate_score}</b>{delta !== 0 && <em className={delta > 0 ? 'cf-plus' : 'cf-minus'}>{delta > 0 ? '+' : ''}{delta}</em>}</span>
        </span>
        <span className="upgrade-details-facts">
          {item.indexer && <span className="release-pill release-pill-indexer" style={indexerBadge ? { color: indexerBadge.text, borderColor: indexerBadge.border, background: indexerBadge.background } : undefined}><i>Indexer</i>{item.indexer}</span>}
          <Tooltip className={`release-pill release-pill-swarm swarm-${swarmHealth(item.seeders)}`} content={`${item.seeders ?? 'N/A'} seeders, ${item.leechers ?? 'N/A'} leechers`}>{formatPeers(item.seeders, item.leechers)}</Tooltip>
          {item.candidate_size != null && <span className="upgrade-fact">{formatBytes(item.candidate_size)}</span>}
        </span>
      </section> : !item.blocked_reason && <section className="upgrade-details-side">
        <span className="upgrade-details-label">Replacement</span>
        <p className="new-media-alt-note">{item.searching ? 'Searching indexers…' : item.last_search_result === 'no_match' ? `Nothing equivalent or better was found ${formatAge(item.last_searched_at)} ago.` : item.last_search_result === 'failed' ? 'Every indexer failed during the last search.' : 'Not searched yet.'}</p>
      </section>}

      {item.tab === 'found' && <section className="upgrade-details-others">
        <span className="upgrade-details-label">Other releases<button type="button" className="upgrade-details-link" onClick={onOpenAll} disabled={!results}>Open full list</button></span>
        {!results ? <p className="new-media-alt-note">Loading the last search…</p>
          : !others.length ? <p className="new-media-alt-note">No other releases were found.</p>
            : others.map((result) => <button
              type="button"
              key={result.id}
              className={`new-media-candidate upgrade-other ${result.selected ? 'chosen' : ''}`}
              disabled={busy || result.selected || !result.eligible}
              title={!result.eligible ? result.rejectionReasons.join(' ') : result.title}
              onClick={() => onChooseResult(result.id)}
            >
              <span className="new-media-candidate-copy">
                <strong>{result.title}</strong>
                <small>{[qualityLabel(result.quality) || 'Unknown', result.customFormatScore !== undefined ? `CF ${result.customFormatScore}` : undefined, `${formatPeers(result.seeders, result.leechers)} peers`, result.indexerName].filter(Boolean).join(' · ')}{!result.eligible ? ' · rejected' : ''}</small>
              </span>
              {result.selected && <Icon name="check" size={14} />}
            </button>)}
      </section>}

      <div className="replace-details-actions">
        {item.tab === 'found' && <div className="replace-details-download">
          <Select aria-label="qBittorrent client" value={selectedClient} onChange={(event) => onClient(event.target.value)}>
            <option value="">qBittorrent client…</option>
            {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </Select>
          <Button variant="primary" icon="download" disabled={busy || !selectedClient} onClick={onDownload}>Download replacement</Button>
        </div>}
        <span className="replace-details-secondary">
          {searchable && <Button variant="secondary" icon="search" disabled={busy || item.searching} onClick={onSearch}>{item.searching ? 'Searching…' : item.tab === 'found' ? 'Search again' : 'Search'}</Button>}
          {canDismiss && (item.dismissed
            ? <Button variant="secondary" icon="refresh" disabled={busy} onClick={() => onDecide('restore')}>Return to queue</Button>
            : <><Button variant="ghost" disabled={busy} onClick={() => onDecide('snooze')}>Snooze 7 days</Button><Button variant="ghost" disabled={busy} onClick={() => onDecide('ignore')}>Ignore</Button></>)}
        </span>
      </div>
    </div>
  </aside>
}
