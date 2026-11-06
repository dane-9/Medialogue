import type { EventHistoryItem, HealthIndicator, Job, RecoveryReimportPlan, ScheduledTask } from '../../types'
import { mapJob } from '../mappers/system'
import { openApiClient, openApiData } from '../transport'

export const systemApi = {
  scheduledTasks: async (): Promise<ScheduledTask[]> => {
    const rows = await openApiData(openApiClient.GET('/api/v1/tasks'))
    return rows.map((item) => ({
      id: item.id,
      name: item.name,
      jobType: item.job_type,
      description: item.description,
      intervalSeconds: item.interval_seconds,
      intervalOptions: item.interval_options,
      configurable: item.configurable,
      enabled: item.enabled,
      lastStartedAt: item.last_started_at ?? undefined,
      lastFinishedAt: item.last_finished_at ?? undefined,
      nextRunAt: item.next_run_at ?? undefined,
      lastStatus: item.last_status ?? undefined,
      lastDurationMs: item.last_duration_ms ?? undefined,
      activeJobId: item.active_job_id ?? undefined,
      activeJobStatus: item.active_job_status ?? undefined,
    }))
  },
  updateScheduledTask: async (taskId: string, intervalSeconds: number | null): Promise<ScheduledTask> => {
    const item = await openApiData(openApiClient.PATCH('/api/v1/tasks/{task_id}', { params: { path: { task_id: taskId } }, body: { interval_seconds: intervalSeconds } }))
    return {
      id: item.id,
      name: item.name,
      jobType: item.job_type,
      description: item.description,
      intervalSeconds: item.interval_seconds,
      intervalOptions: item.interval_options,
      configurable: item.configurable,
      enabled: item.enabled,
      lastStartedAt: item.last_started_at ?? undefined,
      lastFinishedAt: item.last_finished_at ?? undefined,
      nextRunAt: item.next_run_at ?? undefined,
      lastStatus: item.last_status ?? undefined,
      lastDurationMs: item.last_duration_ms ?? undefined,
      activeJobId: item.active_job_id ?? undefined,
      activeJobStatus: item.active_job_status ?? undefined,
    }
  },
  runScheduledTask: (taskId: string) => openApiData(openApiClient.POST('/api/v1/tasks/{task_id}/run', { params: { path: { task_id: taskId } } })),
  health: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/integrations/health'))
    const { plex, qbittorrent, indexers, storage_roots: storageRoots } = payload
    const serviceState = (status: string, enabled = true): HealthIndicator['state'] => !enabled
      ? 'unknown'
      : status === 'healthy' || status === 'available'
        ? 'healthy'
        : status === 'unavailable'
          ? 'offline'
          : status === 'degraded'
            ? 'degraded'
            : 'unknown'
    const latency = (value: number | null | undefined) => value != null ? ` · ${value} ms` : ''
    const latencyFact = (value: number | null | undefined) => value != null
      ? [{ value: `${value} ms`, help: 'How long the latest health check took to receive a response.' }]
      : []
    const scopeFact = (scope: string) => ({
      value: scope === 'both' ? 'Both' : scope === 'movies' ? 'Movies' : scope === 'shows' ? 'Shows' : scope,
      help: scope === 'both'
        ? 'This integration can be used for both movies and shows.'
        : `This integration is used for ${scope} only.`,
    })
    const availableRoots = storageRoots.filter((root) => root.status === 'available').length
    const unavailableRoots = storageRoots.filter((root) => root.status === 'unavailable').length
    const storageState: HealthIndicator['state'] = !storageRoots.length
      ? 'unknown'
      : availableRoots === storageRoots.length
        ? 'healthy'
        : availableRoots
          ? 'degraded'
          : unavailableRoots
            ? 'offline'
            : 'unknown'
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
        {
          name: 'Plex', settingsTab: 'Plex', state: serviceState(plexState), detail: plexDetail,
          items: plex.configured ? [{
            id: 'plex', name: 'Plex server', state: serviceState(plexState), detail: `${plexState}${latency(plex.latency_ms)}`,
            facts: [
              { value: plexState, help: plexState === 'healthy' ? 'Medialogue connected to and authenticated with this Plex server.' : 'Medialogue could not verify this Plex server.' },
              ...latencyFact(plex.latency_ms),
            ],
          }] : [],
        },
        {
          name: 'qBittorrent', settingsTab: 'qBittorrent', state: serviceState(qbittorrentState), detail: qbittorrentDetail,
          items: qbittorrent.clients.map((client) => ({
            id: client.id,
            name: client.name,
            state: serviceState(client.health, client.enabled),
            detail: client.enabled ? `${client.scope} · ${client.health}${latency(client.latency_ms)}` : `${client.scope} · Disabled`,
            facts: [
              scopeFact(client.scope),
              { value: client.enabled ? client.health : 'Disabled', help: !client.enabled ? 'This client is configured but is not currently in use.' : client.health === 'healthy' ? 'Medialogue connected to and authenticated with this qBittorrent client.' : 'Medialogue could not connect to or authenticate with this qBittorrent client.' },
              ...(client.enabled ? latencyFact(client.latency_ms) : []),
            ],
          })),
        },
        {
          name: 'Media Folders', settingsTab: 'Media', state: storageState,
          detail: storageRoots.length ? `${availableRoots}/${storageRoots.length} folders available` : 'No media folders configured',
          items: storageRoots.map((root) => ({
            id: root.id, name: root.name, state: serviceState(root.status), detail: root.status,
            facts: [{
              value: root.status,
              help: root.status === 'available' ? 'Medialogue can currently access this media folder.' : root.status === 'unavailable' ? 'Medialogue cannot currently access this media folder.' : 'This media folder has not been checked yet.',
            }],
          })),
        },
        {
          name: 'Indexers', settingsTab: 'Indexers', state: serviceState(indexers.status),
          detail: indexers.configured ? `${indexers.healthy}/${indexers.enabled} enabled healthy` : 'Not configured',
          items: indexers.items.map((indexer) => ({
            id: indexer.id,
            name: indexer.name,
            state: serviceState(indexer.health, indexer.enabled),
            detail: indexer.enabled ? `${indexer.scope} · ${indexer.health}${latency(indexer.latency_ms)}` : `${indexer.scope} · Disabled`,
            facts: [
              scopeFact(indexer.scope),
              { value: indexer.enabled ? indexer.health : 'Disabled', help: !indexer.enabled ? 'This indexer is configured but is not currently searched.' : indexer.health === 'healthy' ? 'The indexer responded successfully to the latest health check.' : 'The indexer did not respond successfully to the latest health check.' },
              ...(indexer.enabled ? latencyFact(indexer.latency_ms) : []),
            ],
          })),
        },
      ] satisfies HealthIndicator[],
    }
  },
  jobs: async () => {
    const payload = await openApiData(openApiClient.GET('/api/v1/jobs'))
    return payload.items.map(mapJob)
  },
  job: async (jobId: string) => mapJob(await openApiData(openApiClient.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: jobId } } }))),
  recoveryReimportPlan: async (): Promise<RecoveryReimportPlan> => {
    const payload = await openApiData(openApiClient.GET('/api/v1/recovery/reimport/plan'))
    return {
      generatedAt: payload.generated_at,
      expiresAt: payload.expires_at,
      confirmationToken: payload.confirmation_token,
      total: payload.total,
      ready: payload.ready,
      alreadyPresent: payload.already_present,
      blocked: payload.blocked,
      items: (payload.items ?? []).map((item) => ({
        torrentId: item.torrent_id,
        infoHash: item.info_hash,
        torrentName: item.torrent_name,
        mediaType: item.media_type ?? undefined,
        mediaTitle: item.media_title ?? undefined,
        releaseName: item.release_name ?? undefined,
        status: item.status,
        reasons: item.reasons ?? [],
        warnings: item.warnings ?? [],
        downloadClientId: item.download_client_id ?? undefined,
        downloadClientName: item.download_client_name ?? undefined,
        savePath: item.save_path ?? undefined,
        resolvedSavePath: item.resolved_save_path ?? undefined,
        category: item.category ?? undefined,
        tags: item.tags ?? [],
        totalFileCount: item.total_file_count,
        selectedFileCount: item.selected_file_count,
        skippedFileCount: item.skipped_file_count,
      })),
    }
  },
  startRecoveryReimport: (confirmationToken: string, torrentIds: string[]) => openApiData(openApiClient.POST('/api/v1/recovery/reimport', { body: { confirmation_token: confirmationToken, torrent_ids: torrentIds } })),
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
