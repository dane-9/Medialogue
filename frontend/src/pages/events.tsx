import { useEffect, useState } from 'react'
import { eventLabel, eventStamp } from '../lib/eventLabel'
import { Icon } from '../components/Icon'
import { Tooltip } from '../components/Tooltip'
import { useConfirm } from '../components/Confirm'
import { Button, EmptyState, Input, Panel, Select } from '../components/ui'
import { integrationsApi } from '../api/modules/integrations'
import { systemApi } from '../api/modules/system'
import { useUrlNumber, useUrlState } from '../lib/urlState'
import type { EventHistoryItem } from '../types'
import { EventSortHeader, Page, indexerBadgePreview } from './shared'
import { openLiveEvents } from '../lib/liveEvents'

export function EventHistoryPage() {
  const confirm = useConfirm()
  const [events, setEvents] = useState<EventHistoryItem[]>([])
  const [indexerColors, setIndexerColors] = useState<Record<string, string>>({})
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(0)
  const [page, setPage] = useUrlNumber('page', 1)
  const [eventType, setEventType] = useUrlState('type')
  const [severity, setSeverity] = useUrlState('severity')
  const [entityType, setEntityType] = useUrlState('entity')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    integrationsApi.indexers()
      .then((items) => {
        if (!alive) return
        setIndexerColors(Object.fromEntries(items.map((item) => [item.name, item.color ?? ''])))
      })
      .catch(() => undefined)
    return () => { alive = false }
  }, [])
  const [error, setError] = useState('')
  const [pageSize, setPageSize] = useState(100)
  const [sort, setSort] = useState('-created_at')

  const load = async (targetPage = page) => {
    setLoading(true)
    try {
      const payload = await systemApi.events({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined, sort, page: targetPage, pageSize })
      setEvents(payload.items)
      setTotal(payload.total)
      setPages(payload.pages)
      setPage(payload.page)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load events.')
    } finally { setLoading(false) }
  }

  useEffect(() => { setPage(1) }, [eventType, severity, entityType, pageSize, sort])

  useEffect(() => {
    let alive = true
    const refresh = () => systemApi.events({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined, sort, page, pageSize }).then((payload) => {
      if (alive) { setEvents(payload.items); setTotal(payload.total); setPages(payload.pages); setError('') }
    }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : 'Could not load events.') }).finally(() => { if (alive) setLoading(false) })
    void refresh()
    const stream = openLiveEvents()
    const onDurableEvent = () => void refresh()
    ;[
      'download.completed', 'scan.completed', 'scan.failed', 'plex.health', 'plex.verified', 'plex.conflict', 'plex.sync_completed',
      'problem.created', 'problem.resolved', 'storage_root.unavailable', 'storage_root.restored', 'release.replaced',
      'media.missing', 'media.present', 'media.reappeared', 'torrent.detected', 'torrent.removed', 'torrent.reappeared',
      'search.download_submitted', 'show.metadata_refreshed', 'qbittorrent.health',
      'tag.created', 'tag.updated', 'tag.deleted', 'movie.tags_updated', 'movie.monitoring_updated',
      'quality_profile.assignment_updated', 'parser.reevaluated', 'custom_formats.reevaluated', 'bulk.operation_completed',
    ].forEach((name) => stream.addEventListener(name, onDurableEvent))
    const timer = window.setInterval(refresh, 30000)
    return () => { alive = false; stream.close(); window.clearInterval(timer) }
  }, [eventType, severity, entityType, page, pageSize, sort])

  const removeEvent = async (item: EventHistoryItem) => {
    if (!await confirm({
      title: 'Delete this event?',
      body: item.message,
      confirmLabel: 'Delete event',
      note: 'This only removes the row from Events.',
    })) return
    try { await systemApi.deleteEvent(item.id); await load(page) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete event.') }
  }

  const clearHistory = async () => {
    const label = eventType || severity || entityType ? 'all events matching the current filters' : 'ALL events'
    if (!await confirm({
      title: `Delete ${label}?`,
      confirmLabel: 'Delete',
      note: 'Current media, torrents, problems and integrations are unaffected.',
    })) return
    try {
      const result = await systemApi.clearEvents({ eventType: eventType || undefined, severity: severity || undefined, entityType: entityType || undefined })
      setPage(1)
      await load(1)
      setError(result.deleted ? '' : 'No matching history events were present.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not clear events.') }
  }

  return <Page title="Events" subtitle="Every change worth keeping. Live progress is not recorded here." >
    <div className="toolbar event-toolbar">
      <div className="search-field event-search"><Icon name="search" size={16} /><Input value={eventType} onChange={(event) => setEventType(event.target.value)} placeholder={`Search ${total} event${total === 1 ? '' : 's'}, e.g. release.replaced…`} /></div>
      <Select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">All severities</option><option value="info">Info</option><option value="warning">Warning</option><option value="error">Error</option></Select>
      <Select value={entityType} onChange={(event) => setEntityType(event.target.value)}><option value="">All entities</option><option value="movie">Movies</option><option value="show">Shows</option><option value="episode">Episodes</option><option value="movie_release">Movie releases</option><option value="show_release">Show releases</option><option value="torrent">Torrents</option><option value="storage_root">Media folders</option><option value="download_client">Download clients</option><option value="tag">Tags</option><option value="bulk_operation">Bulk operations</option></Select>
      <Select aria-label="Events per page" value={String(pageSize)} onChange={(event) => setPageSize(Number(event.target.value))}>{[50, 100, 150, 200, 250, 500, 1000].map((size) => <option key={size} value={size}>{size} per page</option>)}</Select>
      <Button variant="danger" onClick={() => void clearHistory()}>Clear events</Button>
    </div>
    {error && <div className="settings-note error-note"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {!loading && <Panel className="event-history-panel">

      {events.length > 0 && <div className="event-columns">
        <span />
        <EventSortHeader label="When" field="created_at" sort={sort} onSort={setSort} />
        <EventSortHeader label="Event" field="event_type" sort={sort} onSort={setSort} />
        <span>Details</span>
        <span />
      </div>}
      {events.map((item) => <article className={`event-row event-row-${item.severity}`} key={item.id}>

        <details className="event-details">
          <summary className="event-row-main">

            <span className="event-severity">
              <Tooltip content={item.severity === 'error' ? 'Error' : item.severity === 'warning' ? 'Warning' : 'Info'}>
                <span><Icon name={item.severity === 'error' || item.severity === 'warning' ? 'alert' : 'info'} size={14} /></span>
              </Tooltip>
            </span>
            <span className="event-time">{eventStamp(item.createdAt)}</span>
            <span className="event-kind">{eventLabel(item.eventType)}</span>
            <div className="event-body">
              <p className="event-message">{item.message}</p>
              {eventIndexerName(item.details) ? <span className="release-pill release-pill-indexer event-indexer" style={indexerBadgePreview(indexerColors[eventIndexerName(item.details)!] ?? '')}><i>Indexer</i>{eventIndexerName(item.details)}</span> : null}
            </div>
            <span className="event-row-actions">
              <span className="event-delete" role="button" tabIndex={0} aria-label="Delete this event" onClick={(clicked) => { clicked.preventDefault(); void removeEvent(item) }}><Icon name="close" size={14} /></span>
            </span>
          </summary>
          <pre>{JSON.stringify(item.details, null, 2)}</pre>
        </details>
      </article>)}
      {!events.length && <div className="history-empty">No events match the selected filters.</div>}
    </Panel>}
    {!loading && pages > 1 && <div className="pagination-bar"><Button variant="ghost" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</Button><span>Page {page} of {pages} · {total} events</span><Button variant="ghost" disabled={page >= pages} onClick={() => setPage((value) => Math.min(pages, value + 1))}>Next</Button></div>}
    {loading && <EmptyState icon="clock" title="Loading events" detail="Loading events." />}
  </Page>
}

function eventIndexerName(details: Record<string, unknown>): string | undefined {
  const value = details.indexer_name ?? details.indexerName
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}
