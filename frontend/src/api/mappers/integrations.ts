import type {
  DownloadClient, DownloadClientCategory, DownloadClientTestResult, Indexer, IndexerTestResult,
  PlexConfiguration,
} from '../../types'
import type { components } from '../generated/schema'

type DownloadClientDto = components['schemas']['DownloadClientResponse']
type DownloadClientCategoryDto = components['schemas']['DownloadClientCategoryResponse']
type DownloadClientTestResponseDto = components['schemas']['DownloadClientTestResponse']
type IndexerDto = components['schemas']['IndexerResponse']
type IndexerTestResponseDto = components['schemas']['IndexerTestResponse']
type PlexConfigurationDto = components['schemas']['PlexConfigurationResponse']
type TMDBConfigurationDto = components['schemas']['TMDBConfigurationResponse']

export function mapDownloadClient(value: DownloadClientDto): DownloadClient {
  return {
    id: value.id,
    name: value.name,
    url: value.url,
    username: value.username ?? undefined,
    password_configured: value.password_configured,
    scope: value.scope,
    category: value.category ?? undefined,
    tags: value.tags,
    enabled: value.enabled,
    health: value.health ?? 'unknown',
    last_checked_at: value.last_health_checked_at ?? undefined,
    last_success_at: value.last_success_at ?? undefined,
    latency_ms: value.latency_ms ?? undefined,
    last_error: value.last_error ?? undefined,
    revision: value.revision,
    pollIntervalSeconds: value.poll_interval_seconds,
    recentPriority: value.recent_priority,
    olderPriority: value.older_priority,
    sequentialOrder: value.sequential_order,
    firstLastFirst: value.first_last_first,
    contentLayout: value.content_layout,
    completedDownloadHandling: value.completed_download_handling,
  }
}

export function mapDownloadClientCategory(value: DownloadClientCategoryDto): DownloadClientCategory {
  return {
    name: value.name,
    savePath: value.save_path,
    resolvedSavePath: value.resolved_save_path,
    isDefault: value.is_default ?? false,
  }
}

export function mapDownloadClientTestResult(value: DownloadClientTestResponseDto): DownloadClientTestResult {
  return {
    status: value.status,
    version: value.version ?? undefined,
    latency_ms: value.latency_ms ?? undefined,
    message: value.message ?? undefined,
  }
}

export function mapIndexer(value: IndexerDto): Indexer {
  return {
    id: value.id,
    name: value.name,
    torznabUrl: value.torznab_url,
    apiKeyConfigured: value.api_key_configured,
    scope: value.scope,
    enabled: value.enabled,
    timeoutSeconds: value.timeout_seconds,
    enableRss: value.enable_rss,
    enableInteractiveSearch: value.enable_interactive_search,
    useForUpgrades: value.use_for_upgrades,
    categories: value.categories,
    minimumSeeders: value.minimum_seeders,
    color: value.color ?? undefined,
    multiLanguages: value.multi_languages,
    downloadClientIds: value.download_client_ids,
    priority: value.priority,
    health: value.health,
    lastCheckedAt: value.last_checked_at ?? undefined,
    lastSuccessAt: value.last_success_at ?? undefined,
    latencyMs: value.latency_ms ?? undefined,
    lastError: value.last_error ?? undefined,
    revision: value.revision,
  }
}

export function mapIndexerTestResult(value: IndexerTestResponseDto): IndexerTestResult {
  return {
    status: value.status,
    latencyMs: value.latency_ms ?? undefined,
    title: value.title ?? undefined,
    message: value.message ?? undefined,
  }
}

export function mapPlexConfiguration(value: PlexConfigurationDto): PlexConfiguration {
  return {
    configured: value.configured,
    url: value.url ?? undefined,
    token_configured: value.token_configured ?? false,
    enabled: value.enabled ?? false,
    health: value.health ?? 'unknown',
    machine_identifier: value.machine_identifier ?? undefined,
    last_checked_at: value.last_checked_at ?? undefined,
    last_success_at: value.last_success_at ?? undefined,
    latency_ms: value.latency_ms ?? undefined,
    last_error: value.last_error ?? undefined,
    revision: value.revision ?? undefined,
  }
}

export function mapTmdbConfiguration(value: TMDBConfigurationDto) {
  return {
    configured: value.configured,
    api_key_configured: value.api_key_configured ?? false,
    enabled: value.enabled ?? false,
    health: value.health ?? 'unknown',
    latency_ms: value.latency_ms ?? undefined,
    last_error: value.last_error ?? undefined,
    revision: value.revision ?? undefined,
  }
}
