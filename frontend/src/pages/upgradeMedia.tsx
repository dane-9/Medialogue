import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { cachedList, rememberList } from '../lib/listCache'
import { usePersistentSet } from '../lib/persistentSet'
import type { CSSProperties, ReactNode } from 'react'
import { badgeColor } from '../lib/badgeColor'
import { formatAge } from '../lib/age'
import { diffReleaseTokens } from '../lib/releaseTokens'
import { candidateFormatReasons, shareCombinedDynamicRangeReasons, upgradeTokenReason, UPGRADE_REASON_SCORE } from '../lib/upgradeTokenReason'
import { formatBytes, ReleaseResults } from '../components/ReleaseResults'
import { ReleaseSourcePill } from '../components/ReleaseSourcePill'
import { UpgradePill } from '../components/UpgradePill'
import { MediaHistoryModal } from '../components/MediaHistoryModal'
import { formatPeers, swarmHealth } from '../lib/swarm'
import { qualityLabel } from '../lib/qualityLabel'
import { splitQuality } from '../lib/quality'
import { pathParts } from '../lib/pathParts'
import { Field, SettingsGroup } from '../components/settings'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { Dropdown } from '../components/Dropdown'
import { Modal } from '../components/Modal'
import { Button, EmptyState, Select } from '../components/ui'
import { formatEta } from '../api/mappers/downloads'
import { upgradeResultsToReleases } from '../lib/upgradeResults'
import { integrationsApi } from '../api/modules/integrations'
import { settingsApi } from '../api/modules/settings'
import { upgradesApi } from '../api/modules/upgrades'
import type { DownloadClient, InteractiveSearchResult, QualityProfile, UpgradeCandidate, UpgradeCleanupPreview, UpgradeMatchedFormat, UpgradeSearchResults } from '../types'
import { MIXED_PROFILE, Page, defaultProfileLabel, libraryInvalidationEvents, tmdbPosterUrl } from './shared'
import { useLiveEvent, useLiveRefresh } from '../lib/liveRefresh'

/** A score change, signed: +5, −10, ±0. */
function formatScoreDelta(value: number) {
  return value > 0 ? `+${value}` : value < 0 ? `−${Math.abs(value)}` : '±0'
}

/** The two scores' Custom Formats side by side, on hover. Replace Media
 *  shows it for its replacements too. */
type UpgradeCompareScores = Pick<UpgradeCandidate, 'currentMatchedFormats' | 'candidateMatchedFormats' | 'currentCustomFormatScore' | 'customFormatScore' | 'scoreDelta'>

export function UpgradeCompare({ item, profile, children }: { item: UpgradeCompareScores; profile: string; children: ReactNode }) {
  // Formats of one group (Audio, Dynamic Range, ...) fill the same part of a
  // release, so they share a row: DTS-HD MA on one side, TrueHD Atmos on the
  // other. A format with no group is a row of its own.
  const rows = useMemo(() => {
    type Side = { name: string; contribution: number }[]
    const merged = new Map<string, { label: string; grouped: boolean; current: Side; candidate: Side }>()
    const add = (format: UpgradeMatchedFormat, side: 'current' | 'candidate') => {
      const key = format.group ? `group:${format.group}` : `format:${format.id}`
      const row = merged.get(key) ?? { label: format.group ?? format.name, grouped: Boolean(format.group), current: [], candidate: [] }
      row[side].push({ name: format.name, contribution: format.contribution })
      merged.set(key, row)
    }
    item.currentMatchedFormats.forEach((format) => add(format, 'current'))
    item.candidateMatchedFormats.forEach((format) => add(format, 'candidate'))
    const total = (side: Side) => side.reduce((sum, format) => sum + format.contribution, 0)
    // Biggest movers first, so what changed is read before what did not.
    return [...merged.entries()]
      .map(([key, row]) => ({ key, ...row, delta: total(row.candidate) - total(row.current) }))
      .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta) || a.label.localeCompare(b.label))
  }, [item.currentMatchedFormats, item.candidateMatchedFormats])

  const score = (value: number) => `${value > 0 ? '+' : ''}${value}`
  // Each side is two columns, names then scores, so every name starts on one
  // line and every score ends on another.
  // A format the current release already has, at the same score, is not
  // part of the change, so it stays grey even in a group that improved.
  const side = (formats: { name: string; contribution: number }[], grouped: boolean, tone: string, upgrade = false, unchanged: { name: string; contribution: number }[] = []) => <>
    <span className={`cf-compare-names ${upgrade ? 'cf-compare-upgrade' : ''}`}>{grouped && formats.map((format, index) => <span key={index}>{format.name}</span>)}</span>
    <span className={`cf-compare-scores ${upgrade ? '' : 'cf-compare-current'}`}>{formats.length
      ? formats.map((format, index) => <b key={index} className={unchanged.some((other) => other.name === format.name && other.contribution === format.contribution) ? 'cf-zero' : tone}>{score(format.contribution)}</b>)
      : <b className="cf-zero">-</b>}</span>
  </>

  return <Tooltip asChild placement="below" delay={250} content={<span className="cf-breakdown">
    <span className="cf-breakdown-head"><strong>Custom Formats</strong><small>{profile}</small></span>
    {rows.length
      ? <span className="cf-compare">
          <span className="cf-compare-legend">Format</span>
          <span className="cf-compare-legend cf-compare-side cf-compare-current">Current</span>
          <span className="cf-compare-legend cf-compare-side cf-compare-upgrade">Upgrade</span>
          {rows.map((row) => <Fragment key={row.key}>
            <span className="cf-compare-label">{row.label}</span>
            {side(row.current, row.grouped, 'cf-zero')}
            {side(row.candidate, row.grouped, row.delta > 0 ? 'cf-plus' : row.delta < 0 ? 'cf-minus' : 'cf-zero', true, row.current)}
          </Fragment>)}
          <span className="cf-compare-label cf-compare-total cf-compare-foot">Total</span>
          <span className="cf-compare-scores cf-compare-side cf-compare-current cf-compare-foot"><b className="cf-compare-total">{item.currentCustomFormatScore}</b></span>
          <span className="cf-compare-scores cf-compare-side cf-compare-upgrade cf-compare-foot"><span className="cf-compare-sum">
            <b className={`cf-compare-total ${item.scoreDelta > 0 ? 'cf-plus' : item.scoreDelta < 0 ? 'cf-minus' : 'cf-zero'}`}>{item.customFormatScore}</b>
            <small className="cf-score-delta">{formatScoreDelta(item.customFormatScore - item.currentCustomFormatScore)}</small>
          </span></span>
        </span>
      : <small>No Custom Formats matched either release.</small>}
  </span>}>{children}</Tooltip>
}

