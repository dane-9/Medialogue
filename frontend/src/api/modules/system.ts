import type { EventHistoryItem, HealthIndicator, Job, RecoveryCapabilities } from '../../types'
import { mapJob } from '../mappers/system'
import { openApiClient, openApiData } from '../transport'

export const systemApi = {
  health: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/integrations/health'))
    const { plex, qbittorrent, indexers, storage_roots: storageRoots } = payload
    const storageHealthy = storageRoots.every((root) => root.status === 'available')
    const plexState = plex.configured ? plex.status : 'unknown'
    const plexDetail = !plex.configured
      ? 'Not configured'
      : plexState === 'healthy'
        ? `Connected${plex.latency_ms ? ` · ${plex.latency_ms} ms` : ''}`
        : plexState
    const qbittorrentState = qbittorrent.configured ? qbittorrent.status : 'unknown'
    const qbittorrentDetail = !qbittorrent.configured
      ? 'Not configured'
      : qbittorrent.total_clients
        ? `${qbittorrent.healthy_clients}/${qbittorrent.total_clients} clients healthy`
        : qbittorrentState
    return {
      indicators: [
        { name: 'Plex', state: plexState === 'healthy' ? 'healthy' as const : plexState === 'unavailable' ? 'offline' as const : 'unknown' as const, detail: plexDetail },
        { name: 'qBittorrent', state: qbittorrentState === 'healthy' ? 'healthy' as const : qbittorrentState === 'unavailable' ? 'offline' as const : qbittorrentState === 'degraded' ? 'degraded' as const : 'unknown' as const, detail: qbittorrentDetail },
        { name: 'Storage', state: (storageRoots.length && storageHealthy ? 'healthy' : 'unknown') as HealthIndicator['state'], detail: storageRoots.length ? `${storageRoots.length} roots configured` : 'No roots configured' },
        { name: 'Indexers', state: indexers.status === 'healthy' ? 'healthy' as const : indexers.status === 'unavailable' ? 'offline' as const : indexers.status === 'degraded' ? 'degraded' as const : 'unknown' as const, detail: indexers.configured ? `${indexers.healthy}/${indexers.enabled} enabled healthy` : 'Not configured' },
      ] satisfies HealthIndicator[],
    }
  },
  jobs: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/jobs'))
    return payload.items.map(mapJob)
  },
  job: async (jobId: string) => mapJob(await openApiData(openApiClient.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: jobId } } }))),
  recoveryCapabilities: async (): Promise<RecoveryCapabilities> => {
    const payload = await openApiData(openApiClient.GET('/api/v1/recovery/capabilities'))
    return { supported: payload.supported, databaseBackend: payload.database_backend, sqliteVersion: payload.sqlite_version ?? undefined, databasePath: payload.database_path ?? undefined, databaseReadable: payload.database_readable, migrationRevision: payload.migration_revision ?? undefined, torrentArchiveReadable: payload.torrent_archive_readable, exportDirectoryWritable: payload.export_directory_writable, exportDirectory: payload.export_directory, retentionHours: payload.retention_hours, reasons: payload.reasons ?? [] }
  },
  startRecoveryExport: () => openApiData(openApiClient.POST('/api/v1/recovery/export')),
  recoveryDownloadUrl: (jobId: string) => `/api/v1/recovery/exports/${encodeURIComponent(jobId)}/download`,
  cancelJob: async (jobId: string) => mapJob(await openApiData(openApiClient.POST('/api/v1/jobs/{job_id}/cancel', { params: { path: { job_id: jobId } } }))),
  events: async (filters: { eventType?: string; severity?: string; entityType?: string; sort?: string; page?: number; pageSize?: number } = {}) => {
    const payload = await openApiData(openApiClient.GET('/api/v1/events', { params: { query: {
      event_type: filters.eventType,
      severity: filters.severity,
      entity_type: filters.entityType,
      sort: filters.sort ?? 'newest',
      page: filters.page ?? 1,
      page_size: filters.pageSize ?? 100,
    } } }))
    return {
      items: payload.items.map((item): EventHistoryItem => ({ id: item.id, eventType: item.event_type, severity: item.severity, entityType: item.entity_type, entityId: item.entity_id ?? undefined, message: item.message, details: item.details ?? {}, createdAt: item.created_at })),
      total: payload.total ?? payload.items.length,
      pages: payload.pages ?? 1,
      page: payload.page ?? filters.page ?? 1,
    }
  },
  deleteEvent: (id: string) => openApiData(openApiClient.DELETE('/api/v1/events/{event_id}', { params: { path: { event_id: id } } })),
  clearEvents: (filters: { eventType?: string; severity?: string; entityType?: string } = {}) => openApiData(openApiClient.DELETE('/api/v1/events', { params: { query: {
    event_type: filters.eventType,
    severity: filters.severity,
    entity_type: filters.entityType,
  } } })),
  runReconciliation: () => openApiData(openApiClient.POST('/api/v1/reconciliation/run', { body: {} })),
  waitForJobs: async (jobIds: string[]) => {
    const pending = new Set(jobIds.filter(Boolean))
    const seen = new Set(pending)
    const finished: Job[] = []
    while (pending.size) {
      const jobs = await Promise.all(Array.from(pending, async (jobId) => mapJob(await openApiData(openApiClient.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: jobId } } })))))
      for (const job of jobs) {
        if (!['completed', 'failed', 'cancelled', 'interrupted'].includes(job.state)) continue
        pending.delete(job.id)
        finished.push(job)
        const followups = Array.isArray(job.summary?.followup_job_ids) ? job.summary.followup_job_ids : []
        for (const value of followups) {
          const followupId = String(value || '')
          if (followupId && !seen.has(followupId)) {
            seen.add(followupId)
            pending.add(followupId)
          }
        }
      }
      if (pending.size) await new Promise((resolve) => window.setTimeout(resolve, 500))
    }
    return finished
  },
}
