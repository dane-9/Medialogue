import { useEffect, useRef } from 'react'
import type { CSSProperties } from 'react'
import { badgeColor } from '../lib/badgeColor'
import { useNavigate } from 'react-router-dom'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { Dropdown } from '../components/Dropdown'
import { PageTopbar } from '../components/Shell'
import { Badge } from '../components/ui'
import type { MediaTorrentStatus, MovieEditionUpdate, QualityProfile, TMDBShowLookup, TMDBMovieLookup } from '../types'
import { parseJobStatusLiveEvent } from '../validation/liveEvents'
import { openLiveEvents } from '../lib/liveEvents'

// TMDB serves posters at fixed widths. A library card or table thumbnail asks
// for one near its own size: the full w500 image is several times the bytes
// and decode work, which a page of cards pays on every scroll.
export type TmdbPosterSize = 'w92' | 'w154' | 'w342' | 'w500'

export function tmdbPosterUrl(reference?: string, size: TmdbPosterSize = 'w500') {
  if (!reference) return undefined
  if (/^https?:\/\//i.test(reference)) return reference
  return `https://image.tmdb.org/t/p/${size}${reference.startsWith('/') ? reference : `/${reference}`}`
}

export function PosterImage({ reference, title, size }: { reference?: string; title: string; size?: TmdbPosterSize }) {
  const src = tmdbPosterUrl(reference, size)
  if (!src) return null
  return <img className="poster-image" src={src} alt={`${title} poster`} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.style.display = 'none' }} />
}

export const libraryInvalidationEvents = [
  'scan.progress', 'scan.completed', 'scan.failed',
  'media.present', 'media.missing', 'media.reappeared',
  'plex.matched', 'plex.verified', 'plex.not_found', 'plex.pending', 'plex.conflict', 'plex.multiple_versions', 'plex.unavailable', 'plex.sync_completed',
  'problem.created', 'problem.updated', 'problem.resolved', 'problem.deleted',
  'show.metadata_refreshed', 'movie.monitoring_updated', 'movie.tags_updated',
  'download.progress', 'download.completed', 'torrent.detected', 'torrent.removed',
  'torrent.archived', 'torrent.archive_failed', 'torrent.tracker_unregistered', 'torrent.tracker_recovered',
  'upgrade.execution',
] as const

export function useLiveLibraryRefresh(refresh: () => void | Promise<void>, pollIntervalMs = 15000, watchScanJobs = false) {
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh

  useEffect(() => {
    let refreshTimer: number | undefined
    let stopped = false
    const run = () => {
      if (!stopped && document.visibilityState !== 'hidden') void refreshRef.current()
    }
    // Throttle high-frequency scan progress to at most a couple of REST reads
    // per second while still making newly discovered rows appear during a scan.
    const invalidate = () => {
      if (refreshTimer !== undefined) return
      refreshTimer = window.setTimeout(() => {
        refreshTimer = undefined
        run()
      }, 500)
    }

    const stream = openLiveEvents()
    libraryInvalidationEvents.forEach((name) => stream.addEventListener(name, invalidate))
    const scanJobChanged = (event: MessageEvent<string>) => {
      if (!watchScanJobs) return
      const payload = parseJobStatusLiveEvent(event.data)
      if (payload && (payload.data.job_type === 'storage_root_scan' || payload.data.job_type === 'reconciliation')) invalidate()
    }
    if (watchScanJobs) stream.addEventListener('job.status', scanJobChanged)
    const poll = window.setInterval(run, pollIntervalMs)
    const onVisibility = () => { if (document.visibilityState === 'visible') invalidate() }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      stopped = true
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
      window.clearInterval(poll)
      document.removeEventListener('visibilitychange', onVisibility)
      if (watchScanJobs) stream.removeEventListener('job.status', scanJobChanged)
      stream.close()
    }
  }, [pollIntervalMs, watchScanJobs])
}

/** Library filter as a single select. */
export function LibraryFilter({ value, options, onChange, ariaLabel }: { value: string; options: string[]; onChange: (value: string) => void; ariaLabel: string }) {
  // Without a width class the trigger is width: 100%, which fills the toolbar
  // and pushes everything after it onto its own row.
  return <Dropdown
    className="select library-control"
    ariaLabel={ariaLabel}
    selectedPrefix="FILTER"
    value={value}
    options={options.map((option) => ({ value: option, label: option }))}
    onChange={onChange}
  />
}

export function MonitorMark({ monitored, title, busy, onToggle }: { monitored: boolean; title?: string; busy?: boolean; onToggle?: (monitored: boolean) => void }) {
  // Short enough to sit on one line: a tooltip box is as wide as its max-width
  // the moment its text wraps, so a long string leaves the corner empty.
  const help = monitored
    ? (onToggle ? 'Monitored. Click to stop.' : 'Monitored')
    : (onToggle ? 'Not monitored. Click to monitor.' : 'Not monitored')
  const className = `monitor-mark ${monitored ? 'on' : 'off'}`
  const glyph = <Icon name={monitored ? 'monitored' : 'monitored-off'} size={13} />
  return <Tooltip asChild content={help}>
    {onToggle
      ? <button
          type="button"
          className={className}
          aria-pressed={monitored}
          aria-label={`${monitored ? 'Stop monitoring' : 'Monitor'}${title ? ` ${title}` : ''}`}
          disabled={busy}
          onClick={(event) => { event.preventDefault(); event.stopPropagation(); onToggle(!monitored) }}
        >{glyph}</button>
      : <span className={className} aria-label={monitored ? 'Monitored' : 'Not monitored'}>{glyph}</span>}
  </Tooltip>
}