type UpgradeStatus = 'ready' | 'in_progress' | 'attention'

const UPGRADE_STATUSES: { value: UpgradeStatus; label: string; icon: 'upgrade-media' | 'download' | 'alert'; tone: string; empty: string }[] = [
  { value: 'ready', label: 'Ready', icon: 'upgrade-media', tone: 'ok', empty: 'No upgrades are waiting to be started in this folder.' },
  { value: 'in_progress', label: 'In progress', icon: 'download', tone: 'blue', empty: 'No upgrades are downloading or being verified.' },
  { value: 'attention', label: 'Needs attention', icon: 'alert', tone: 'amber', empty: 'Nothing needs your attention.' },
]

/** A release name that may wrap only between its dotted words. */
function breakableRelease(name: string) {
  return name.split(/(?<=[._\-\s])/).map((part, index) => <Fragment key={index}>{index > 0 && <wbr />}{part}</Fragment>)
}

/** The current release as named on disk or by its tracker. */
function currentReleaseLabel(item: UpgradeCandidate) {
  return item.currentReleaseName || item.releaseFolder || pathParts(item.currentPaths[0])?.name || item.mediaTitle
}

/** Whether an upgrade can be started: never tried, or its last attempt failed. */
function upgradeStartable(item: UpgradeCandidate) {
  return !item.executionState || item.executionState === 'failed'
}

function upgradeStatusOf(item: UpgradeCandidate): UpgradeStatus {
  if (!item.executionState || item.executionState === 'completed' || item.executionState === 'failed') return 'ready'
  if (item.executionState === 'handoff_attention' || item.executionState === 'cleanup_required') return 'attention'
  return 'in_progress'
}

function upgradeEditionLabel(edition?: string) {
  return edition?.trim() || 'Theatrical Cut'
}

