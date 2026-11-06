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
import { PosterImage } from './pages'
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

const TABS: { value: ReplacementTab; label: string; icon: 'search' | 'check' | 'download' | 'clock'; noun: string }[] = [
  { value: 'needs_search', label: 'Needs search', icon: 'search', noun: 'title' },
  { value: 'found', label: 'Replacement found', icon: 'check', noun: 'replacement' },
  { value: 'in_progress', label: 'In progress', icon: 'download', noun: 'replacement' },
  { value: 'dismissed', label: 'Snoozed & ignored', icon: 'clock', noun: 'title' },
]

const EMPTY: Record<ReplacementTab, { title: string; detail: string }> = {
  needs_search: {
    title: 'Nothing needs replacing',
    detail: 'Media appears here when every torrent behind it has been removed by its tracker (for example "unregistered torrent"). A cross-seed that still works keeps media off this list.',
  },
  found: { title: 'No replacements waiting', detail: 'Searches that find an equivalent or better release list it here for review. Nothing is downloaded automatically.' },
  in_progress: { title: 'No replacements in progress', detail: 'A replacement you start is tracked here while it downloads, is verified, and the old copy is retired.' },
  dismissed: { title: 'Nothing snoozed or ignored', detail: 'Snoozed titles return after 7 days. Ignored titles stay out of the queue and scheduled searches until you return them.' },
}

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

/** Why a row has no replacement yet, in the replacement column. */
function missingReason(item: ReplacementItem) {
  if (item.blocked_reason) return item.blocked_reason
  if (item.searching) return 'Searching indexers…'
  if (item.last_search_result === 'no_match') return `Nothing equivalent or better · searched ${formatAge(item.last_searched_at)}`
  if (item.last_search_result === 'failed') return `Every indexer failed · ${formatAge(item.last_searched_at)}`
  return 'Not searched yet'
}

