import type { FilesystemListing, LibrarySummary, RemotePathMapping, StorageRoot, StorageRootScan } from '../../types'
import type { components } from '../generated/schema'

type StorageRootScanDto = components['schemas']['StorageRootScan']
type StorageRootDto = components['schemas']['StorageRootResponse']
type RemotePathMappingDto = components['schemas']['RemotePathMappingResponse']
type FilesystemListingDto = components['schemas']['FilesystemListing']
type LibrarySummaryDto = components['schemas']['LibrarySummary']

export function mapStorageRootScan(value: StorageRootScanDto | null | undefined): StorageRootScan | undefined {
  if (!value) return undefined
  return {
    job_id: value.job_id,
    status: value.status,
    stage: value.stage ?? undefined,
    current: value.current ?? 0,
    total: value.total ?? 0,
    percent: value.percent ?? 0,
  }
}

export function mapStorageRoot(value: StorageRootDto): StorageRoot {
  return {
    id: value.id,
    name: value.name,
    resolved_root_path: value.resolved_root_path,
    media_type: value.media_type,
    access_mode: value.access_mode,
    discovery_mode: value.discovery_mode,
    enabled: value.enabled,
    missing_grace_checks: value.missing_grace_checks,
    scanning: value.scanning ?? false,
    scan: mapStorageRootScan(value.scan),
    media_count: value.media_count ?? 0,
    last_health: value.last_health ?? undefined,
    last_scan_at: value.last_scan_at ?? undefined,
    last_health_checked_at: value.last_health_checked_at ?? undefined,
    affected_media_count: value.affected_media_count,
  }
}

export function mapRemotePathMapping(value: RemotePathMappingDto): RemotePathMapping {
  return {
    id: value.id,
    name: value.name,
    integration_type: value.integration_type,
    integration_id: value.integration_id ?? undefined,
    remote_prefix: value.remote_prefix,
    local_prefix: value.local_prefix,
    storage_root_id: value.storage_root_id ?? undefined,
    enabled: value.enabled,
  }
}

export function mapFilesystemListing(value: FilesystemListingDto): FilesystemListing {
  return {
    current_path: value.current_path,
    parent: value.parent ?? null,
    directories: value.directories ?? [],
    problem: value.problem ?? null,
  }
}

export function mapLibrarySummary(value: LibrarySummaryDto): LibrarySummary {
  return {
    movies: value.movies ?? 0,
    shows: value.shows ?? 0,
    unlinked_directories: value.unlinked_directories ?? 0,
  }
}
