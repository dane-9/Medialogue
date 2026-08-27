import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { DownloadClient, DownloadClientCategory, InteractiveSearchJob, InteractiveSearchResult, QualityProfile } from '../types'
import { Icon } from './Icon'
import { Button, Select } from './ui'
import { ReleaseResults, formatBytes } from './ReleaseResults'
import { DownloadClientField } from './DownloadClientField'
import imdbLogo from '../assets/imdb.svg'

type MovieChoice = {
  tmdbId: number
  title: string
  year?: number
  poster?: string
  overview?: string
  imdbId?: string
  imdbRating?: number
  imdbVotes?: number
}

function posterUrl(reference?: string) {
  if (!reference) return undefined
  if (reference.startsWith('http')) return reference
  return `https://image.tmdb.org/t/p/w185${reference.startsWith('/') ? reference : `/${reference}`}`
}

export function MovieDownloadWizard({ movie, onBack, onClose, onComplete }: {
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
  // Bumped by a client health recheck, so categories are fetched again after a
  // client that was down comes back. The client id does not change, so nothing
  // else would retry the load that failed while it was unreachable.
  const [clientReload, setClientReload] = useState(0)
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
        setError(profileRows.length ? (movieClients.length ? '' : 'Configure an enabled Movies qBittorrent client before downloading a movie.') : 'Create a Movie Quality Profile before downloading a movie.')
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not load download settings.'))
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
  }, [clientId, selectedClient?.category, clientReload])

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

  const search = async (force = false) => {
    if (!profileId || !clientId || !category) return
    if (!force && jobId && searchedProfileId === profileId) {
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

  useEffect(() => {
    if (step !== 'search' || !profileId || searchedProfileId === profileId) return
    void search()
  }, [profileId, step, searchedProfileId])

  const selectedResult = job?.results.find((result) => result.id === selectedResultId)
  const canSearch = Boolean(profileId && clientId && category && !setupLoading && !categoryLoading)

  const startDownload = async () => {
    if (!selectedResult || !clientId || !category || !selectedResult.qualityAllowed) return
    setStarting(true); setError('')
    try {
      const response = await api.downloadSearchResult(selectedResult.id, { download_client_id: clientId, category })
      if (!response.movie_id) throw new Error('qBittorrent accepted the release, but no movie was created.')
      onComplete(response.movie_id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not start this download.')
    } finally { setStarting(false) }
  }

  return <div className="modal-backdrop" onClick={onClose}>
    <div className="wizard-modal" role="dialog" aria-modal="true" aria-label={`Acquire ${movie.title}`} onClick={(event) => event.stopPropagation()}>
      <header className="wizard-head">
        <div className="wizard-title-block">
          <h2>{movie.title}{movie.year ? <span> ({movie.year})</span> : null}</h2>
        </div>
        {step === 'search' && <label className="wizard-profile">
          <span>Quality Profile</span>
          <Select
            value={profileId}
            aria-label="Quality Profile"
            disabled={starting}
            onChange={(event) => setProfileId(event.target.value)}
          >{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</Select>
        </label>}
        <nav className="wizard-steps" aria-label="Download steps">
          <button
            type="button"
            className={step === 'setup' ? 'active' : ''}
            aria-current={step === 'setup' ? 'step' : undefined}
            onClick={() => { setStep('setup'); setError('') }}
          ><i>1</i>Setup</button>
          <button
            type="button"
            className={step === 'search' ? 'active' : ''}
            aria-current={step === 'search' ? 'step' : undefined}
            disabled={!canSearch || starting}
            onClick={() => void search()}
          ><i>2</i>Select release</button>
        </nav>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close"><Icon name="close" size={18} /></button>
      </header>

      {step === 'setup' ? <div className="wizard-setup">
        <aside className="wizard-movie-card">
          <div className="wizard-poster">{posterUrl(movie.poster) ? <img src={posterUrl(movie.poster)} alt="" /> : <Icon name="clapper" size={28} />}</div>
          <div><strong className="wizard-title-line">{movie.title}{movie.imdbRating != null && <span className="wizard-imdb-rating"><img src={imdbLogo} alt="IMDb" /> {movie.imdbRating.toFixed(1)}{movie.imdbVotes != null ? ` · ${movie.imdbVotes.toLocaleString()} Ratings` : ''}</span>}</strong><span>{movie.year || 'Year unknown'} · TMDB {movie.tmdbId}</span>{movie.overview && <p>{movie.overview}</p>}</div>
        </aside>
        <section className="wizard-form">
          <div className="wizard-box"><label><span className="field-label">Quality Profile</span><Select value={profileId} onChange={(event) => setProfileId(event.target.value)} disabled={setupLoading}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</Select><small>Controls quality ordering and Custom Format scoring for this search.</small></label></div>
          <div className="wizard-destination"><DownloadClientField
            clients={clients}
            clientId={clientId}
            scope="movies"
            disabled={setupLoading}
            onChange={setClientId}
            onRefreshed={(rows) => { setClients(rows); setClientReload((value) => value + 1) }}
          /><label><span className="field-label">Category</span><Select value={category} onChange={(event) => setCategory(event.target.value)} disabled={!clientId || categoryLoading}>{categoryLoading && <option value="">Loading categories…</option>}{!categoryLoading && categories.map((item) => <option key={item.name} value={item.name}>{item.name}{item.isDefault ? ' · default' : ''}</option>)}</Select></label><div className="wizard-destination-path"><span>Destination</span><strong>{selectedCategory?.resolvedSavePath || (categoryLoading ? 'Loading…' : 'Unavailable')}</strong>{selectedCategory && !selectedCategory.savePath && <small>Uses the qBittorrent default download directory because this category has no explicit path.</small>}</div></div>
          {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
        </section>
      </div> : <div className="wizard-search">
        {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
        <ReleaseResults
          results={job?.results ?? []}
          selectedId={selectedResultId}
          onSelect={setSelectedResultId}
          label="Torrent search results"
          onRefresh={() => void search(true)}
          refreshing={starting}
          empty={job && ['completed', 'failed'].includes(job.status) ? 'No releases were returned.' : 'Searching configured indexers…'}
        />
      </div>}

      <footer className="wizard-foot">
        {step === 'search' && job
          ? <span className="wizard-foot-count">{job.resultTotal} Torrent{job.resultTotal === 1 ? '' : 's'} found</span>
          : null}
        <span>{step === 'setup'
          ? 'Nothing is added to your library until the download starts.'
          : selectedResult
            ? <>Downloading to <strong>{selectedClient?.name} / {category}</strong> · {formatBytes(selectedResult.size)}</>
            : job?.status === 'completed' ? null : 'Results appear as each indexer finishes.'}</span>
        <div>{step === 'setup' ? <><Button variant="secondary" type="button" onClick={onBack}>Back</Button><Button variant="primary" type="button" disabled={!canSearch || starting} onClick={() => void search()}>{starting ? 'Starting…' : (jobId && searchedProfileId === profileId ? 'Return to results' : 'Search releases')}</Button></> : <><Button variant="secondary" type="button" onClick={() => { setStep('setup'); setError('') }}>Back</Button><Button variant="primary" type="button" disabled={!selectedResult || !selectedResult.qualityAllowed || starting} onClick={() => void startDownload()}>{starting ? 'Starting download…' : 'Start Download'}</Button></>}</div>
      </footer>
    </div>
  </div>
}