function formatUpgradeBytes(bytes?: number) {
  if (bytes === undefined || bytes === null || !Number.isFinite(bytes)) return '-'
  if (bytes === 0) return '0 B'
  // Binary units, matching the 1024 divisor and qBittorrent.
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = Math.max(0, bytes)
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`
}

export function UpgradeMediaPage() {
  // The list as last loaded in this tab: shown at once on return.
  const [items, setItems] = useState<UpgradeCandidate[]>(() => cachedList<UpgradeCandidate>('upgradeMedia') ?? [])
  const [clients, setClients] = useState<DownloadClient[]>([])
  const [profiles, setProfiles] = useState<QualityProfile[]>([])
  const [formatSections, setFormatSections] = useState<Record<string, string>>({})
  const [profileBusy, setProfileBusy] = useState(false)
  const [loading, setLoading] = useState(() => cachedList('upgradeMedia') === undefined)
  const [searching, setSearching] = useState(false)
  const [busyId, setBusyId] = useState('')
  // The lit word under the pointer: its reason, so every word sharing it
  // (DV + HDR) lights together, and the score it carries.
  // A Custom Format word carries its score; a quality word (2160p, BluRay)
  // instead lights the quality it replaces on the Current side.
  const [historyOpen, setHistoryOpen] = useState(false)
  const [hoveredReason, setHoveredReason] = useState<{ id: string; reason: string; score?: number } | null>(null)
  const [selectedRoot, setSelectedRoot] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  // Rows left out stay left out across reloads and devices.
  const [excluded, setExcluded, pruneExcluded] = usePersistentSet('upgrade-media-excluded')
  const [profilePrompt, setProfilePrompt] = useState<string | null>(null)
  const [readOnlyRoots, setReadOnlyRoots] = useState<string[]>([])
  const [confirming, setConfirming] = useState(false)
  const [clientId, setClientId] = useState('')
  const [category, setCategory] = useState('')
  const [categories, setCategories] = useState<Array<{ name: string; resolvedSavePath: string; isDefault: boolean }>>([])
  const [categoryLoading, setCategoryLoading] = useState(false)
  const [cleanupTarget, setCleanupTarget] = useState<UpgradeCandidate | null>(null)
  const [cleanup, setCleanup] = useState<UpgradeCleanupPreview | null>(null)
  const [cleanupLoading, setCleanupLoading] = useState(false)
  const [resultsTarget, setResultsTarget] = useState<UpgradeCandidate | null>(null)
  const [searchResults, setSearchResults] = useState<UpgradeSearchResults | null>(null)
  const [resultsLoading, setResultsLoading] = useState(false)
  const [selectedResultId, setSelectedResultId] = useState('')
  const [resultSelecting, setResultSelecting] = useState(false)
  // Status first, then folder: ready candidates, ones on their way and ones
  // waiting on a decision never share one list.
  const [status, setStatus] = useState<UpgradeStatus>('ready')
  const [statusChosen, setStatusChosen] = useState(false)
  const upgradeReleaseResults = useMemo<InteractiveSearchResult[]>(() => (
    searchResults && resultsTarget ? upgradeResultsToReleases(searchResults, resultsTarget.id, resultsTarget.mediaType) : []
  ), [searchResults, resultsTarget])

  // `quiet`: no loading state (a cached list is showing, or a live refresh).
  // Live refreshes also keep errors quiet; the first load always reports them.
  const load = async (quiet = false, reportError = !quiet) => {
    if (!quiet) setLoading(true)
    try {
      const [nextItems, nextClients, nextProfiles, nextCount, nextFormats, nextLayout] = await Promise.all([upgradesApi.upgradeCandidates(), integrationsApi.downloadClients(), settingsApi.qualityProfiles(), upgradesApi.upgradeCandidateCount(), settingsApi.customFormats(), settingsApi.customFormatLayout()])
      rememberList('upgradeMedia', nextItems)
      setItems(nextItems)
      pruneExcluded(nextItems.map((item) => item.id))
      setClients(nextClients)
      setProfiles(nextProfiles)
      // Token tooltips name a format by the section it is filed under on the
      // Custom Formats page, so the layout is what turns an id into that name.
      const formatNames = new Map(nextFormats.map((format) => [format.id, format.name]))
      setFormatSections(Object.fromEntries(nextLayout.flatMap((section) => section.formatIds.flatMap((id) => {
        const name = formatNames.get(id)
        return name ? [[name.toLowerCase(), section.name] as const] : []
      }))))
      setReadOnlyRoots(nextCount.readOnlyRoots ?? [])
      setError('')
    } catch (reason) {
      if (reportError) setError(reason instanceof Error ? reason.message : 'Could not load upgrade candidates.')
    } finally {
      if (!quiet) setLoading(false)
    }
  }

  useEffect(() => { void load(cachedList('upgradeMedia') !== undefined, true) }, [])

  // Live updates. Progress events patch the downloading rows in place; only a
  // change of state (an upgrade submitted, finished or failed, a library
  // change) re-reads the list, and only the list and its count. Clients,
  // profiles and formats change through this page's own actions, which load().
  const loadLiveRef = useRef(async () => {})
  loadLiveRef.current = async () => {
    try {
      const [nextItems, nextCount] = await Promise.all([upgradesApi.upgradeCandidates(), upgradesApi.upgradeCandidateCount()])
      rememberList('upgradeMedia', nextItems)
      setItems(nextItems)
      setReadOnlyRoots(nextCount.readOnlyRoots ?? [])
    } catch {
      // A missed refresh is retried by the next event or the poll.
    }
  }
  useLiveEvent(['download.progress'], (event) => {
    let payload: { data?: Record<string, unknown> }
    try { payload = JSON.parse(event.data) } catch { return }
    const data = payload?.data
    if (!data || typeof data.torrent_id !== 'string') return
    const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : undefined
    setItems((current) => {
      let changed = false
      const next = current.map((item) => {
        if (item.torrentId !== data.torrent_id) return item
        if (typeof data.client_id === 'string' && item.selectedDownloadClientId && item.selectedDownloadClientId !== data.client_id) return item
        changed = true
        return {
          ...item,
          downloadProgress: number(data.progress) ?? item.downloadProgress,
          downloadState: typeof data.state === 'string' ? data.state : item.downloadState,
          downloadSpeed: number(data.download_speed) ?? item.downloadSpeed,
          downloadEtaSeconds: number(data.eta_seconds) ?? item.downloadEtaSeconds,
        }
      })
      return changed ? next : current
    })
  })
  // Structural changes reload; the timer is a safety net for missed events.
  useLiveRefresh(() => loadLiveRef.current(), {
    events: [...libraryInvalidationEvents.filter((name) => name !== 'download.progress'), 'results.upgrades'],
    coalesceMs: 500,
    pollMs: 30_000,
  })

  useEffect(() => {
    if (!confirming || !clientId) { setCategories([]); setCategory(''); return }
    let alive = true
    setCategoryLoading(true)
    integrationsApi.downloadClientCategories(clientId)
      .then((rows) => {
        if (!alive) return
        setCategories(rows)
        const client = clients.find((entry) => entry.id === clientId)
        const preferred = rows.find((row) => row.name === client?.category) ?? rows.find((row) => row.isDefault) ?? rows[0]
        setCategory(preferred?.name ?? '')
      })
      .catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not read qBittorrent categories.') })
      .finally(() => { if (alive) setCategoryLoading(false) })
    return () => { alive = false }
  }, [confirming, clientId, clients])

  const search = async () => {
    setSearching(true); setError(''); setMessage('')
    try {
      await upgradesApi.searchUpgrades()
      setMessage('Upgrade search queued. This page will refresh as candidates are evaluated.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not start upgrade search.') }
    finally { setSearching(false) }
  }

  // Leaving a row out is the exception, so the set holds what is excluded.
  const toggleChosen = (id: string) => setExcluded((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const applyProfileResult = (
    result: { candidateIds: string[]; qualityProfileId?: string; qualityProfileName?: string },
  ) => {
    const affected = new Set(result.candidateIds)
    setItems((current) => current.map((item) => affected.has(item.id) ? {
      ...item,
      qualityProfileId: result.qualityProfileId,
      qualityProfileName: result.qualityProfileName,
      qualityProfileInherited: !result.qualityProfileId,
      executionEligible: false,
      executionBlockReason: 'Quality Profile changed; waiting for upgrade candidates to be re-evaluated.',
    } : item))
  }

  // Submitted one at a time so a rejected candidate does not discard the rest,
  // and so a failure can name which one it was.
  const executeChosen = async () => {
    if (!clientId || !category || !picked.length) return
    setBusyId('batch'); setError(''); setMessage('')
    const failures: string[] = []
    let submitted = 0
    for (const item of picked) {
      try {
        const updated = await upgradesApi.executeUpgrade(item.id, clientId, category)
        setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))
        submitted += 1
      } catch (reason) {
        failures.push(`${item.mediaTitle}: ${reason instanceof Error ? reason.message : 'submission failed'}`)
      }
    }
    setConfirming(false)
    if (submitted) setMessage(`Started ${submitted} ${submitted === 1 ? 'upgrade' : 'upgrades'}. Old torrents and media will be removed automatically after each replacement validates.`)
    if (failures.length) setError(failures.join(' · '))
    setBusyId('')
  }

  const retryHandoff = async (item: UpgradeCandidate) => {
    setBusyId(item.id); setError(''); setMessage('')
    try {
      const updated = await upgradesApi.retryUpgradeHandoff(item.id)
      setItems((current) => current.map((entry) => entry.id === updated.id ? updated : entry))
      setMessage('Replacement handoff was checked again.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not retry the replacement handoff.') }
    finally { setBusyId('') }
  }

  const reviewCleanup = async (item: UpgradeCandidate) => {
    setCleanupTarget(item); setCleanup(null); setCleanupLoading(true); setError('')
    try { setCleanup(await upgradesApi.upgradeCleanupPreview(item.id)) }
    catch (reason) { setCleanupTarget(null); setError(reason instanceof Error ? reason.message : 'Could not build the old-media cleanup preview.') }
    finally { setCleanupLoading(false) }
  }

  const openResults = async (item: UpgradeCandidate) => {
    setResultsTarget(item); setSearchResults(null); setResultsLoading(true); setError('')
    try {
      const result = await upgradesApi.upgradeSearchResults(item.id)
      setSearchResults(result)
      setSelectedResultId(result.results.find((entry) => entry.selected)?.id ?? result.results.find((entry) => entry.recommended)?.id ?? '')
    } catch (reason) {
      setResultsTarget(null)
      setError(reason instanceof Error ? reason.message : 'Could not load cached upgrade search results.')
    } finally { setResultsLoading(false) }
  }

  const chooseSearchResult = async () => {
    if (!resultsTarget || !selectedResultId) return
    setResultSelecting(true); setError('')
    try {
      const updated = await upgradesApi.selectUpgradeSearchResult(resultsTarget.id, selectedResultId)
      setItems((current) => current.map((item) => item.id === updated.id ? updated : item))
      setResultsTarget(null); setSearchResults(null)
      setMessage(`Selected ${updated.title} as the upgrade for ${updated.mediaTitle}.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not select that upgrade result.') }
    finally { setResultSelecting(false) }
  }

  const commitCleanup = async () => {
    if (!cleanupTarget || !cleanup?.eligible) return
    setBusyId(cleanupTarget.id); setError(''); setMessage('')
    try {
      const result = await upgradesApi.cleanupUpgrade(cleanupTarget.id, cleanup.confirmationToken)
      setCleanupTarget(null); setCleanup(null)
      setMessage(`Upgrade complete. Deleted ${result.deletedPaths.length} reviewed old-media ${result.deletedPaths.length === 1 ? 'target' : 'targets'}.`)
      await load(true)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete the reviewed old media.') }
    finally { setBusyId('') }
  }

  const showResultsEdition = Boolean(
    resultsTarget?.mediaType === 'movies'
    && (resultsTarget.currentEdition || resultsTarget.movieHasMultipleEditions),
  )
  // Grouped by media folder, like New Media: a candidate is read inside the
  // folder it belongs to rather than in one flat list across every root.
  const statusCounts = useMemo(() => {
    const counts: Record<UpgradeStatus, number> = { ready: 0, in_progress: 0, attention: 0 }
    for (const item of items) if (item.executionState !== 'completed') counts[upgradeStatusOf(item)] += 1
    return counts
  }, [items])
  // Open on a status that has something, until the user picks one.
  const shownStatus: UpgradeStatus = statusChosen || statusCounts[status] ? status
    : (['ready', 'attention', 'in_progress'] as UpgradeStatus[]).find((value) => statusCounts[value]) ?? 'ready'
  const statusItems = useMemo(() => items.filter((item) => item.executionState !== 'completed' && upgradeStatusOf(item) === shownStatus), [items, shownStatus])
  const roots = useMemo(() => {
    const grouped = new Map<string, { name: string; mediaType: 'movies' | 'shows'; count: number }>()
    for (const item of statusItems) {
      const name = item.rootName ?? 'Unknown folder'
      const existing = grouped.get(name)
      if (existing) existing.count += 1
      else grouped.set(name, { name, mediaType: item.mediaType, count: 1 })
    }
    return [...grouped.values()].sort((a, b) => a.name.localeCompare(b.name))
  }, [statusItems])
  const rootName = roots.some((entry) => entry.name === selectedRoot) ? selectedRoot : roots[0]?.name ?? ''
  const rows = useMemo(() => statusItems.filter((item) => (item.rootName ?? 'Unknown folder') === rootName), [statusItems, rootName])

  // The checkboxes decide what the bar acts on, for the profile exactly as for
  // the upgrade button.
  const profileTargets = useMemo(() => rows.filter((item) => upgradeStartable(item) && !excluded.has(item.id)), [rows, excluded])
  const sharedProfile = useMemo(() => {
    if (!profileTargets.length) return undefined
    const first = profileTargets[0].qualityProfileId ?? ''
    return profileTargets.every((item) => (item.qualityProfileId ?? '') === first) ? first : undefined
  }, [profileTargets])

  // Writes the profile on every title in the folder, which then re-scores their
  // candidates, so the change is confirmed before it is made.
  const setAllProfiles = async (profileId: string | null) => {
    setProfilePrompt(null)
    if (!profileTargets.length) return
    setProfileBusy(true); setError(''); setMessage('')
    try {
      const result = await upgradesApi.setUpgradeQualityProfiles(profileTargets.map((item) => item.id), profileId)
      applyProfileResult(result)
      const name = profiles.find((profile) => profile.id === profileId)?.name ?? defaultProfileLabel(profiles)
      setMessage(`${result.updatedEntities} ${result.updatedEntities === 1 ? 'title' : 'titles'} set to ${name}. Upgrade candidates are being re-evaluated.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not set the Quality Profile.') }
    finally { setProfileBusy(false) }
  }
  // A candidate already in flight keeps its own per-row actions, and a blocked
  // one cannot be submitted at all, so neither can join the batch.
  const picked = useMemo(() => rows.filter((item) => upgradeStartable(item) && item.executionEligible && !excluded.has(item.id)), [rows, excluded])
  const pickedBytes = useMemo(() => picked.reduce((total, item) => total + (item.size ?? 0), 0), [picked])
  // How much the library grows (or shrinks) once these replace what is on
  // disk. Only rows whose current size is known can say.
  const pickedBytesChange = useMemo(() => picked.reduce((total, item) => (
    item.size !== undefined && item.currentSize !== undefined ? total + item.size - item.currentSize : total
  ), 0), [picked])
  const mediaType = rows[0]?.mediaType ?? 'movies'
  const noun = mediaType === 'movies' ? 'Movie' : 'Show'
  const eligibleClients = useMemo(() => clients.filter((client) => client.enabled && client.scope === mediaType), [clients, mediaType])

  // One destination for the whole selection, chosen when the dialog opens.
  const openConfirm = () => {
    const preferred = eligibleClients.find((client) => client.id === picked[0]?.selectedDownloadClientId) ?? eligibleClients[0]
    setClientId(preferred?.id ?? '')
    setCategory('')
    setCategories([])
    setConfirming(true)
  }

  return <Page
    title="Upgrade Media"
    subtitle="Review the best known replacement for each release. Starting an upgrade authorizes its validated replacement and automatic retirement of the old release."
    action={<Button variant="secondary" icon="clock" onClick={() => setHistoryOpen(true)}>History</Button>}
  >
    {historyOpen && <MediaHistoryModal load={upgradesApi.upgradeHistory} empty="No upgrades have finished yet." onClose={() => setHistoryOpen(false)} />}
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {message && !error && <div className="settings-note note-ok"><Icon name="check" size={16} /><span>{message}</span></div>}
    {/* One note for the folder setting, rather than one badge on every release
        that happens to sit inside it. */}
    {readOnlyRoots.length > 0 && <div className="settings-note"><Icon name="lock" size={16} /><span>
      {readOnlyRoots.length === 1 ? `${readOnlyRoots[0]} is read-only` : `${readOnlyRoots.join(', ')} are read-only`}, so no upgrades are searched there. Change the access mode in Settings - Media Folders.
    </span></div>}

    {loading ? <div className="upgrade-media-loading">Loading upgrade candidates…</div> : !items.length ? <EmptyState
      icon="refresh"
      title="No upgrades found"
      detail="Run an upgrade search to compare current releases with the configured indexers. Nothing is downloaded automatically."
      action={<Button variant="primary" icon="refresh" disabled={searching} onClick={() => void search()}>Search for upgrades</Button>}
    /> : <>
      <div className="upgrade-v2-filters">
        <div className="upgrade-v2-status-group">
          <div className="new-media-roots-label">Status</div>
          <div className="new-media-roots">
            {UPGRADE_STATUSES.map((entry) => <button
              type="button"
              key={entry.value}
              className={`new-media-root ${entry.value === shownStatus ? 'active' : ''}`}
              onClick={() => { setStatus(entry.value); setStatusChosen(true) }}
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
              onClick={() => { setSelectedRoot(entry.name) }}
            >
              <span className="new-media-root-name">
                <span className={`kind-box kind-${entry.mediaType === 'movies' ? 'movie' : 'show'}`} aria-label={entry.mediaType === 'movies' ? 'Movies folder' : 'Shows folder'}>
                  <Icon name={entry.mediaType === 'movies' ? 'clapper' : 'tv-screen'} size={13} />
                </span>
                <strong>{entry.name}</strong>
              </span>
              <small>{entry.count} {entry.count === 1 ? 'upgrade' : 'upgrades'}</small>
            </button>)}
          </div>
        </div>}
      </div>

      <div className="new-media-workspace upgrade-v2 upgrade-v3">
        <div className="new-media-scroll">
          <span className="new-media-col-head">Current</span>
          <span className="new-media-col-head upgrade-v2-head">
            <span>Upgrade</span>
            {shownStatus === 'ready' && <>
              <span className="upgrade-v2-seeds">Seeds</span>
              <span className="upgrade-v2-size">Size</span>
              <span className="upgrade-v2-size-change" aria-hidden="true" />
            </>}
            {shownStatus === 'in_progress' && <>
              <span className="upgrade-v2-live">Progress</span>
              <span className="upgrade-v2-live">Speed</span>
              <span className="upgrade-v2-live">ETA</span>
            </>}
            {shownStatus === 'attention' && <span className="upgrade-v2-action">Action</span>}
          </span>
          <span className="upgrade-v3-rail-head" aria-hidden="true" />
          {!rows.length && <div className="upgrade-v2-empty">{UPGRADE_STATUSES.find((entry) => entry.value === shownStatus)?.empty}</div>}
          {rows.map((item) => {
            const active = false
            const batchable = upgradeStartable(item) && item.executionEligible
            const included = batchable && !excluded.has(item.id)
            const blocked = upgradeStartable(item) && !item.executionEligible
            const delta = item.scoreDelta
            const swarm = swarmHealth(item.seeders)
            const progress = item.downloadProgress === undefined ? undefined : Math.round(Math.max(0, Math.min(1, item.downloadProgress)) * 100)
            const currentQualityText = qualityLabel(item.currentQuality) || undefined
            const qualityHovered = hoveredReason?.id === item.id && hoveredReason.reason.startsWith('Quality:')
            const detailParts: { text: string; quality?: boolean }[] = [
              item.mediaYear ? { text: `${item.mediaYear}` } : undefined,
              item.seasonNumber !== undefined ? { text: `Season ${item.seasonNumber}` } : undefined,
              currentQualityText ? { text: currentQualityText, quality: true } : undefined,
              // After the quality, as the movie page and release lists show it.
              item.currentEdition ? { text: item.currentEdition } : undefined,
            ].filter((part): part is { text: string; quality?: boolean } => Boolean(part))
            // With no details pane, the row itself is the checkbox.
            const open = () => { if (batchable) toggleChosen(item.id) }
            const profileName = profiles.find((profile) => profile.id === item.qualityProfileId)?.name
              ?? profiles.find((profile) => profile.isDefault)?.name ?? 'Default'
            const tokens = diffReleaseTokens(item.title, item.currentReleaseName)
            const tokenReasons = shareCombinedDynamicRangeReasons(tokens, tokens.map((token) => upgradeTokenReason(token, {
              upgradeTypes: item.upgradeTypes as string[],
              addedCustomFormats: candidateFormatReasons(item.comparisonSnapshot, item.addedCustomFormats),
              currentQuality: item.currentQuality,
              quality: item.quality,
              formatSections,
            })))
            const indexerBadge = badgeColor(item.indexerColor)
            const running = item.executionState === 'submitted' || item.executionState === 'downloading' || item.executionState === 'validating'
            const stuck = item.executionState === 'handoff_attention' || item.executionState === 'cleanup_required'
            // How far along the download is; a sent torrent has not started
            // and a checked one is complete.
            const runningPercent = item.executionState === 'validating' ? 100 : item.executionState === 'submitted' ? 0 : progress ?? 0
            const runningLabel = item.executionState === 'submitted' ? 'Sent' : item.executionState === 'validating' ? 'Checking' : `${runningPercent}%`
            // What is wrong, in the row itself: the client's or cleanup's own
            // words, then what is at stake.
            // A failed attempt says why on the row; the upgrade can be started again.
            const failed = item.executionState === 'failed'
            const problem = failed
              ? [`Last attempt failed: ${item.executionError ?? 'no reason was recorded'}`, item.submittedAt ? formatAge(item.submittedAt) : undefined]
              : item.executionState === 'handoff_attention'
              ? [item.executionError ?? 'The download client never confirmed the torrent.', item.submittedAt ? `sent ${formatAge(item.submittedAt)}` : undefined]
              : item.executionState === 'cleanup_required'
                ? [item.executionError ?? 'The old release still needs removing.', item.currentSize ? `frees ~${formatBytes(item.currentSize)}` : undefined]
                : []
            const sizeDelta = item.size !== undefined && item.currentSize !== undefined ? item.size - item.currentSize : undefined
            // A big score jump reads as one; a few points stay quiet.
            const scoreWeight = delta >= 100 ? 'big' : delta >= 25 ? 'medium' : 'small'
            // Seeds and size are columns level with the release name, under
            // their headers; the indexer sits below them on the score's line.
            const releaseFacts = <span className="upgrade-v2-facts">
              <UpgradePill facts={item} />
              <span className="release-pill release-pill-indexer" style={indexerBadge ? { color: indexerBadge.text, borderColor: indexerBadge.border, background: indexerBadge.background } : undefined}><i>Indexer</i>{item.indexerName}</span>
            </span>
            return <div className="new-media-row" key={item.id}>
              <div className={`new-media-folder-cell upgrade-v2-current ${active ? 'active' : ''} ${blocked ? 'excluded' : ''} ${running ? 'replacing' : ''}`} onClick={open}>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={included}
                  aria-label={`Upgrade ${item.mediaTitle}`}
                  className={`new-media-check ${included ? 'on' : ''}`}
                  disabled={!batchable}
                  // Including a row in the batch is not the same intent as
                  // opening it, so the checkbox must not also select the row.
                  onClick={(event) => { event.stopPropagation(); toggleChosen(item.id) }}
                >{included && <Icon name="check" size={11} />}</button>
                <span className="new-media-poster">{item.posterRef
                  ? <img src={tmdbPosterUrl(item.posterRef)} alt="" loading="lazy" referrerPolicy="no-referrer" />
                  : <Icon name={item.mediaType === 'movies' ? 'clapper' : 'tv-screen'} size={16} />}</span>
                <span className="new-media-match-copy upgrade-v2-title">
                  <strong>{item.mediaTitle}</strong>
                  <small>{detailParts.length
                    ? detailParts.map((part, index) => <Fragment key={index}>
                      {index > 0 && ' · '}
                      {part.quality ? <span className={`upgrade-current-quality ${qualityHovered ? 'hovered' : ''}`}>{part.text}</span> : part.text}
                    </Fragment>)
                    : item.mediaType === 'movies' ? 'Movie' : 'Show'}</small>
                  {/* Where the current release came from, under its title. */}
                  <ReleaseSourcePill indexer={item.currentIndexer} color={item.currentIndexerColor} tracker={item.currentTracker} />
                </span>
                {/* The release on disk fills the rest of the row on one line;
                    the full name is a hover away. */}
                <div className="upgrade-v2-side">
                  <div className="upgrade-v2-line">
                    <span className="upgrade-v2-release">{breakableRelease(currentReleaseLabel(item))}</span>
                  </div>
                  <small className="upgrade-v2-score">CF: {item.currentCustomFormatScore}</small>
                </div>
                {blocked && <Tooltip content={item.executionBlockReason ?? 'This upgrade cannot be started.'}><span className="new-media-state needs-match"><Icon name="lock" size={11} />Blocked</span></Tooltip>}
              </div>
              <div className={`new-media-match-cell upgrade-v2-change ${active ? 'active' : ''} ${blocked ? 'excluded' : ''}`}>
                {/* Opposite the current release across the divide, only the
                    changed words lit, so the two names read as a pair. */}
                <div className="upgrade-v2-side">
                  <div className="upgrade-v2-line">
                    <span className="upgrade-v2-release">{tokens.map((token, index) => <Fragment key={index}>
                      <span className="upgrade-token-sep">{token.sep}</span>{token.sep && <wbr />}
                      {/* Pointing at a lit word turns it blue and puts the score of
                          the Custom Format it earned beside CF, also blue. */}
                      {tokenReasons[index]
                        ? <span
                          // Quality words wear a soft chip, so they read apart
                          // from the words a Custom Format scored.
                          className={`upgrade-token-new ${tokenReasons[index]!.startsWith('Quality:') ? 'quality' : ''} ${hoveredReason?.id === item.id && hoveredReason.reason === tokenReasons[index] ? 'hovered' : ''}`}
                          onMouseEnter={() => {
                            const reason = tokenReasons[index]!
                            const match = UPGRADE_REASON_SCORE.exec(reason)
                            if (match) setHoveredReason({ id: item.id, reason, score: Number(match[1]) })
                            else if (reason.startsWith('Quality:')) setHoveredReason({ id: item.id, reason })
                          }}
                          onMouseLeave={() => setHoveredReason(null)}
                        >{token.text}</span>
                        : <span className="upgrade-token-same">{token.text}</span>}
                    </Fragment>)}</span>
                    {running && <>
                      <span className="upgrade-v2-live">{runningLabel}</span>
                      <span className="upgrade-v2-live upgrade-v2-speed">{item.downloadSpeed ? `${formatBytes(item.downloadSpeed)}/s` : '-'}</span>
                      <span className="upgrade-v2-live">{item.executionState === 'downloading' ? formatEta(item.downloadEtaSeconds) : '-'}</span>
                    </>}
                    {stuck && <span className="upgrade-v2-action">{item.executionState === 'handoff_attention'
                      ? <Button variant="secondary" icon="refresh" disabled={busyId === item.id} onClick={(event) => { event.stopPropagation(); void retryHandoff(item) }}>{busyId === item.id ? 'Checking…' : 'Retry'}</Button>
                      : <Button variant="danger" icon="alert" disabled={busyId === item.id} onClick={(event) => { event.stopPropagation(); void reviewCleanup(item) }}>Clean up</Button>}</span>}
                    {!running && !stuck && <>
                    <span className="upgrade-v2-seeds">{(swarm === 'dead' || swarm === 'weak')
                      ? <Tooltip content={`${item.seeders ?? 0} seeders: this release may be slow or impossible to download.`}><span className={`release-pill release-pill-swarm swarm-${swarm}`}>{formatPeers(item.seeders, item.leechers)}</span></Tooltip>
                      : <span className={`release-pill release-pill-swarm swarm-${swarm}`}>{formatPeers(item.seeders, item.leechers)}</span>}</span>
                    <span className="upgrade-v2-size upgrade-fact">{item.size === undefined ? '-' : formatBytes(item.size)}</span>
                    <span className="upgrade-v2-size-change upgrade-fact">{item.size !== undefined && sizeDelta !== undefined && sizeDelta !== 0 && <em className={sizeDelta > 0 ? 'size-up' : 'size-down'} aria-label={`${sizeDelta > 0 ? 'Larger' : 'Smaller'} by ${formatBytes(Math.abs(sizeDelta))}`}><Icon name="caret" size={10} />{formatBytes(Math.abs(sizeDelta))}</em>}</span>
                    </>}
                  </div>
                  {stuck || failed ? <div className="upgrade-v2-meta">
                    <small className="upgrade-v2-problem" title={problem.filter(Boolean).join(' · ')}>
                      <Icon name="alert" size={11} />{problem.filter(Boolean).join(' · ')}
                    </small>
                  </div> : <div className="upgrade-v2-meta">
                    <span onClick={(event) => event.stopPropagation()}><UpgradeCompare item={item} profile={profileName}>
                      <small className={`upgrade-v2-score ${scoreWeight}`}>CF: {item.customFormatScore} {hoveredReason?.id === item.id && hoveredReason.score !== undefined
                        ? <span className="cf-score-delta hovered">{formatScoreDelta(hoveredReason.score)}</span>
                        : <span className="cf-score-delta">{formatScoreDelta(item.customFormatScore - item.currentCustomFormatScore)}</span>}</small>
                    </UpgradeCompare></span>
                    {/* The download fills the space between score and indexer. */}
                    {running && <span className="upgrade-v2-bar" style={{ '--upgrade-progress': `${runningPercent}%` } as CSSProperties} aria-hidden="true" />}
                    {releaseFacts}
                  </div>}
                </div>
              </div>
              {/* Outside the panel, so the two columns keep their width. */}
              <span className="upgrade-v3-rail">
                {upgradeStartable(item) && <Tooltip asChild content="View all releases">
                  <button type="button" className="icon-button" aria-label={`View all releases for ${item.mediaTitle}`} onClick={() => void openResults(item)}><Icon name="search" size={15} /></button>
                </Tooltip>}
              </span>
            </div>
          })}
        </div>

        <div className="new-media-add">
          {shownStatus === 'ready' ? <>
            <span className="new-media-add-profile">
              <span className="field-label">Quality Profile</span>
              <Dropdown
                ariaLabel="Quality Profile for every upgrade candidate in this folder"
                value={sharedProfile ?? MIXED_PROFILE}
                disabled={profileBusy || !profileTargets.length}
                options={[
                  ...(sharedProfile === undefined ? [{ value: MIXED_PROFILE, label: 'Mixed', disabled: true }] : []),
                  { value: '', label: defaultProfileLabel(profiles) },
                  ...profiles.map((profile) => ({ value: profile.id, label: profile.name })),
                ]}
                onChange={(value) => setProfilePrompt(value || '')}
              />
            </span>
            <div className="toolbar-spacer" />
            <Button variant="secondary" icon="refresh" disabled={searching} onClick={() => void search()}>{searching ? 'Starting…' : 'Search for upgrades'}</Button>
            <Button
              variant="primary"
              icon="download"
              disabled={!picked.length || Boolean(busyId)}
              onClick={openConfirm}
            >
              {`Upgrade ${picked.length} ${picked.length === 1 ? noun : `${noun}s`}${pickedBytes ? ` · ${formatBytes(pickedBytes)}` : ''}`}
              {pickedBytesChange !== 0 && <em className={`upgrade-batch-change ${pickedBytesChange > 0 ? 'size-up' : 'size-down'}`} aria-label={`${pickedBytesChange > 0 ? 'Larger' : 'Smaller'} by ${formatBytes(Math.abs(pickedBytesChange))} in total`}>
                <Icon name="caret" size={11} />{formatBytes(Math.abs(pickedBytesChange))}
              </em>}
            </Button>
          </> : <>
            <span>{shownStatus === 'attention' ? 'These upgrades are waiting for you. Select one to see what it needs.' : 'Upgrades on their way. Progress updates live.'}</span>
            <div className="toolbar-spacer" />
            <Button variant="secondary" icon="refresh" disabled={searching} onClick={() => void search()}>{searching ? 'Starting…' : 'Search for upgrades'}</Button>
          </>}
        </div>

      </div>
    </>}

    {profilePrompt !== null && <Modal
      title="Change Quality Profile"
      eyebrow={rootName}
      onClose={() => { if (!profileBusy) setProfilePrompt(null) }}
      footer={<><span className="modal-foot-spacer" /><Button variant="ghost" disabled={profileBusy} onClick={() => setProfilePrompt(null)}>Cancel</Button><Button variant="primary" icon="check" disabled={profileBusy} onClick={() => void setAllProfiles(profilePrompt || null)}>{profileBusy ? 'Applying…' : 'Change Quality Profile'}</Button></>}
    >
      <div className="upgrade-modal-copy modal-copy-inset">
        <p>
          This sets the <strong>{profiles.find((profile) => profile.id === profilePrompt)?.name ?? defaultProfileLabel(profiles)}</strong> quality
          profile to the {profileTargets.length} selected {profileTargets.length === 1 ? (mediaType === 'movies' ? 'movie' : 'show') : (mediaType === 'movies' ? 'movies' : 'shows')} in the <strong>{rootName}</strong> media folder.
        </p>
        <p>The profile belongs to the title, not to this page, so the change applies everywhere. Every candidate here is re-scored against the new profile: scores can change, and a row can disappear entirely if its release is no longer beaten under it.</p>
      </div>
    </Modal>}

    {confirming && <Modal
      title="Start upgrade"
      eyebrow={`${picked.length} ${picked.length === 1 ? noun.toLowerCase() : `${noun.toLowerCase()}s`} in ${rootName}`}
      icon="download"
      onClose={() => { if (!busyId) setConfirming(false) }}
      footer={<><Button variant="ghost" disabled={Boolean(busyId)} onClick={() => setConfirming(false)}>Cancel</Button><Button variant="primary" icon="download" disabled={Boolean(busyId) || !clientId || !category || categoryLoading} onClick={() => void executeChosen()}>{busyId ? 'Submitting…' : 'Start upgrade'}</Button></>}
    >
      <div className="upgrade-modal-copy modal-copy-inset">
        <p>By starting this upgrade, you authorize Medialogue to complete the full replacement workflow for every selected title:</p>
        <ol className="upgrade-confirm-steps">
          <li><strong>Download and validate the upgrade.</strong><span>The current release remains untouched unless the replacement completes and is verified on disk.</span></li>
          <li><strong>Retire the old torrents.</strong><span>All torrent entries associated with the old release, including recognized cross-seeds, are removed from qBittorrent.</span></li>
          <li><strong>Remove the old media.</strong><span>The superseded release folder or files are deleted automatically after safety checks pass.</span></li>
        </ol>
        <p>If Medialogue cannot verify the replacement or safely identify the old files, it stops and asks for attention without deleting them.</p>
      </div>
      <div className="settings-form upgrade-destination-form">
        <SettingsGroup>
          <Field label="qBittorrent client" help="Which client receives every grab in this upgrade.">
            <Select value={clientId} onChange={(event) => setClientId(event.target.value)}>{eligibleClients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</Select>
          </Field>
          <Field label="Category" help="qBittorrent owns the save path this category resolves to.">
            <Select value={category} disabled={!clientId || categoryLoading} onChange={(event) => setCategory(event.target.value)}>{categoryLoading && <option value="">Loading categories…</option>}{!categoryLoading && categories.map((item) => <option key={item.name} value={item.name}>{item.name}{item.isDefault ? ' · default' : ''}</option>)}</Select>
          </Field>
        </SettingsGroup>
        <div className="wizard-destination-path"><span>Save path</span><strong>{categories.find((item) => item.name === category)?.resolvedSavePath || 'Select a category'}</strong><small>Medialogue does not set a save path or move the downloaded media.</small></div>
      </div>
    </Modal>}

    {resultsTarget && <Modal
      title={resultsTarget.mediaTitle}
      titleSuffix={resultsTarget.mediaYear ? <span className="upgrade-results-title-year"> ({resultsTarget.mediaYear})</span> : undefined}
      eyebrow={resultsTarget.seasonNumber !== undefined ? `SEASON ${resultsTarget.seasonNumber}` : undefined}
      className="upgrade-results-modal"
      onClose={() => { if (!resultSelecting) { setResultsTarget(null); setSearchResults(null) } }}
      footer={<><span className="wizard-foot-count">{searchResults ? `${searchResults.results.length} Torrent${searchResults.results.length === 1 ? '' : 's'} found` : ''}</span><span className="modal-foot-spacer" /><Button variant="ghost" disabled={resultSelecting} onClick={() => { setResultsTarget(null); setSearchResults(null) }}>Cancel</Button><Button variant="primary" icon="check" disabled={resultSelecting || !selectedResultId || !searchResults?.results.find((entry) => entry.id === selectedResultId)?.eligible} onClick={() => void chooseSearchResult()}>{resultSelecting ? 'Selecting…' : 'Use this result'}</Button></>}
    >
      <div className="upgrade-results-current">
        {(() => {
          // Laid out on the release table's own grid so the current release reads
          // as a row of the list above it: format box over FORMAT, score over CF SCORE.
          const { resolution, type } = splitQuality(resultsTarget.currentQuality)
          const score = resultsTarget.currentCustomFormatScore
          const edition = showResultsEdition ? upgradeEditionLabel(resultsTarget.currentEdition) : undefined
          return <div className="upgrade-results-current-row">
            <Tooltip asChild content={resultsTarget.currentQuality || 'Unknown quality'}>
              <span className="release-quality">
                {resolution ? <strong>{resolution}</strong> : null}
                <small>{type || 'Unknown'}</small>
              </span>
            </Tooltip>
            <span className="release-name">
              <strong>{resultsTarget.currentReleaseName}</strong>
              <span className="release-tags">
                {edition ? <em className="release-edition">{edition}</em> : null}
                <span className="release-pill upgrade-current-release-pill">Current release</span>
              </span>
            </span>
            <span className={`release-numeric release-score ${score < 0 ? 'negative' : score > 0 ? 'positive' : ''}`}>{score > 0 ? `+${score}` : score}</span>
          </div>
        })()}
      </div>
      {resultsLoading ? <div className="table-loading">Loading cached search results…</div> : searchResults && <div className="upgrade-results-browser">
        <ReleaseResults
          results={upgradeReleaseResults}
          selectedId={selectedResultId}
          onSelect={setSelectedResultId}
          disabled={resultSelecting}
          isSelectable={(release) => Boolean(searchResults.results.find((entry) => entry.id === release.id)?.eligible)}
          renderTags={(release) => {
            const result = searchResults.results.find((entry) => entry.id === release.id)
            if (!result) return null
            return <>
              {result.recommended && <span className="release-pill upgrade-result-recommended">Recommended</span>}
              {result.selected && <span className="release-pill upgrade-result-selected">Selected</span>}
              {!result.eligible && <Tooltip className="release-pill release-blocked" content={<ul>{result.rejectionReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}><Icon name="alert" size={11} />Rejected</Tooltip>}
            </>
          }}
          label={`Cached upgrade search results for ${resultsTarget.mediaTitle}`}
          empty="No cached releases were returned."
        />
      </div>}
    </Modal>}

    {cleanupTarget && <Modal
      title="Review old media cleanup"
      eyebrow={cleanupTarget.mediaTitle}
      icon="alert"
      wide
      onClose={() => { if (!busyId) { setCleanupTarget(null); setCleanup(null) } }}
      footer={<><Button variant="ghost" disabled={Boolean(busyId)} onClick={() => { setCleanupTarget(null); setCleanup(null) }}>Keep old media</Button><Button variant="danger" icon="alert" disabled={Boolean(busyId) || cleanupLoading || !cleanup?.eligible} onClick={() => void commitCleanup()}>{busyId ? 'Deleting…' : 'Delete reviewed old media'}</Button></>}
    >
      {cleanupLoading ? <div className="table-loading">Building a fresh filesystem preview…</div> : cleanup && <div className="upgrade-cleanup-review">
        <div className="settings-note"><Icon name="info" size={16} /><span>The replacement is already authoritative and the old torrent has been removed from qBittorrent without deleting files. Only the paths below will be deleted.</span></div>
        {cleanup.warnings.map((warning) => <div className="settings-note" key={warning}><Icon name="alert" size={16} /><span>{warning}</span></div>)}
        {cleanup.blocked.map((reason) => <div className="settings-note error-note" key={reason}><Icon name="lock" size={16} /><span>{reason}</span></div>)}
        <div className="upgrade-cleanup-total"><span>Deletion total</span><strong>{formatUpgradeBytes(cleanup.totalBytes)}</strong><small>{cleanup.targets.length} target{cleanup.targets.length === 1 ? '' : 's'} · preview expires {new Date(cleanup.expiresAt).toLocaleTimeString()}</small></div>
        <div className="upgrade-cleanup-targets">{cleanup.targets.map((target) => <div className="upgrade-cleanup-target" key={target.path}><span className="kind-box"><Icon name={target.kind === 'directory' ? 'folder' : 'clapper'} size={13} /></span><div><strong>{target.path}</strong><small>{target.kind} · {target.inventory.length} inventoried item{target.inventory.length === 1 ? '' : 's'}</small></div></div>)}</div>
      </div>}
    </Modal>}
  </Page>
}
