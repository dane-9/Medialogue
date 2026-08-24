import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type {
  DownloadClient,
  DownloadClientCategory,
  InteractiveSearchJob,
  InteractiveSearchResult,
  QualityProfile,
  ShowDownloadPreview,
  ShowDownloadSeasonPreview,
} from '../types'
import { Icon } from './Icon'
import { Tooltip } from './Tooltip'
import { ReleaseResults, formatBytes } from './ReleaseResults'
import { DownloadClientField } from './DownloadClientField'
import { useHorizontalWheelScroll } from '../lib/useHorizontalWheelScroll'
import { Badge, Button, Input, Select } from './ui'
import imdbLogo from '../assets/imdb.svg'

type ShowChoice = {
  tmdbId: number
  title: string
  year?: number
  poster?: string
  overview?: string
  imdbId?: string
  imdbRating?: number
  imdbVotes?: number
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



function seasonLabel(season: ShowDownloadSeasonPreview) {
  return season.seasonNumber === 0 ? 'Specials' : `Season ${season.seasonNumber}`
}

function sanitizeShowFolderName(value: string) {
  return value
    .trim()
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, ' - ')
    .replace(/\s+/g, ' ')
    .replace(/(?:\s+-\s+){2,}/g, ' - ')
    .replace(/[ .]+$/g, '')
    .slice(0, 240)
}

function showFolderName(base: string, year: number | undefined, includeYear: boolean) {
  const safeBase = sanitizeShowFolderName(base)
  if (!safeBase) return ''
  if (!includeYear) return safeBase
  if (!year) return ''
  const suffix = ` ${year}`
  if (safeBase.toLocaleLowerCase().endsWith(suffix.toLocaleLowerCase())) return safeBase
  return `${safeBase.slice(0, Math.max(1, 240 - suffix.length)).replace(/[ .]+$/g, '')}${suffix}`
}

function joinQbitPath(root: string | undefined, folder: string) {
  if (!root || !folder) return ''
  const separator = root.includes('\\') && !root.includes('/') ? '\\' : '/'
  return `${root.replace(/[\\/]+$/g, '')}${separator}${folder}`
}