export function QbitStatusBadge({ status, compact = false, showState = false }: { status: MediaTorrentStatus; compact?: boolean; showState?: boolean }) {
  const labels: Record<MediaTorrentStatus['state'], string> = {
    untracked: 'Not tracked',
    tracked: 'Tracked, not backed up',
    backed_up: 'Tracked + backed up',
    archived: 'Backed up, not loaded',
    partial: 'Partially protected',
    attention: 'Needs attention',
  }
  const tones: Record<MediaTorrentStatus['state'], 'neutral' | 'green' | 'amber' | 'red' | 'blue'> = {
    untracked: 'neutral',
    tracked: 'blue',
    backed_up: 'green',
    archived: 'neutral',
    partial: 'amber',
    attention: 'red',
  }
  const trackerNote = status.trackerState === 'unregistered'
    ? ' All usable trackers report this torrent is no longer registered.'
    : status.trackerState === 'degraded'
      ? ' At least one tracker is working and at least one is failing.'
      : status.trackerState === 'unavailable'
        ? ' No tracker is currently responding.'
        : ''
  const detail = status.releaseCount
    ? `${status.trackedReleaseCount} of ${status.releaseCount} release${status.releaseCount === 1 ? '' : 's'} tracked in qBittorrent; ${status.backedUpReleaseCount} fully backed up. ${status.presentTorrentCount} of ${status.torrentCount} associated torrent${status.torrentCount === 1 ? '' : 's'} currently loaded.${trackerNote}`
    : 'No registered media release is available to associate with a torrent.'
  return <Tooltip content={detail}><Badge tone={tones[status.state]} className="media-card-secondary-badge">{showState ? labels[status.state] : compact ? 'qBit' : `qBittorrent ${labels[status.state]}`}</Badge></Tooltip>
}

export function formatEvidenceDate(value?: string) {
  if (!value) return 'Time not recorded'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

// Every title resolves to a profile: the one it was given, or the default it
// inherits. "No profile" is no longer reachable, so the empty option names what
// actually happens instead of implying nothing applies.
/** Trigger-only value: the included folders do not agree on one profile. */
export const MIXED_PROFILE = '__mixed__'

export function defaultProfileLabel(profiles: QualityProfile[]): string {
  const fallback = profiles.find((profile) => profile.isDefault)
  return fallback ? `Use default (${fallback.name})` : 'Use default'
}

export function EventSortHeader({ label, field, sort, onSort }: { label: string; field: string; sort: string; onSort: (sort: string) => void }) {
  const active = sort === field || sort === `-${field}`
  const descending = sort === `-${field}`
  return <button
    type="button"
    className={`event-sort ${active ? 'active' : ''}`}
    aria-sort={active ? (descending ? 'descending' : 'ascending') : 'none'}
    onClick={() => onSort(active && descending ? field : `-${field}`)}
  >{label}<Icon name="caret" size={12} className={descending ? '' : 'flipped'} /></button>
}

export type TMDBCandidate = TMDBMovieLookup | TMDBShowLookup

export const CUSTOM_EDITION = '__custom__'

export const THEATRICAL_EDITION = 'Theatrical Cut'

export type EditionDraft = { selection: string; custom: string }

export function editionChoices(options: string[]): string[] {
  return [THEATRICAL_EDITION, ...options.filter((edition) => edition !== THEATRICAL_EDITION)]
}

export function editionUpdate(releaseId: string, draft: EditionDraft): MovieEditionUpdate {
  return { releaseId, mode: 'named', edition: draft.selection === CUSTOM_EDITION ? draft.custom.trim() : draft.selection }
}

export function indexerBadgePreview(color: string): CSSProperties | undefined {
  const badge = badgeColor(color)
  if (!badge) return undefined
  return { color: badge.text, borderColor: badge.border, background: badge.background }
}

// Back returns to wherever you actually came from, a Problem, a search result,
// a download row, and only falls back to the hardcoded list route when this
// page was opened cold (deep link, refresh, new tab) and there is no history to
// return to. The label follows suit so it never promises the wrong destination.
export function Page({ title, subtitle, action, back, backTo = '/movies', children }: { title: string; subtitle: string; action?: React.ReactNode; back?: string; backTo?: string; children: React.ReactNode }) {
  const navigate = useNavigate()
  const cameFromApp = typeof window !== 'undefined' && window.history.state?.idx > 0
  const goBack = () => { if (cameFromApp) navigate(-1); else navigate(backTo) }
  return <div className="page"><PageTopbar title={title} subtitle={subtitle} action={action} back={back ? (cameFromApp ? 'Back' : back) : undefined} onBack={goBack} />{children}</div> }
