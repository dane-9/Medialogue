import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type {
  DownloadClient,
  DownloadClientCategory,
  InteractiveSearchJob,
  InteractiveSearchResult,
  QualityProfile,
  ShowAcquisitionPreview,
  ShowAcquisitionSeasonPreview,
} from '../types'
import { Icon } from './Icon'
import { Badge, Button, Select } from './ui'

type ShowChoice = {
  tmdbId: number
  title: string
  year?: number
  poster?: string
  overview?: string
}

type SeasonSearchState = {
  jobId?: string
  job?: InteractiveSearchJob
  selectedResultId?: string
  starting?: boolean
  submitted?: boolean
  submissionError?: string
}

function posterUrl(reference?: string) {
  if (!reference) return undefined
  if (reference.startsWith('http')) return reference
  return `https://image.tmdb.org/t/p/w185${reference.startsWith('/') ? reference : `/${reference}`}`
}

function formatBytes(bytes?: number) {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`
}

function matchedFormatNames(result: InteractiveSearchResult): string[] {
  const raw = result.customFormatSnapshot.matched_format_names ?? result.customFormatSnapshot.matchedFormatNames
  return Array.isArray(raw) ? raw.map(String).filter(Boolean) : []
}

function seasonLabel(season: ShowAcquisitionSeasonPreview) {
  return season.seasonNumber === 0 ? 'Specials' : `Season ${season.seasonNumber}`
}

export function ShowAcquisitionWizard({ show, onBack, onClose, onComplete }: {
  show: ShowChoice
  onBack: () => void
  onClose: () => void
  onComplete: (showId: string) => void
}) {
  const [preview, setPreview] = useState<ShowAcquisitionPreview | null>(null)
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [profileId, setProfileId] = useState('')
  const [clientId, setClientId] = useState('')
  const [categories, setCategories] = useState<DownloadClientCategory[]>([])
  const [category, setCategory] = useState('')
  const [includeSpecials, setIncludeSpecials] = useState(false)
  const [setupLoading, setSetupLoading] = useState(true)
  const [categoryLoading, setCategoryLoading] = useState(false)
  const [step, setStep] = useState<'setup' | 'search'>('setup')
  const [searchProfileId, setSearchProfileId] = useState('')
  const [activeSeasonNumber, setActiveSeasonNumber] = useState<number | null>(null)
  const [seasonStates, setSeasonStates] = useState<Record<number, SeasonSearchState>>({})
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [committedShowId, setCommittedShowId] = useState('')

  useEffect(() => {
    let alive = true
    setSetupLoading(true)
    Promise.all([api.showAcquisitionPreview(show.tmdbId), api.qualityProfiles(), api.downloadClients()])
      .then(([showPreview, profileRows, clientRows]) => {
        if (!alive) return
        const showClients = clientRows.filter((client) => client.enabled && client.scope === 'shows')
        setPreview(showPreview)
        setProfiles(profileRows)
        setClients(showClients)
        setProfileId((current) => current || profileRows[0]?.id || '')
        setClientId((current) => current || showClients[0]?.id || '')
        setError(profileRows.length ? (showClients.length ? '' : 'Configure an enabled Shows qBittorrent client before acquiring a show.') : 'Create a Quality Profile before acquiring a show.')
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not load Show acquisition settings.'))
      .finally(() => { if (alive) setSetupLoading(false) })
    return () => { alive = false }
  }, [show.tmdbId])

  const selectedClient = clients.find((client) => client.id === clientId)
  const selectedCategory = categories.find((item) => item.name === category)

  useEffect(() => {
    if (!clientId) { setCategories([]); setCategory(''); return }
    let alive = true
    setCategoryLoading(true)
    setError('')
    api.downloadClientCategories(clientId)
      .then((rows) => {
        if (!alive) return
        setCategories(rows)
        const defaultName = selectedClient?.category
        const next = (defaultName && rows.some((item) => item.name === defaultName) ? defaultName : rows.find((item) => item.isDefault)?.name) || rows[0]?.name || ''
        setCategory(next)
        if (!rows.length) setError('This qBittorrent instance has no categories. Create a category in qBittorrent before continuing.')
      })
      .catch((reason) => {
        if (!alive) return
        setCategories([]); setCategory('')
        setError(reason instanceof Error ? reason.message : 'Could not load qBittorrent categories.')
      })
      .finally(() => { if (alive) setCategoryLoading(false) })
    return () => { alive = false }
  }, [clientId, selectedClient?.category])

  const visibleSeasons = useMemo(() => {
    const rows = (preview?.seasons ?? []).filter((season) => includeSpecials || season.seasonNumber !== 0)
    return [...rows].sort((left, right) => left.seasonNumber - right.seasonNumber)
  }, [preview, includeSpecials])

  const activeSeason = visibleSeasons.find((season) => season.seasonNumber === activeSeasonNumber)
  const activeState = activeSeasonNumber === null ? undefined : seasonStates[activeSeasonNumber]
  const activeJobId = activeState?.jobId

  useEffect(() => {
    if (!activeJobId || activeSeasonNumber === null || step !== 'search') return
    let alive = true
    let timer = 0
    const refresh = async () => {
      try {
        const value = await api.searchJob(activeJobId)
        if (!alive) return
        setSeasonStates((current) => ({
          ...current,
          [activeSeasonNumber]: { ...current[activeSeasonNumber], job: value, starting: false },
        }))
        if (value.status === 'failed') setError(String(value.error?.message ?? `${activeSeasonNumber === 0 ? 'Specials' : `Season ${activeSeasonNumber}`} search failed.`))
        if (!['completed', 'failed', 'cancelled', 'interrupted'].includes(value.status)) timer = window.setTimeout(() => void refresh(), 700)
      } catch (reason) {
        if (alive) setError(reason instanceof Error ? reason.message : 'Could not refresh season search results.')
      }
    }
    void refresh()
    return () => { alive = false; if (timer) window.clearTimeout(timer) }
  }, [activeJobId, activeSeasonNumber, step])

  const continueToSeasons = () => {
    if (!profileId || !clientId || !category || !preview) return
    if (searchProfileId && searchProfileId !== profileId) {
      setSeasonStates({})
      setActiveSeasonNumber(null)
    }
    setSearchProfileId(profileId)
    setError('')
    setStep('search')
  }

  const startSeasonSearch = async (season: ShowAcquisitionSeasonPreview, force = false) => {
    const current = seasonStates[season.seasonNumber]
    setActiveSeasonNumber(season.seasonNumber)
    if (current?.submitted) return
    if (current?.jobId && !force) return
    setError('')
    setSeasonStates((states) => ({
      ...states,
      [season.seasonNumber]: {
        ...(states[season.seasonNumber] ?? {}),
        starting: true,
        job: force ? undefined : states[season.seasonNumber]?.job,
        jobId: force ? undefined : states[season.seasonNumber]?.jobId,
        selectedResultId: force ? undefined : states[season.seasonNumber]?.selectedResultId,
        submissionError: undefined,
      },
    }))
    try {
      const accepted = await api.startUnattachedShowSeasonSearch(show.tmdbId, profileId, season.seasonNumber)
      setSeasonStates((states) => ({
        ...states,
        [season.seasonNumber]: {
          ...(states[season.seasonNumber] ?? {}),
          jobId: accepted.job_id,
          job: undefined,
          starting: false,
          selectedResultId: undefined,
          submissionError: undefined,
        },
      }))
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : `Could not search ${seasonLabel(season)}.`
      setSeasonStates((states) => ({
        ...states,
        [season.seasonNumber]: { ...(states[season.seasonNumber] ?? {}), starting: false, submissionError: message },
      }))
      setError(message)
    }
  }

  const selectResult = (seasonNumber: number, resultId: string) => {
    setSeasonStates((states) => {
      if (states[seasonNumber]?.submitted) return states
      return {
        ...states,
        [seasonNumber]: {
          ...(states[seasonNumber] ?? {}),
          selectedResultId: resultId,
          submissionError: undefined,
        },
      }
    })
  }

  const selectedSeasons = visibleSeasons.filter((season) => Boolean(seasonStates[season.seasonNumber]?.selectedResultId))
  const pendingSeasons = selectedSeasons.filter((season) => !seasonStates[season.seasonNumber]?.submitted)
  const selectedResult = activeState?.job?.results.find((result) => result.id === activeState.selectedResultId)
  const canContinue = Boolean(preview && profileId && clientId && category && visibleSeasons.length && !setupLoading && !categoryLoading)

  const startDownloads = async () => {
    if (!clientId || !category || !pendingSeasons.length) return
    setSubmitting(true); setError('')
    let showId = committedShowId
    let succeeded = 0
    let failed = 0
    for (const season of pendingSeasons) {
      const state = seasonStates[season.seasonNumber]
      const result = state?.job?.results.find((item) => item.id === state.selectedResultId)
      if (!result || !result.qualityAllowed) {
        failed += 1
        setSeasonStates((states) => ({
          ...states,
          [season.seasonNumber]: { ...(states[season.seasonNumber] ?? {}), submissionError: 'The selected release is no longer available or allowed by this Quality Profile.' },
        }))
        continue
      }
      try {
        const response = await api.downloadSearchResult(result.id, { download_client_id: clientId, category })
        showId = response.show_id || showId
        succeeded += 1
        setSeasonStates((states) => ({
          ...states,
          [season.seasonNumber]: { ...(states[season.seasonNumber] ?? {}), submitted: true, submissionError: undefined },
        }))
      } catch (reason) {
        failed += 1
        const message = reason instanceof Error ? reason.message : `Could not start ${seasonLabel(season)}.`
        setSeasonStates((states) => ({
          ...states,
          [season.seasonNumber]: { ...(states[season.seasonNumber] ?? {}), submissionError: message },
        }))
      }
    }
    if (showId) setCommittedShowId(showId)
    setSubmitting(false)
    if (failed === 0 && showId) {
      onComplete(showId)
      return
    }
    if (failed > 0) setError(`${succeeded} season pack${succeeded === 1 ? '' : 's'} started; ${failed} failed. Successful downloads are already running. Review the marked season tab${failed === 1 ? '' : 's'} and retry.`)
  }

  const activeIndexerSummary = useMemo(() => {
    const job = activeState?.job
    if (!job) return activeState?.starting ? 'Starting search…' : ''
    const done = job.indexers.filter((item) => ['completed', 'failed', 'timeout'].includes(item.status)).length
    return `${done}/${job.indexers.length} indexers · ${job.resultTotal} season pack${job.resultTotal === 1 ? '' : 's'}`
  }, [activeState])

  return <div className="modal-backdrop" onClick={onClose}>
    <div className="acquisition-modal show-acquisition-modal" role="dialog" aria-modal="true" aria-label={`Acquire ${show.title}`} onClick={(event) => event.stopPropagation()}>
      <header className="acquisition-head">
        <button className="icon-button" type="button" onClick={step === 'setup' ? onBack : () => { setStep('setup'); setError('') }} aria-label="Back"><Icon name="chevron" size={17} /></button>
        <div className="acquisition-title-block"><span className="eyebrow">MANUAL SHOW ACQUISITION</span><h2>{preview?.title || show.title}{(preview?.year || show.year) ? <span> ({preview?.year || show.year})</span> : null}</h2></div>
        <div className="acquisition-steps" aria-label="Acquisition steps"><span className={step === 'setup' ? 'active' : 'done'}>1 Setup</span><i /><span className={step === 'search' ? 'active' : ''}>2 Select seasons</span></div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close"><Icon name="close" size={18} /></button>
      </header>

      {step === 'setup' ? <div className="acquisition-setup">
        <aside className="acquisition-movie-card">
          <div className="acquisition-poster">{posterUrl(preview?.posterRef || show.poster) ? <img src={posterUrl(preview?.posterRef || show.poster)} alt="" /> : <Icon name="tv" size={28} />}</div>
          <div><strong>{preview?.title || show.title}</strong><span>{preview?.year || show.year || 'Year unknown'} · TMDB {show.tmdbId}</span>{(preview?.overview || show.overview) && <p>{preview?.overview || show.overview}</p>}</div>
        </aside>
        <section className="acquisition-form">
          <label><span className="field-label">Quality Profile</span><Select value={profileId} onChange={(event) => setProfileId(event.target.value)} disabled={setupLoading || Boolean(committedShowId)}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</Select><small>Applies to every season-pack search in this acquisition.</small></label>
          <label><span className="field-label">qBittorrent</span><Select value={clientId} onChange={(event) => setClientId(event.target.value)} disabled={setupLoading}>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</Select><small>{selectedClient ? `${selectedClient.health} · ${selectedClient.url}` : 'Choose the client that should receive the selected season packs.'}</small></label>
          <label><span className="field-label">Category</span><Select value={category} onChange={(event) => setCategory(event.target.value)} disabled={!clientId || categoryLoading}>{categoryLoading && <option value="">Loading categories…</option>}{!categoryLoading && categories.map((item) => <option key={item.name} value={item.name}>{item.name}{item.isDefault ? ' · default' : ''}</option>)}</Select><small>The category owns the destination path in qBittorrent.</small></label>
          <div className="acquisition-destination"><span>Destination</span><strong>{selectedCategory?.resolvedSavePath || (categoryLoading ? 'Loading…' : 'Unavailable')}</strong>{selectedCategory && !selectedCategory.savePath && <small>Uses the qBittorrent default download directory because this category has no explicit path.</small>}</div>
          <label className="acquisition-specials-toggle"><span><strong>Include Specials</strong><small>Off by default. When enabled, Specials appears as its own season-pack search tab.</small></span><input type="checkbox" checked={includeSpecials} disabled={Boolean(seasonStates[0]?.submitted)} onChange={(event) => { setIncludeSpecials(event.target.checked); if (!event.target.checked && activeSeasonNumber === 0) setActiveSeasonNumber(null) }} /></label>
          {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
        </section>
      </div> : <div className="acquisition-search show-acquisition-search">
        <div className="acquisition-search-toolbar">
          <div><strong>{profiles.find((item) => item.id === profileId)?.name || 'Quality Profile'}</strong><span>{selectedClient?.name} · {category} · {selectedCategory?.resolvedSavePath || 'destination unavailable'}</span></div>
          <span>{selectedSeasons.length ? `${selectedSeasons.length} season${selectedSeasons.length === 1 ? '' : 's'} selected` : 'No seasons selected yet'}</span>
        </div>
        <div className="show-season-tabs" role="tablist" aria-label="Show seasons">
          {visibleSeasons.map((season) => {
            const state = seasonStates[season.seasonNumber]
            const isActive = activeSeasonNumber === season.seasonNumber
            const isSelected = Boolean(state?.selectedResultId)
            return <button
              key={season.seasonNumber}
              type="button"
              role="tab"
              aria-selected={isActive}
              className={`${isActive ? 'active' : ''} ${isSelected ? 'selected' : ''} ${state?.submissionError ? 'error' : ''}`}
              onClick={() => void startSeasonSearch(season)}
            >
              <span>{isSelected && <Icon name="check" size={12} />}{seasonLabel(season)}</span>
              <small>{state?.submitted ? 'Started' : state?.starting ? 'Searching…' : state?.job ? `${state.job.resultTotal} packs` : `${season.episodeCount} eps`}</small>
            </button>
          })}
        </div>
        {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
        {activeSeason ? <>
          <div className="show-season-search-status">
            <div><strong>{seasonLabel(activeSeason)}</strong><span>{activeState?.submissionError || activeIndexerSummary || 'Search starts when this tab is opened.'}</span></div>
            {activeState?.job && !activeState.submitted && <Button variant="ghost" type="button" disabled={activeState.starting} onClick={() => void startSeasonSearch(activeSeason, true)}>Search again</Button>}
          </div>
          <div className="acquisition-search-body">
            <div className="acquisition-results" role="listbox" aria-label={`${seasonLabel(activeSeason)} season-pack results`}>
              {activeState?.job?.results.map((result) => <button key={result.id} type="button" role="option" aria-selected={activeState.selectedResultId === result.id} disabled={Boolean(activeState.submitted) || !result.qualityAllowed} className={`acquisition-result ${activeState.selectedResultId === result.id ? 'selected' : ''}`} onClick={() => selectResult(activeSeason.seasonNumber, result.id)}>
                <span className="acquisition-result-main"><strong>{result.title}</strong><span>{result.indexerName}{result.releaseGroup ? ` · ${result.releaseGroup}` : ''}</span></span>
                <span className="acquisition-result-quality">{result.quality || 'Unknown'}<small>Season pack</small></span>
                <span className={`acquisition-result-score ${(result.customFormatScore ?? 0) >= 0 ? 'positive' : 'negative'}`}>{(result.customFormatScore ?? 0) > 0 ? '+' : ''}{result.customFormatScore ?? 0}<small>CF</small></span>
                <span className="acquisition-result-stat">{formatBytes(result.size)}<small>size</small></span>
                <span className="acquisition-result-stat">{result.seeders ?? '—'}<small>seeders</small></span>
                {!result.qualityAllowed ? <Badge tone="red">Not allowed</Badge> : <Badge tone="green">Pack</Badge>}
              </button>)}
              {!activeState?.job?.results.length && <div className="acquisition-results-empty">{activeState?.starting || (activeState?.job && !['completed', 'failed'].includes(activeState.job.status)) ? `Searching for ${seasonLabel(activeSeason)} season packs…` : activeState?.job ? `No ${seasonLabel(activeSeason)} season packs were returned.` : 'Search starts when you open this season tab.'}</div>}
            </div>
            <aside className="acquisition-evidence">
              {selectedResult ? <>
                <div className="eyebrow">SELECTED SEASON PACK</div><h3>{seasonLabel(activeSeason)}</h3><p className="acquisition-release-name">{selectedResult.title}</p>
                <div className="badge-row"><Badge tone="green">Season pack</Badge>{activeState?.submitted && <Badge tone="green">Download started</Badge>}</div>
                <dl><div><dt>Quality</dt><dd>{selectedResult.quality || 'Unknown'}</dd></div><div><dt>Custom Format score</dt><dd>{(selectedResult.customFormatScore ?? 0) > 0 ? '+' : ''}{selectedResult.customFormatScore ?? 0}</dd></div><div><dt>Seeders</dt><dd>{selectedResult.seeders ?? '—'}</dd></div><div><dt>Size</dt><dd>{formatBytes(selectedResult.size)}</dd></div><div><dt>Indexer</dt><dd>{selectedResult.indexerName}</dd></div></dl>
                {matchedFormatNames(selectedResult).length > 0 && <div className="acquisition-matches"><span>Matched formats</span><div>{matchedFormatNames(selectedResult).map((name) => <Badge key={name} tone="purple">{name}</Badge>)}</div></div>}
                {selectedResult.warnings.length > 0 && <div className="acquisition-warnings">{selectedResult.warnings.map((warning) => <p key={warning}><Icon name="alert" size={13} />{warning}</p>)}</div>}
                {activeState?.submissionError && <div className="acquisition-warnings"><p><Icon name="alert" size={13} />{activeState.submissionError}</p></div>}
                <div className="acquisition-confirm-destination"><span>Download to</span><strong>{selectedClient?.name} / {category}</strong><small>{selectedCategory?.resolvedSavePath}</small></div>
              </> : <div className="acquisition-evidence-empty"><Icon name="tv" size={24} /><strong>Select one season pack</strong><p>Each season can have exactly one selected pack. Selecting a pack turns its season tab green.</p></div>}
            </aside>
          </div>
        </> : <div className="show-season-tab-empty"><Icon name="search" size={26} /><strong>Choose a season</strong><p>Each season is searched lazily. Opening a season tab starts that season's indexer search; unopened seasons do not generate searches.</p></div>}
      </div>}

      <footer className="acquisition-foot">
        <span>{step === 'setup' ? 'Nothing is added to Medialogue until at least one selected season pack is accepted by qBittorrent.' : committedShowId ? 'The Show now exists in Medialogue because at least one season download has started.' : 'Select one pack in any season you want to download. Green tabs are selected.'}</span>
        <div>{step === 'setup' ? <><Button variant="ghost" type="button" onClick={onClose}>Cancel</Button><Button variant="primary" type="button" disabled={!canContinue} onClick={continueToSeasons}>Continue</Button></> : <><Button variant="ghost" type="button" onClick={() => { setStep('setup'); setError('') }}>Edit setup</Button>{committedShowId && pendingSeasons.length === 0 ? <Button variant="primary" type="button" onClick={() => onComplete(committedShowId)}>Open Show</Button> : <Button variant="primary" type="button" disabled={!pendingSeasons.length || submitting} onClick={() => void startDownloads()}>{submitting ? 'Starting downloads…' : `${committedShowId ? 'Retry' : 'Start'} Downloads (${pendingSeasons.length})`}</Button>}</>}</div>
      </footer>
    </div>
  </div>
}
