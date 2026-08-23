import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { DownloadClient, DownloadClientCategory, InteractiveSearchJob, InteractiveSearchResult, QualityProfile } from '../types'
import { Icon } from './Icon'
import { Badge, Button, Select } from './ui'

type MovieChoice = {
  tmdbId: number
  title: string
  year?: number
  poster?: string
  overview?: string
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

export function MovieAcquisitionWizard({ movie, onBack, onClose, onComplete }: {
  movie: MovieChoice
  onBack: () => void
  onClose: () => void
  onComplete: (movieId: string) => void
}) {
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [profileId, setProfileId] = useState('')
  const [clientId, setClientId] = useState('')
  const [categories, setCategories] = useState<DownloadClientCategory[]>([])
  const [category, setCategory] = useState('')
  const [setupLoading, setSetupLoading] = useState(true)
  const [categoryLoading, setCategoryLoading] = useState(false)
  const [step, setStep] = useState<'setup' | 'search'>('setup')
  const [jobId, setJobId] = useState('')
  const [searchedProfileId, setSearchedProfileId] = useState('')
  const [job, setJob] = useState<InteractiveSearchJob | null>(null)
  const [selectedResultId, setSelectedResultId] = useState('')
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    let alive = true
    setSetupLoading(true)
    Promise.all([api.qualityProfiles(), api.downloadClients()])
      .then(([profileRows, clientRows]) => {
        if (!alive) return
        const movieClients = clientRows.filter((client) => client.enabled && client.scope === 'movies')
        setProfiles(profileRows)
        setClients(movieClients)
        setProfileId((current) => current || profileRows[0]?.id || '')
        setClientId((current) => current || movieClients[0]?.id || '')
        setError(profileRows.length ? (movieClients.length ? '' : 'Configure an enabled Movies qBittorrent client before acquiring a movie.') : 'Create a Movie Quality Profile before acquiring a movie.')
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not load acquisition settings.'))
      .finally(() => { if (alive) setSetupLoading(false) })
    return () => { alive = false }
  }, [])

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

  useEffect(() => {
    if (!jobId || step !== 'search') return
    let alive = true
    let timer = 0
    const refresh = async () => {
      try {
        const value = await api.searchJob(jobId)
        if (!alive) return
        setJob(value)
        if (value.status === 'failed') setError(String(value.error?.message ?? 'Interactive search failed.'))
        if (!['completed', 'failed', 'cancelled', 'interrupted'].includes(value.status)) timer = window.setTimeout(() => void refresh(), 700)
      } catch (reason) {
        if (alive) setError(reason instanceof Error ? reason.message : 'Could not refresh search results.')
      }
    }
    void refresh()
    return () => { alive = false; if (timer) window.clearTimeout(timer) }
  }, [jobId, step])

  const search = async () => {
    if (!profileId || !clientId || !category) return
    if (jobId && searchedProfileId === profileId) {
      setError('')
      setStep('search')
      return
    }
    setStarting(true); setError(''); setJob(null); setSelectedResultId('')
    try {
      const accepted = await api.startUnattachedMovieSearch(movie.tmdbId, profileId)
      setJobId(accepted.job_id)
      setSearchedProfileId(profileId)
      setStep('search')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not start interactive search.')
    } finally { setStarting(false) }
  }

  const selectedResult = job?.results.find((result) => result.id === selectedResultId)
  const canSearch = Boolean(profileId && clientId && category && !setupLoading && !categoryLoading)

  const startDownload = async () => {
    if (!selectedResult || !clientId || !category || !selectedResult.qualityAllowed) return
    setStarting(true); setError('')
    try {
      const response = await api.downloadSearchResult(selectedResult.id, { download_client_id: clientId, category })
      if (!response.movie_id) throw new Error('qBittorrent accepted the release, but Medialogue did not return the new Movie id.')
      onComplete(response.movie_id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not start this download.')
    } finally { setStarting(false) }
  }

  const indexerSummary = useMemo(() => {
    if (!job) return ''
    const done = job.indexers.filter((item) => ['completed', 'failed', 'timeout'].includes(item.status)).length
    return `${done}/${job.indexers.length} indexers · ${job.resultTotal} result${job.resultTotal === 1 ? '' : 's'}`
  }, [job])

  return <div className="modal-backdrop" onClick={onClose}>
    <div className="acquisition-modal" role="dialog" aria-modal="true" aria-label={`Acquire ${movie.title}`} onClick={(event) => event.stopPropagation()}>
      <header className="acquisition-head">
        <button className="icon-button" type="button" onClick={step === 'setup' ? onBack : () => { setStep('setup'); setError('') }} aria-label="Back"><Icon name="chevron" size={17} /></button>
        <div className="acquisition-title-block"><span className="eyebrow">MANUAL ACQUISITION</span><h2>{movie.title}{movie.year ? <span> ({movie.year})</span> : null}</h2></div>
        <div className="acquisition-steps" aria-label="Acquisition steps"><span className={step === 'setup' ? 'active' : 'done'}>1 Setup</span><i /><span className={step === 'search' ? 'active' : ''}>2 Select release</span></div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close"><Icon name="close" size={18} /></button>
      </header>

      {step === 'setup' ? <div className="acquisition-setup">
        <aside className="acquisition-movie-card">
          <div className="acquisition-poster">{posterUrl(movie.poster) ? <img src={posterUrl(movie.poster)} alt="" /> : <Icon name="film" size={28} />}</div>
          <div><strong>{movie.title}</strong><span>{movie.year || 'Year unknown'} · TMDB {movie.tmdbId}</span>{movie.overview && <p>{movie.overview}</p>}</div>
        </aside>
        <section className="acquisition-form">
          <label><span className="field-label">Quality Profile</span><Select value={profileId} onChange={(event) => setProfileId(event.target.value)} disabled={setupLoading}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</Select><small>Controls quality ordering and Custom Format scoring for this search.</small></label>
          <label><span className="field-label">qBittorrent</span><Select value={clientId} onChange={(event) => setClientId(event.target.value)} disabled={setupLoading}>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</Select><small>{selectedClient ? `${selectedClient.health} · ${selectedClient.url}` : 'Choose the client that should receive the selected torrent.'}</small></label>
          <label><span className="field-label">Category</span><Select value={category} onChange={(event) => setCategory(event.target.value)} disabled={!clientId || categoryLoading}>{categoryLoading && <option value="">Loading categories…</option>}{!categoryLoading && categories.map((item) => <option key={item.name} value={item.name}>{item.name}{item.isDefault ? ' · default' : ''}</option>)}</Select><small>The category is owned by qBittorrent; Medialogue does not override its save path.</small></label>
          <div className="acquisition-destination"><span>Destination</span><strong>{selectedCategory?.resolvedSavePath || (categoryLoading ? 'Loading…' : 'Unavailable')}</strong>{selectedCategory && !selectedCategory.savePath && <small>Uses the qBittorrent default download directory because this category has no explicit path.</small>}</div>
          {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
        </section>
      </div> : <div className="acquisition-search">
        <div className="acquisition-search-toolbar">
          <div><strong>{profiles.find((item) => item.id === profileId)?.name || 'Quality Profile'}</strong><span>{selectedClient?.name} · {category} · {selectedCategory?.resolvedSavePath || 'destination unavailable'}</span></div>
          <span>{job ? indexerSummary : 'Starting search…'}</span>
        </div>
        {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
        <div className="acquisition-search-body">
          <div className="acquisition-results" role="listbox" aria-label="Torrent search results">
            {job?.results.map((result) => <button key={result.id} type="button" role="option" aria-selected={selectedResultId === result.id} className={`acquisition-result ${selectedResultId === result.id ? 'selected' : ''}`} onClick={() => setSelectedResultId(result.id)}>
              <span className="acquisition-result-main"><strong>{result.title}</strong><span>{result.indexerName}{result.releaseGroup ? ` · ${result.releaseGroup}` : ''}</span></span>
              <span className="acquisition-result-quality">{result.quality || 'Unknown'}{result.edition ? <small>{result.edition}</small> : null}</span>
              <span className={`acquisition-result-score ${(result.customFormatScore ?? 0) >= 0 ? 'positive' : 'negative'}`}>{(result.customFormatScore ?? 0) > 0 ? '+' : ''}{result.customFormatScore ?? 0}<small>CF</small></span>
              <span className="acquisition-result-stat">{formatBytes(result.size)}<small>size</small></span>
              <span className="acquisition-result-stat">{result.seeders ?? '—'}<small>seeders</small></span>
              {!result.qualityAllowed && <Badge tone="red">Not allowed</Badge>}
            </button>)}
            {!job?.results.length && <div className="acquisition-results-empty">{job && ['completed', 'failed'].includes(job.status) ? 'No releases were returned.' : 'Searching configured indexers…'}</div>}
          </div>
          <aside className="acquisition-evidence">
            {selectedResult ? <>
              <div className="eyebrow">SELECTED RELEASE</div><h3>{selectedResult.quality || 'Unknown quality'}</h3><p className="acquisition-release-name">{selectedResult.title}</p>
              <dl><div><dt>Custom Format score</dt><dd>{(selectedResult.customFormatScore ?? 0) > 0 ? '+' : ''}{selectedResult.customFormatScore ?? 0}</dd></div><div><dt>Seeders</dt><dd>{selectedResult.seeders ?? '—'}</dd></div><div><dt>Size</dt><dd>{formatBytes(selectedResult.size)}</dd></div><div><dt>Indexer</dt><dd>{selectedResult.indexerName}</dd></div></dl>
              {matchedFormatNames(selectedResult).length > 0 && <div className="acquisition-matches"><span>Matched formats</span><div>{matchedFormatNames(selectedResult).map((name) => <Badge key={name} tone="purple">{name}</Badge>)}</div></div>}
              {selectedResult.warnings.length > 0 && <div className="acquisition-warnings">{selectedResult.warnings.map((warning) => <p key={warning}><Icon name="alert" size={13} />{warning}</p>)}</div>}
              <div className="acquisition-confirm-destination"><span>Download to</span><strong>{selectedClient?.name} / {category}</strong><small>{selectedCategory?.resolvedSavePath}</small></div>
            </> : <div className="acquisition-evidence-empty"><Icon name="search" size={24} /><strong>Select a release</strong><p>Review the parsed quality, Custom Format score, size, seeders and warnings before starting the download.</p></div>}
          </aside>
        </div>
      </div>}

      <footer className="acquisition-foot">
        <span>{step === 'setup' ? 'Nothing is added to Medialogue until Start Download succeeds.' : job?.status === 'completed' ? 'Search complete.' : 'Results appear as each indexer finishes.'}</span>
        <div>{step === 'setup' ? <><Button variant="ghost" type="button" onClick={onClose}>Cancel</Button><Button variant="primary" type="button" disabled={!canSearch || starting} onClick={() => void search()}>{starting ? 'Starting…' : (jobId && searchedProfileId === profileId ? 'Return to results' : 'Search releases')}</Button></> : <><Button variant="ghost" type="button" onClick={() => { setStep('setup'); setError('') }}>Edit setup</Button><Button variant="primary" type="button" disabled={!selectedResult || !selectedResult.qualityAllowed || starting} onClick={() => void startDownload()}>{starting ? 'Starting download…' : 'Start Download'}</Button></>}</div>
      </footer>
    </div>
  </div>
}
