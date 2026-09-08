import { useEffect, useState } from 'react'
import { integrationsApi } from '../api/modules/integrations'
import { moviesApi } from '../api/modules/movies'
import { searchApi } from '../api/modules/search'
import type { DownloadClient, DownloadClientCategory, InteractiveSearchJob } from '../types'
import { Icon } from './Icon'
import { Modal } from './Modal'
import { Button, Select } from './ui'
import { DownloadClientField } from './DownloadClientField'
import { ReleaseResults, formatBytes } from './ReleaseResults'

const FINISHED = ['completed', 'failed', 'cancelled', 'interrupted']

/** Search indexers for a movie already in the library and grab one result.
 *
 * The download wizard covers the same ground for a title being added, but it
 * searches by TMDB id for a movie that does not exist yet. This one searches an
 * existing movie, so the grab attaches to it rather than creating anything.
 */
export function MovieInteractiveSearch({ movieId, title, onClose, onStarted }: {
  movieId: string
  title: string
  onClose: () => void
  onStarted: (message: string) => void
}) {
  const [job, setJob] = useState<InteractiveSearchJob | null>(null)
  const [jobId, setJobId] = useState('')
  const [selectedResultId, setSelectedResultId] = useState('')
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [clientId, setClientId] = useState('')
  const [clientReload, setClientReload] = useState(0)
  const [categories, setCategories] = useState<DownloadClientCategory[]>([])
  const [category, setCategory] = useState('')
  const [categoryLoading, setCategoryLoading] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')

  // The search starts as soon as the dialog opens: there is nothing to choose
  // beforehand, since the movie already carries its own profile.
  useEffect(() => {
    let alive = true
    moviesApi.startMovieSearch(movieId)
      .then((accepted) => { if (alive) setJobId(String(accepted.job_id)) })
      .catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not start the search.') })
    return () => { alive = false }
  }, [movieId])

  useEffect(() => {
    let alive = true
    integrationsApi.downloadClients()
      .then((rows) => {
        if (!alive) return
        const usable = rows.filter((client) => client.enabled && client.scope === 'movies')
        setClients(usable)
        setClientId((current) => current || usable[0]?.id || '')
        if (!usable.length) setError('Configure an enabled Movies qBittorrent client before grabbing a release.')
      })
      .catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load download clients.') })
    return () => { alive = false }
  }, [])

  const selectedClient = clients.find((client) => client.id === clientId)

  useEffect(() => {
    if (!clientId) { setCategories([]); setCategory(''); return }
    let alive = true
    setCategoryLoading(true)
    integrationsApi.downloadClientCategories(clientId)
      .then((rows) => {
        if (!alive) return
        setCategories(rows)
        const preferred = selectedClient?.category
        setCategory((preferred && rows.some((item) => item.name === preferred) ? preferred : rows.find((item) => item.isDefault)?.name) || rows[0]?.name || '')
        if (!rows.length) setError('This qBittorrent instance has no categories. Create one in qBittorrent before continuing.')
      })
      .catch((reason) => {
        if (!alive) return
        setCategories([]); setCategory('')
        setError(reason instanceof Error ? reason.message : 'Could not load qBittorrent categories.')
      })
      .finally(() => { if (alive) setCategoryLoading(false) })
    return () => { alive = false }
  }, [clientId, selectedClient?.category, clientReload])

  // Results stream in as each indexer answers, so the job is polled until it
  // reaches a terminal state rather than awaited once.
  useEffect(() => {
    if (!jobId) return
    let alive = true
    let timer = 0
    const refresh = async () => {
      try {
        const value = await searchApi.searchJob(jobId)
        if (!alive) return
        setJob(value)
        if (value.status === 'failed') setError(String(value.error?.message ?? 'Interactive search failed.'))
        if (!FINISHED.includes(value.status)) timer = window.setTimeout(() => void refresh(), 700)
      } catch (reason) {
        if (alive) setError(reason instanceof Error ? reason.message : 'Could not refresh search results.')
      }
    }
    void refresh()
    return () => { alive = false; if (timer) window.clearTimeout(timer) }
  }, [jobId])

  const results = job?.results ?? []
  const selectedResult = results.find((result) => result.id === selectedResultId)
  const selectedCategory = categories.find((item) => item.name === category)

  const grab = async () => {
    if (!selectedResult || !clientId || !category) return
    setStarting(true); setError('')
    try {
      await searchApi.downloadSearchResult(selectedResult.id, { download_client_id: clientId, category })
      onStarted(`Sent ${selectedResult.title} to ${selectedClient?.name ?? 'qBittorrent'}.`)
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not start this download.')
    } finally { setStarting(false) }
  }

  return <Modal
    wide
    className="movie-search-modal"
    title={title}
    eyebrow="INTERACTIVE SEARCH"
    onClose={onClose}
    headerAction={<Button variant="secondary" icon="refresh" disabled={!jobId || starting} onClick={() => { setJob(null); setSelectedResultId(''); setError(''); setJobId(''); void moviesApi.startMovieSearch(movieId).then((accepted) => setJobId(String(accepted.job_id))).catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not restart the search.')) }}>Search again</Button>}
    footer={<>
      <span className="wizard-foot-count">{job ? `${results.length} Torrent${results.length === 1 ? '' : 's'} found` : 'Searching…'}</span>
      <span className="modal-foot-spacer" />
      {selectedResult && <span className="muted">{formatBytes(selectedResult.size)} → {selectedClient?.name} / {category}</span>}
      <Button variant="ghost" onClick={onClose} disabled={starting}>Cancel</Button>
      <Button variant="primary" icon="download" disabled={!selectedResult || !clientId || !category || starting} onClick={() => void grab()}>{starting ? 'Sending…' : 'Grab release'}</Button>
    </>}
  >
    <div className="movie-search-body">
      <div className="movie-search-destination">
        <DownloadClientField
          clients={clients}
          clientId={clientId}
          scope="movies"
          disabled={starting}
          onChange={setClientId}
          onRefreshed={(rows) => { setClients(rows); setClientReload((value) => value + 1) }}
        />
        <label><span className="field-label">Category</span><Select value={category} disabled={!clientId || categoryLoading} onChange={(event) => setCategory(event.target.value)}>{categoryLoading && <option value="">Loading categories…</option>}{!categoryLoading && categories.map((item) => <option key={item.name} value={item.name}>{item.name}{item.isDefault ? ' · default' : ''}</option>)}</Select></label>
        <div className="wizard-destination-path"><span>Save path</span><strong>{selectedCategory?.resolvedSavePath || (categoryLoading ? 'Loading…' : 'Select a category')}</strong><small>Medialogue does not set a save path or move the downloaded media.</small></div>
      </div>
      {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
      <ReleaseResults
        results={results}
        selectedId={selectedResultId}
        onSelect={setSelectedResultId}
        disabled={starting}
        label={`Search results for ${title}`}
        empty={job && FINISHED.includes(job.status) ? 'No releases were returned by your indexers.' : 'Searching configured indexers…'}
      />
    </div>
  </Modal>
}