export function ShowDownloadWizard({ show, onBack, onClose, onComplete }: {
  show: ShowChoice
  onBack: () => void
  onClose: () => void
  onComplete: (showId: string) => void
}) {
  const [preview, setPreview] = useState<ShowDownloadPreview | null>(null)
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [profileId, setProfileId] = useState('')
  const [clientId, setClientId] = useState('')
  const [categories, setCategories] = useState<DownloadClientCategory[]>([])
  const [category, setCategory] = useState('')
  const [includeSpecials, setIncludeSpecials] = useState(false)
  const [showFolderBaseName, setShowFolderBaseName] = useState(show.title)
  const [includeYearInFolder, setIncludeYearInFolder] = useState(false)
  const [setupLoading, setSetupLoading] = useState(true)
  const [categoryLoading, setCategoryLoading] = useState(false)
  // Bumped by a client health recheck, so categories are fetched again after a
  // client that was down comes back. The client id does not change, so nothing
  // else would retry the load that failed while it was unreachable.
  const [clientReload, setClientReload] = useState(0)
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
    Promise.all([api.showDownloadPreview(show.tmdbId), api.qualityProfiles(), api.downloadClients(), api.showDownloadPreferences()])
      .then(([showPreview, profileRows, clientRows, downloadPreferences]) => {
        if (!alive) return
        const showClients = clientRows.filter((client) => client.enabled && client.scope === 'shows')
        setPreview(showPreview)
        setShowFolderBaseName(showPreview.title || show.title)
        setIncludeYearInFolder(Boolean(downloadPreferences.include_year_in_folder_name) && Boolean(showPreview.year || show.year))
        setProfiles(profileRows)
        setClients(showClients)
        setProfileId((current) => current || profileRows[0]?.id || '')
        setClientId((current) => current || showClients[0]?.id || '')
        setError(profileRows.length ? (showClients.length ? '' : 'Configure an enabled Shows qBittorrent client before downloading a show.') : 'Create a Quality Profile before downloading a show.')
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not load download settings.'))
      .finally(() => { if (alive) setSetupLoading(false) })
    return () => { alive = false }
  }, [show.tmdbId])

  const selectedClient = clients.find((client) => client.id === clientId)
  const selectedCategory = categories.find((item) => item.name === category)
  const finalShowFolderName = showFolderName(showFolderBaseName, preview?.year || show.year, includeYearInFolder)
  const finalDestination = joinQbitPath(selectedCategory?.resolvedSavePath, finalShowFolderName)

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

  // Every season's results were ranked against the old profile, so they are
  // discarded and the season you are looking at is searched again.
  useEffect(() => {
    if (step !== 'search' || !profileId || searchProfileId === profileId) return
    setSearchProfileId(profileId)
    setSeasonStates({})
    if (activeSeason) void startSeasonSearch(activeSeason, true)
  }, [profileId, step, searchProfileId])

  const startSeasonSearch = async (season: ShowDownloadSeasonPreview, force = false) => {
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
  const canContinue = Boolean(preview && profileId && clientId && category && finalShowFolderName && finalDestination && visibleSeasons.length && !setupLoading && !categoryLoading)

  const changeIncludeYear = (checked: boolean) => {
    setIncludeYearInFolder(checked)
    setError('')
    void api.saveShowDownloadPreferences(checked).catch((reason) => {
      setError(reason instanceof Error ? reason.message : 'Could not save the folder-year preference.')
    })
  }

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
          [season.seasonNumber]: { ...(states[season.seasonNumber] ?? {}), submissionError: 'This release is no longer available, or this profile no longer allows it.' },
        }))
        continue
      }
      try {
        const response = await api.downloadSearchResult(result.id, {
          download_client_id: clientId,
          category,
          show_folder_name: showFolderBaseName,
          include_year_in_show_folder: includeYearInFolder,
        })
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

  // A show with many seasons overflows the tab strip, so the wheel scrolls it.
  const seasonTabsRef = useHorizontalWheelScroll<HTMLDivElement>()

  return <div className="modal-backdrop" onClick={onClose}>
    <div className="wizard-modal show-wizard-modal" role="dialog" aria-modal="true" aria-label={`Acquire ${show.title}`} onClick={(event) => event.stopPropagation()}>
      <header className="wizard-head">
        <div className="wizard-title-block">
          <h2>{preview?.title || show.title}{(preview?.year || show.year) ? <span> ({preview?.year || show.year})</span> : null}</h2>
        </div>
        {step === 'search' && <label className="wizard-profile">
          <span>Quality Profile</span>
          <Select
            value={profileId}
            aria-label="Quality Profile"
            // Seasons already downloading were ranked and grabbed under the
            // profile in force at the time; changing it now would misrepresent them.
            disabled={Boolean(committedShowId)}
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
            disabled={!canContinue}
            onClick={continueToSeasons}
          ><i>2</i>Select seasons</button>
        </nav>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close"><Icon name="close" size={18} /></button>
      </header>

      {step === 'setup' ? <div className="wizard-setup">
        <aside className="wizard-movie-card">
          <div className="wizard-poster">{posterUrl(preview?.posterRef || show.poster) ? <img src={posterUrl(preview?.posterRef || show.poster)} alt="" /> : <Icon name="tv" size={28} />}</div>
          <div><strong className="wizard-title-line">{preview?.title || show.title}{show.imdbRating != null && <span className="wizard-imdb-rating"><img src={imdbLogo} alt="IMDb" /> {show.imdbRating.toFixed(1)}{show.imdbVotes != null ? ` · ${show.imdbVotes.toLocaleString()} Ratings` : ''}</span>}</strong><span>{preview?.year || show.year || 'Year unknown'} · TMDB {show.tmdbId}</span>{(preview?.overview || show.overview) && <p>{preview?.overview || show.overview}</p>}</div>
        </aside>
        <section className="wizard-form">
          <div className="wizard-box"><label><span className="field-label">Quality Profile</span><Select value={profileId} onChange={(event) => setProfileId(event.target.value)} disabled={setupLoading || Boolean(committedShowId)}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</Select></label></div>
          <div className="wizard-destination"><DownloadClientField
            clients={clients}
            clientId={clientId}
            scope="shows"
            disabled={setupLoading || Boolean(committedShowId)}
            onChange={setClientId}
            onRefreshed={(rows) => { setClients(rows); setClientReload((value) => value + 1) }}
          /><label><span className="field-label">Category</span><Select value={category} onChange={(event) => setCategory(event.target.value)} disabled={!clientId || categoryLoading || Boolean(committedShowId)}>{categoryLoading && <option value="">Loading categories…</option>}{!categoryLoading && categories.map((item) => <option key={item.name} value={item.name}>{item.name}{item.isDefault ? ' · default' : ''}</option>)}</Select></label><div className="wizard-destination-path"><span>Category root</span><strong>{selectedCategory?.resolvedSavePath || (categoryLoading ? 'Loading…' : 'Unavailable')}</strong>{selectedCategory && !selectedCategory.savePath && <small>Uses the qBittorrent default download directory because this category has no explicit path.</small>}</div></div>
          <div className="wizard-box"><label><span className="field-label">Folder name</span><Input value={showFolderBaseName} maxLength={240} onChange={(event) => setShowFolderBaseName(event.target.value)} disabled={Boolean(committedShowId)} placeholder={preview?.title || show.title} /></label></div>
          <label className="wizard-specials-toggle"><span><strong>Include year in folder name</strong></span><input type="checkbox" checked={includeYearInFolder} disabled={Boolean(committedShowId) || !(preview?.year || show.year)} onChange={(event) => changeIncludeYear(event.target.checked)} /></label>
          <Tooltip
            asChild
            delay={600}
            content={<>
              <p>Off by default. When enabled, Specials (season 0) appears as its own season-pack search tab, and is searched and downloaded like any other season.</p>
              <p>This applies to this download only. Whether Specials count toward episode totals is a separate, library-wide setting.</p>
            </>}
          >
            <label className="wizard-specials-toggle"><span><strong>Include Specials for this Show</strong></span><input type="checkbox" checked={includeSpecials} disabled={Boolean(seasonStates[0]?.submitted)} onChange={(event) => { setIncludeSpecials(event.target.checked); if (!event.target.checked && activeSeasonNumber === 0) setActiveSeasonNumber(null) }} /></label>
          </Tooltip>
          <Tooltip
            asChild
            delay={600}
            content={<>
              <p>Every season you download goes into this folder, with a subfolder per season.</p>
            </>}
          >
            <div className="wizard-destination wizard-summary"><span>Final destination</span><strong>{finalDestination || 'Unavailable'}</strong></div>
          </Tooltip>
          {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
        </section>
      </div> : <div className="wizard-search show-wizard-search">
        <div className="show-season-tabs" ref={seasonTabsRef} role="tablist" aria-label="Show seasons">
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
              <small>{state?.submitted ? 'Started' : state?.starting ? 'Searching…' : `${season.episodeCount} eps`}</small>
            </button>
          })}
        </div>
        {error && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{error}</span></div>}
        {activeSeason ? <>
          {activeState?.submissionError && <div className="settings-note error-note"><Icon name="alert" size={15} /><span>{activeState.submissionError}</span></div>}
          <ReleaseResults
            results={activeState?.job?.results ?? []}
            selectedId={activeState?.selectedResultId ?? ''}
            onSelect={(id) => selectResult(activeSeason.seasonNumber, id)}
            disabled={Boolean(activeState?.submitted)}
            label={`${seasonLabel(activeSeason)} season-pack results`}
            tag="Season pack"
            onRefresh={activeState?.submitted ? undefined : () => void startSeasonSearch(activeSeason, true)}
            refreshing={Boolean(activeState?.starting)}
            empty={activeState?.starting || (activeState?.job && !['completed', 'failed'].includes(activeState.job.status))
              ? `Searching for ${seasonLabel(activeSeason)} season packs…`
              : activeState?.job
                ? `No ${seasonLabel(activeSeason)} season packs were returned.`
                : 'Search starts when you open this season tab.'}
          />
        </> : <div className="show-season-tab-empty"><Icon name="search" size={26} /><strong>Choose a season</strong><p>Each season is searched lazily. Opening a season tab starts that season's indexer search; unopened seasons do not generate searches.</p></div>}
      </div>}

      <footer className="wizard-foot">
        {step === 'search' && activeState?.job
          ? <span className="wizard-foot-count">{activeState.job.resultTotal} Torrent{activeState.job.resultTotal === 1 ? '' : 's'} found</span>
          : null}
        <span>{step === 'setup' ? 'Nothing is added to your library until qBittorrent accepts a season pack.' : committedShowId ? 'This show is in your library, a season download has started.' : null}</span>
        <div>{step === 'setup' ? <><Button variant="secondary" type="button" onClick={onBack}>Back</Button><Button variant="primary" type="button" disabled={!canContinue} onClick={continueToSeasons}>Continue</Button></> : <><Button variant="secondary" type="button" onClick={() => { setStep('setup'); setError('') }}>Back</Button>{committedShowId && pendingSeasons.length === 0 ? <Button variant="primary" type="button" onClick={() => onComplete(committedShowId)}>Open Show</Button> : <Button variant="primary" type="button" disabled={!pendingSeasons.length || submitting} onClick={() => void startDownloads()}>{submitting ? 'Starting downloads…' : `${committedShowId ? 'Retry' : 'Start'} Downloads (${pendingSeasons.length})`}</Button>}</>}</div>
      </footer>
    </div>
  </div>
}
