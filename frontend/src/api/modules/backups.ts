import { openApiClient, openApiData, uploadFile } from '../transport'

export type BackupStatus = {
  oauth_configured: boolean
  connected: boolean
  enabled: boolean
  sync_interval_seconds: number
  next_run_at?: string | null
  last_sync_at?: string | null
  last_database_at?: string | null
  last_error?: string | null
  keep_recent: number
  keep_daily: number
  keep_weekly: number
  keep_monthly: number
  database_interval_seconds: number
  torrents_on_drive: number
  folder_url?: string | null
  active_job_id?: string | null
}

type CoverageStatus = 'no_torrent' | 'not_archived' | 'local_only' | 'protected'

export type CoverageGap = { id: string; media_type: 'movies' | 'shows'; title: string; year?: number | null; poster_ref?: string | null; season_number?: number | null; release_name: string; status: CoverageStatus }

export type BackupCoverage = {
  total: number
  protected: number
  local_only: number
  not_archived: number
  no_torrent: number
  gaps: CoverageGap[]
  gaps_truncated: boolean
}

type BackupUploadResult = { written: number; present: number; skipped: number; listed: number; errors: string[] }

export type DatabaseCopy = { id: string; name: string; size?: number | null; created_at?: string | null; url: string; kept_by?: string[]; torrents?: number | null }

export type BackupRetention = Pick<BackupStatus, 'keep_recent' | 'keep_daily' | 'keep_weekly' | 'keep_monthly'>

// The backup endpoints answer with plain objects; their shapes live here.
export const backupsApi = {
  status: async () => await openApiData(openApiClient.GET('/api/v1/backups/status')) as unknown as BackupStatus,
  coverage: async (status?: 'no_torrent' | 'not_archived' | 'local_only') => await openApiData(openApiClient.GET('/api/v1/backups/coverage', { params: { query: { status } } })) as unknown as BackupCoverage,
  saveSettings: (body: { enabled: boolean; database_interval_seconds: number } & BackupRetention) =>
    openApiData(openApiClient.PUT('/api/v1/backups/settings', { body })),
  connect: async () => await openApiData(openApiClient.POST('/api/v1/backups/google/connect')) as unknown as { authorization_url: string },
  disconnect: () => openApiData(openApiClient.POST('/api/v1/backups/google/disconnect')),
  run: () => openApiData(openApiClient.POST('/api/v1/backups/run')),
  /** The backup folder's ZIP, as downloaded from Google Drive. */
  uploadBackup: (file: File) => uploadFile<BackupUploadResult>('/api/v1/backups/restore/upload', file),
  databaseCopies: async () => (await openApiData(openApiClient.GET('/api/v1/backups/database-copies')) as unknown as { items: DatabaseCopy[] }).items,
}