export default function ReplaceMediaPage() {
  const confirm = useConfirm()
  const [items, setItems] = useState<ReplacementItem[]>([])
  const [counts, setCounts] = useState<Record<ReplacementTab, number>>({ needs_search: 0, found: 0, in_progress: 0, dismissed: 0 })
  const [tab, setTab] = useState<ReplacementTab>('needs_search')
  const [tabChosen, setTabChosen] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [bulkProgress, setBulkProgress] = useState<number | null>(null)
  const [review, setReview] = useState<ReplacementItem | null>(null)
  const [results, setResults] = useState<UpgradeSearchResults | null>(null)
  const [selectedResultId, setSelectedResultId] = useState('')
  const [selectedClient, setSelectedClient] = useState('')
  const [finishing, setFinishing] = useState<ReplacementItem | null>(null)

  const load = async () => {
    try {
      const [queue, configured] = await Promise.all([replacementsApi.queue(), integrationsApi.downloadClients()])
      setItems(queue.items)
      const nextCounts = queue.counts as Record<ReplacementTab, number>
      setCounts(nextCounts)
      setClients(configured)
      // Open on the first status that has something, until the user picks one.
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

  // Live: search progress, handoff state and tracker changes refresh the list;
  // nothing waits on a blocking request.
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
  const chosen = rows.filter((item) => picked.has(item.target_key))
  const searchable = (item: ReplacementItem) => (item.tab === 'needs_search' || item.tab === 'found') && !item.blocked_reason
  const dismissable = (item: ReplacementItem) => (item.torrent_ids ?? []).length > 0 && item.tab !== 'in_progress'
  const pickable = (item: ReplacementItem) => searchable(item) || dismissable(item)
  const togglePicked = (key: string) => setPicked((current) => {
    const next = new Set(current)
    if (!next.delete(key)) next.add(key)
    return next
  })
  const chooseTab = (value: ReplacementTab) => { setTab(value); setTabChosen(true); setPicked(new Set()) }

  // Searches run in the background; rows show "Searching indexers…" live.
  const search = (targets: ReplacementItem[] | 'all') => act('search', async () => {
    if (targets === 'all') { await replacementsApi.search(); setBulkProgress(0) }
    else for (const item of targets) await replacementsApi.search(item.target_key)
    setPicked(new Set())
    setMessage('Searching for replacements. Results appear as they are found; nothing is downloaded automatically.')
  })

  const decideAll = (targets: ReplacementItem[], action: 'ignore' | 'snooze' | 'restore') => act('decide', async () => {
    const torrentIds = targets.flatMap((item) => item.torrent_ids ?? [])
    if (torrentIds.length) await replacementsApi.review(torrentIds, action)
    setPicked(new Set())
  })

  const openReview = (item: ReplacementItem) => act(item.target_key, async () => {
    const eligible = clients.filter((client) => client.enabled && client.scope === item.media_type)
    setSelectedClient(eligible.length === 1 ? eligible[0].id : '')
    const loaded = await upgradesApi.upgradeSearchResults(item.candidate_id!)
    setResults(loaded)
    setSelectedResultId(loaded.results.find((result) => result.selected)?.id ?? '')
    setReview(item)
  })

  const closeReview = () => { setReview(null); setResults(null); setSelectedResultId('') }

  const download = () => {
    if (!review?.candidate_id || !selectedClient || !selectedResultId) return
    void act(review.target_key, async () => {
      const current = results?.results.find((result) => result.selected)
      let title = review.candidate_title
      // Picking another release first makes it the replacement.
      if (current?.id !== selectedResultId) title = (await upgradesApi.selectUpgradeSearchResult(review.candidate_id!, selectedResultId)).title
      const client = clients.find((entry) => entry.id === selectedClient)
      if (!await confirm({ title: 'Download this replacement?', body: <p><strong>{title}</strong> will be sent to {client?.name ?? 'qBittorrent'}. The current torrent and files stay until the replacement is verified.</p>, confirmLabel: 'Download replacement', tone: 'primary' })) return
      await upgradesApi.executeUpgrade(review.candidate_id!, selectedClient, undefined, false)
      closeReview(); chooseTab('in_progress')
      setMessage('Replacement submitted. Follow it under In progress.')
    })
  }

  const retire = (item: ReplacementItem) => act(item.target_key, async () => {
    const response = await replacementsApi.retireTorrents(item.candidate_id!)
    if (response.error) throw new Error(response.error)
    setFinishing(null)
    setMessage('Old torrents retired. Their files and backups are kept.')
  })
  const cleanup = (item: ReplacementItem) => act(item.target_key, async () => {
    const preview = await upgradesApi.upgradeCleanupPreview(item.candidate_id!)
    if (!preview.eligible) throw new Error(preview.blocked.join(' '))
    if (!await confirm({ title: 'Delete the old files?', body: <div><p>The replacement is verified. These old paths will be permanently deleted:</p><ul className="replace-path-list">{preview.targets.map((target) => <li key={target.path}><code>{target.path}</code></li>)}</ul></div>, confirmLabel: 'Delete listed files', tone: 'danger' })) return
    await upgradesApi.cleanupUpgrade(item.candidate_id!, preview.confirmationToken)
    setFinishing(null)
    setMessage('Old files deleted. Torrent backups are kept.')
  })
  const finish = (item: ReplacementItem) => act(item.target_key, async () => {
    await replacementsApi.finish(item.candidate_id!)
    setFinishing(null)
    setMessage('Replacement finished. The old torrents, files and backups were kept.')
  })

  const reviewReleases = useMemo(() => (
    review && results ? upgradeResultsToReleases(results, review.candidate_id ?? '', review.media_type ?? 'movies') : []
  ), [review, results])
  const reviewClients = review ? clients.filter((client) => client.enabled && client.scope === review.media_type) : []
  const selectedEligible = Boolean(results?.results.find((result) => result.id === selectedResultId)?.eligible)
  const total = counts.needs_search + counts.found + counts.in_progress + counts.dismissed
  const tabInfo = TABS.find((entry) => entry.value === tab)!

  return <div className="page replace-page">
    <PageTopbar
      title="Replace Media"
      subtitle="Find a new source for media whose torrents were removed by their trackers, at equal or better quality."
    />
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {message && !error && <div className="settings-note note-ok"><Icon name="check" size={16} /><span>{message}</span></div>}

    {loading ? <div className="upgrade-media-loading">Checking tracker evidence…</div> : !total ? <EmptyState
      icon="refresh"
      title={EMPTY.needs_search.title}
      detail={EMPTY.needs_search.detail}
    /> : <>
      <div className="new-media-roots-label">Status</div>
      <div className="new-media-roots">
        {TABS.map((entry) => <button
          type="button"
          key={entry.value}
          className={`new-media-root ${entry.value === tab ? 'active' : ''}`}
          onClick={() => chooseTab(entry.value)}
        >
          <span className="new-media-root-name">
            <span className="kind-box replace-status-box"><Icon name={entry.icon} size={13} /></span>
            <strong>{entry.label}</strong>
          </span>
          <small>{counts[entry.value]} {counts[entry.value] === 1 ? entry.noun : `${entry.noun}s`}</small>
        </button>)}
      </div>

      <div className="upgrade-workspace">
        <div className="upgrade-scroll">
          <span className="new-media-col-head upgrade-head">Current release<em>CF Score</em></span>
          <span aria-hidden="true" />
          <span className="new-media-col-head upgrade-head">Replacement<em><span>CF Score</span><i>Peers</i></em></span>
          <span className="new-media-col-head upgrade-results-head">Releases</span>
          {!rows.length && <div className="replace-empty-row"><EmptyState icon={tabInfo.icon} title={EMPTY[tab].title} detail={EMPTY[tab].detail} /></div>}
          {rows.map((item) => {
            const isPickable = pickable(item)
            const on = isPickable && picked.has(item.target_key)
            const tokens = item.candidate_title && item.current_release ? diffReleaseTokens(item.candidate_title, item.current_release) : []
            const qualityChanged = Boolean(item.candidate_quality) && item.candidate_quality !== item.current_quality
            const delta = (item.candidate_score ?? 0) - (item.current_score ?? 0)
            const indexerBadge = badgeColor(item.indexer_color ?? undefined)
            const label = stateLabel(item.execution_state)
            const rowBusy = busy === item.target_key
            return <div className="upgrade-row" key={item.target_key}>
              <div className="upgrade-cell upgrade-target">
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  aria-label={`Select ${item.title}`}
                  className={`new-media-check ${on ? 'on' : ''}`}
                  disabled={!isPickable}
                  onClick={() => togglePicked(item.target_key)}
                >{on && <Icon name="check" size={11} />}</button>
                <Tooltip placement="left" delay={250} content={<span className="upgrade-identity">
                  <span className="upgrade-identity-art">
                    {item.poster_ref ? <PosterImage reference={item.poster_ref} title={item.title} /> : <Icon name={item.media_type === 'shows' ? 'tv-screen' : 'clapper'} size={28} />}
                  </span>
                  <span className="upgrade-identity-copy">
                    <strong>{item.title}</strong>
                    <small>{[item.year ? `${item.year}` : undefined, item.season_number != null ? `Season ${item.season_number}` : undefined].filter(Boolean).join(' · ') || (item.media_type === 'shows' ? 'Show' : 'Movie')}</small>
                    <code>{item.media_present === false ? 'Files missing or incomplete' : item.media_present ? 'Files present on disk' : 'Media not identified'}</code>
                  </span>
                </span>}>
                  <span className="upgrade-poster">
                    {item.poster_ref ? <PosterImage reference={item.poster_ref} title={item.title} /> : <Icon name={item.media_type === 'shows' ? 'tv-screen' : 'clapper'} size={16} />}
                  </span>
                </Tooltip>
                <span className="upgrade-target-copy">
                  <strong title={item.current_release ?? item.title}>{item.current_release ?? item.title}</strong>
                  <span className="upgrade-target-meta">
                    {item.current_quality && <span className="release-pill">{qualityLabel(item.current_quality) || item.current_quality}</span>}
                    <Tooltip content="Every tracker behind this release reported the torrent as removed."><span className="release-pill release-blocked"><Icon name="blocklist" size={11} />Source lost</span></Tooltip>
                    {item.media_present === false && <Badge tone="amber">Files missing</Badge>}
                    {item.blocked_reason && <Badge tone="amber">Needs identity</Badge>}
                  </span>
                </span>
                <span className="upgrade-score-col">
                  {item.current_score != null && <span className="upgrade-score"><b>{item.current_score}</b></span>}
                </span>
              </div>
              <span className="upgrade-arrow" aria-hidden="true"><Icon name="caret" size={16} /></span>
              <div className="upgrade-cell upgrade-change">
                {item.candidate_title ? <span className="upgrade-change-copy">
                  <strong title={item.candidate_title}>{tokens.length ? tokens.map((token, index) => <Fragment key={index}>
                    <span className="upgrade-token-sep">{token.sep}</span>
                    <span className={token.changed ? 'upgrade-token-new' : 'upgrade-token-same'}>{token.text}</span>
                  </Fragment>) : item.candidate_title}</strong>
                  <small className="upgrade-change-meta">
                    {item.indexer && <span
                      className="release-pill release-pill-indexer"
                      style={indexerBadge ? { color: indexerBadge.text, borderColor: indexerBadge.border, background: indexerBadge.background } : undefined}
                    ><i>Indexer</i>{item.indexer}</span>}
                    {qualityChanged && <span className="upgrade-quality-change">
                      <span className="release-pill">{qualityLabel(item.current_quality) || 'Unknown'}</span>
                      <Icon name="caret" size={14} />
                      <span className="release-pill upgrade-quality-new">{qualityLabel(item.candidate_quality)}</span>
                    </span>}
                    {item.candidate_size != null && <span className="upgrade-fact">{formatBytes(item.candidate_size)}</span>}
                  </small>
                </span> : <span className="upgrade-change-copy replace-missing">
                  <span className={item.blocked_reason ? 'replace-missing-blocked' : ''}>{item.searching && <span className="replace-spinner" aria-hidden="true" />}{missingReason(item)}</span>
                </span>}
                <span className="upgrade-action">
                  {item.candidate_title && <>
                    <span className="upgrade-score-col">
                      <span className="upgrade-score">
                        <b>{item.candidate_score}</b>
                        {delta !== 0 && <em className={delta > 0 ? 'cf-plus' : 'cf-minus'}>{delta > 0 ? '+' : ''}{delta}</em>}
                      </span>
                    </span>
                    <span className="upgrade-peers">
                      <Tooltip className={`release-pill release-pill-swarm swarm-${swarmHealth(item.seeders)}`} content={`${item.seeders ?? 'N/A'} seeders, ${item.leechers ?? 'N/A'} leechers`}>{formatPeers(item.seeders, item.leechers)}</Tooltip>
                    </span>
                  </>}
                  {label && <Tooltip content={item.error ?? label}><Badge tone={stateTone(item.execution_state)}>{label}</Badge></Tooltip>}
                  {item.execution_state === 'handoff_attention' && <Button variant="secondary" icon="refresh" disabled={rowBusy} onClick={() => void act(item.target_key, async () => { await upgradesApi.retryUpgradeHandoff(item.candidate_id!) })}>{rowBusy ? 'Checking…' : 'Retry'}</Button>}
                  {item.execution_state === 'cleanup_required' && <Button variant="primary" icon="check" disabled={rowBusy} onClick={() => setFinishing(item)}>Finish</Button>}
                </span>
              </div>
              <div className="upgrade-cell upgrade-results-cell">
                {item.tab === 'found' || item.tab === 'in_progress'
                  ? <Button variant="ghost" icon="search" disabled={item.tab === 'in_progress' || rowBusy} onClick={() => void openReview(item)}>View All</Button>
                  : item.tab === 'dismissed'
                    ? <Button variant="ghost" icon="refresh" disabled={busy === 'decide'} onClick={() => void decideAll([item], 'restore')}>Restore</Button>
                    : <Button variant="ghost" icon="search" disabled={!searchable(item) || item.searching || busy === 'search'} onClick={() => void search([item])}>{item.searching ? 'Searching' : 'Search'}</Button>}
              </div>
            </div>
          })}
        </div>

        <div className="new-media-add">
          {bulkProgress !== null
            ? <span className="replace-bulk-progress"><span className="replace-spinner" aria-hidden="true" />Searching all titles · {Math.round(bulkProgress)}%</span>
            : <span>{chosen.length ? `${chosen.length} selected` : 'Select titles to search, snooze or ignore them together.'}</span>}
          <div className="toolbar-spacer" />
          {tab === 'dismissed'
            ? <Button variant="secondary" icon="refresh" disabled={!chosen.length || Boolean(busy)} onClick={() => void decideAll(chosen, 'restore')}>Return to queue</Button>
            : tab !== 'in_progress' && <>
              <Button variant="ghost" disabled={!chosen.some(dismissable) || Boolean(busy)} onClick={() => void decideAll(chosen.filter(dismissable), 'ignore')}>Ignore</Button>
              <Button variant="ghost" disabled={!chosen.some(dismissable) || Boolean(busy)} onClick={() => void decideAll(chosen.filter(dismissable), 'snooze')}>Snooze 7 days</Button>
              {chosen.some(searchable)
                ? <Button variant="primary" icon="search" disabled={Boolean(busy)} onClick={() => void search(chosen.filter(searchable))}>{`Search ${chosen.filter(searchable).length} ${chosen.filter(searchable).length === 1 ? 'title' : 'titles'}`}</Button>
                : <Button variant="secondary" icon="search" disabled={Boolean(busy) || bulkProgress !== null || !(counts.needs_search + counts.found)} onClick={() => void search('all')}>Search all</Button>}
            </>}
        </div>
      </div>
    </>}

    {review && <Modal
      title={review.title}
      titleSuffix={review.year ? <span className="upgrade-results-title-year"> ({review.year})</span> : undefined}
      eyebrow={review.season_number != null ? `SEASON ${review.season_number}` : 'REPLACE MEDIA'}
      className="upgrade-results-modal"
      onClose={() => { if (!busy) closeReview() }}
      footer={<>
        <span className="wizard-foot-count">{results ? `${results.results.length} Torrent${results.results.length === 1 ? '' : 's'} found` : ''}</span>
        <span className="modal-foot-spacer" />
        <Select className="replace-client-select" aria-label="qBittorrent client" value={selectedClient} onChange={(event) => setSelectedClient(event.target.value)}>
          <option value="">qBittorrent client…</option>
          {reviewClients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
        </Select>
        <Button variant="ghost" disabled={Boolean(busy)} onClick={closeReview}>Cancel</Button>
        <Button variant="primary" icon="download" disabled={Boolean(busy) || !selectedClient || !selectedEligible} onClick={download}>Download replacement</Button>
      </>}
    >
      <div className="upgrade-results-current">
        {(() => {
          const { resolution, type } = splitQuality(review.current_quality ?? undefined)
          const score = review.current_score ?? 0
          return <div className="upgrade-results-current-row">
            <Tooltip asChild content={review.current_quality || 'Unknown quality'}>
              <span className="release-quality">
                {resolution ? <strong>{resolution}</strong> : null}
                <small>{type || 'Unknown'}</small>
              </span>
            </Tooltip>
            <span className="release-name">
              <strong>{review.current_release}</strong>
              <span className="release-tags">
                <span className="release-pill upgrade-current-release-pill">Current release</span>
                <span className="release-pill release-blocked"><Icon name="blocklist" size={11} />Source lost</span>
              </span>
            </span>
            <span className={`release-numeric release-score ${score < 0 ? 'negative' : score > 0 ? 'positive' : ''}`}>{score > 0 ? `+${score}` : score}</span>
          </div>
        })()}
      </div>
      {results && <div className="upgrade-results-browser">
        <ReleaseResults
          results={reviewReleases}
          selectedId={selectedResultId}
          onSelect={setSelectedResultId}
          disabled={Boolean(busy)}
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
          label={`Replacement search results for ${review.title}`}
          empty="No cached releases were returned. Search again to refresh them."
        />
      </div>}
    </Modal>}

    {finishing && <Modal
      title="Finish replacement"
      eyebrow={finishing.title}
      icon="check"
      onClose={() => { if (!busy) setFinishing(null) }}
      footer={<><span className="modal-foot-spacer" /><Button variant="ghost" disabled={Boolean(busy)} onClick={() => setFinishing(null)}>Close</Button></>}
    >
      <div className="upgrade-modal-copy modal-copy-inset">
        <p><strong>{finishing.candidate_title}</strong> is downloaded and verified on disk. Choose what happens to the old copy:</p>
      </div>
      <div className="replace-choices">
        <button type="button" className="replace-choice" disabled={Boolean(busy)} onClick={() => void retire(finishing)}>
          <Icon name="tracker" size={16} /><strong>Retire old torrents</strong><span>Remove the old torrents from qBittorrent. Files and backups are kept.</span>
        </button>
        <button type="button" className="replace-choice danger" disabled={Boolean(busy)} onClick={() => void cleanup(finishing)}>
          <Icon name="close" size={16} /><strong>Delete old files</strong><span>Preview, then delete the superseded files. Retire the old torrents first.</span>
        </button>
        <button type="button" className="replace-choice" disabled={Boolean(busy)} onClick={() => void finish(finishing)}>
          <Icon name="check" size={16} /><strong>Keep and finish</strong><span>Close this replacement and keep everything that is left.</span>
        </button>
      </div>
    </Modal>}
  </div>
}
