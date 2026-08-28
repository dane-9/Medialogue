/** Generated from openapi/openapi.json. Do not edit by hand. */

export interface components {
  schemas: {
    "AccessMode": "read_only" | "read_write";
    "AdminResponse": {
          "created_at": string;
          "id": string;
          "is_default_password": boolean;
          "username": string;
        };
    "AssociationType": "incoming" | "attached" | "historical";
    "Collection_CustomFormatResponse_": {
          "items": (components['schemas']["CustomFormatResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_DownloadClientResponse_": {
          "items": (components['schemas']["DownloadClientResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_DownloadResponse_": {
          "items": (components['schemas']["DownloadResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_EventResponse_": {
          "items": (components['schemas']["EventResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_IndexerResponse_": {
          "items": (components['schemas']["IndexerResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_JobResponse_": {
          "items": (components['schemas']["JobResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_MediaDiscoveryResponse_": {
          "items": (components['schemas']["MediaDiscoveryResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_MovieSummaryResponse_": {
          "items": (components['schemas']["MovieSummaryResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_ProblemResponse_": {
          "items": (components['schemas']["ProblemResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_RemotePathMappingResponse_": {
          "items": (components['schemas']["RemotePathMappingResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_ShowSummaryResponse_": {
          "items": (components['schemas']["ShowSummaryResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_StorageRootResponse_": {
          "items": (components['schemas']["StorageRootResponse"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "Collection_TorrentArchiveSummary_": {
          "items": (components['schemas']["TorrentArchiveSummary"])[];
          "page"?: number;
          "page_size"?: number;
          "pages": number;
          "total": number;
        };
    "CustomFormatConditionEvaluationResponse": {
          "condition_id": string;
          "condition_type": components['schemas']["CustomFormatConditionType"];
          "effective_result": boolean;
          "evidence"?: unknown;
          "expected"?: unknown;
          "group"?: (string) | (null);
          "matched": boolean;
          "name"?: (string) | (null);
          "negated": boolean;
          "reason"?: (string) | (null);
          "regex_match"?: (string) | (null);
          "required": boolean;
          "score_offset"?: number;
        };
    "CustomFormatConditionInput": {
          "case_sensitive"?: boolean;
          "group"?: (string) | (null);
          "id"?: (string) | (null);
          "name"?: (string) | (null);
          "negate"?: boolean;
          "pattern"?: (string) | (null);
          "required"?: boolean;
          "score_offset"?: number;
          "type": components['schemas']["CustomFormatConditionType"];
          "value"?: (string) | ((string)[]) | (null);
        };
    "CustomFormatConditionResponse": {
          "case_sensitive": boolean;
          "group"?: (string) | (null);
          "id": string;
          "name": (string) | (null);
          "negate": boolean;
          "pattern"?: (string) | (null);
          "required": boolean;
          "score_offset"?: number;
          "type": components['schemas']["CustomFormatConditionType"];
          "value"?: unknown;
        };
    "CustomFormatConditionType": "release_title" | "release_group" | "quality" | "quality_modifier" | "resolution" | "source" | "edition" | "language" | "indexer" | "web_provider" | "video_codec" | "audio_codec" | "audio_channels" | "hdr_type" | "release_attribute";
    "CustomFormatCreate": {
          "conditions": (components['schemas']["CustomFormatConditionInput"])[];
          "description"?: (string) | (null);
          "enabled"?: boolean;
          "media_scope"?: components['schemas']["CustomFormatScope"];
          "name": string;
        };
    "CustomFormatEvaluationResponse": {
          "conditions": (components['schemas']["CustomFormatConditionEvaluationResponse"])[];
          "contribution"?: (number) | (null);
          "custom_format_id": string;
          "custom_format_name": string;
          "error"?: (string) | (null);
          "group_results": { [key: string]: boolean };
          "matched": boolean;
          "profile_score"?: (number) | (null);
          "score_offset"?: number;
        };
    "CustomFormatExportBundle": {
          "application"?: string;
          "custom_formats": (components['schemas']["CustomFormatExportItem"])[];
          "schema_version"?: number;
        };
    "CustomFormatExportItem": {
          "conditions": (components['schemas']["CustomFormatConditionInput"])[];
          "description"?: (string) | (null);
          "enabled"?: boolean;
          "media_scope"?: components['schemas']["CustomFormatScope"];
          "name": string;
          "schema_version"?: number;
        };
    "CustomFormatImportBundle": {
          "application": string;
          "custom_formats": (components['schemas']["CustomFormatExportItem"])[];
          "schema_version": number;
        };
    "CustomFormatImportResponse": {
          "count": number;
          "imported": (components['schemas']["CustomFormatResponse"])[];
        };
    "CustomFormatLayout": {
          "sections": (components['schemas']["CustomFormatSection"])[];
        };
    "CustomFormatResponse": {
          "builtin"?: boolean;
          "condition_count": number;
          "conditions": (components['schemas']["CustomFormatConditionResponse"])[];
          "created_at": string;
          "description": (string) | (null);
          "enabled": boolean;
          "id": string;
          "media_scope": components['schemas']["CustomFormatScope"];
          "name": string;
          "revision": number;
          "schema_version": number;
          "updated_at": string;
          "used_by_profiles": number;
        };
    "CustomFormatScope": "movies" | "shows" | "both";
    "CustomFormatSection": {
          "format_ids"?: (string)[];
          "id": string;
          "name": string;
        };
    "CustomFormatTestAllRequest": {
          "include_disabled"?: boolean;
          "indexer"?: (string) | (null);
          "languages"?: (string)[];
          "media_scope"?: components['schemas']["CustomFormatScope"];
          "quality_profile_id"?: (string) | (null);
          "release_name": string;
        };
    "CustomFormatTestAllResponse": {
          "formats": (components['schemas']["CustomFormatEvaluationResponse"])[];
          "matched_count": number;
          "parsed": { [key: string]: unknown };
          "quality_profile_id"?: (string) | (null);
          "quality_profile_name"?: (string) | (null);
          "total_score"?: (number) | (null);
        };
    "CustomFormatTestDefinition": {
          "conditions": (components['schemas']["CustomFormatConditionInput"])[];
          "description"?: (string) | (null);
          "enabled"?: boolean;
          "id"?: (string) | (null);
          "media_scope"?: components['schemas']["CustomFormatScope"];
          "name"?: string;
        };
    "CustomFormatTestRequest": {
          "custom_format": components['schemas']["CustomFormatTestDefinition"];
          "indexer"?: (string) | (null);
          "languages"?: (string)[];
          "release_name": string;
        };
    "CustomFormatTestResponse": {
          "evaluation": components['schemas']["CustomFormatEvaluationResponse"];
          "parsed": { [key: string]: unknown };
        };
    "CustomFormatUpdate": {
          "conditions"?: ((components['schemas']["CustomFormatConditionInput"])[]) | (null);
          "description"?: (string) | (null);
          "enabled"?: (boolean) | (null);
          "expected_revision"?: (number) | (null);
          "media_scope"?: (components['schemas']["CustomFormatScope"]) | (null);
          "name"?: (string) | (null);
        };
    "DatabaseHealth": {
          "status": "healthy";
        };
    "DeleteResponse": {
          "deleted"?: boolean;
          "id": string;
        };
    "DiscoveryMode": "automatic" | "review";
    "DiscoveryReviewState": "pending" | "approved" | "ignored";
    "DownloadClientCategoryResponse": {
          "is_default"?: boolean;
          "name": string;
          "resolved_save_path": string;
          "save_path": string;
        };
    "DownloadClientCreate": {
          "category"?: (string) | (null);
          "completed_download_handling"?: boolean;
          "content_layout"?: "default" | "original" | "subfolder";
          "enabled"?: boolean;
          "first_last_first"?: boolean;
          "name": string;
          "older_priority"?: "first" | "last";
          "password": string;
          "poll_interval_seconds"?: number;
          "recent_priority"?: "first" | "last";
          "scope": components['schemas']["DownloadScope"];
          "sequential_order"?: boolean;
          "tags"?: (string)[];
          "url": string;
          "username"?: string;
        };
    "DownloadClientHealthItem": {
          "enabled": boolean;
          "health": "unknown" | "healthy" | "unavailable";
          "id": string;
          "last_error"?: (string) | (null);
          "last_health_checked_at"?: (string) | (null);
          "last_polled_at"?: (string) | (null);
          "last_success_at"?: (string) | (null);
          "latency_ms"?: (number) | (null);
          "name": string;
          "scope": components['schemas']["DownloadScope"];
        };
    "DownloadClientResponse": {
          "category": (string) | (null);
          "completed_download_handling": boolean;
          "content_layout": "default" | "original" | "subfolder";
          "created_at": string;
          "enabled": boolean;
          "first_last_first": boolean;
          "health": ("unknown" | "healthy" | "unavailable") | (null);
          "id": string;
          "last_error"?: (string) | (null);
          "last_health_checked_at"?: (string) | (null);
          "last_success_at"?: (string) | (null);
          "latency_ms"?: (number) | (null);
          "name": string;
          "older_priority": "first" | "last";
          "password_configured": boolean;
          "poll_interval_seconds": number;
          "recent_priority": "first" | "last";
          "revision": number;
          "scope": components['schemas']["DownloadScope"];
          "sequential_order": boolean;
          "tags": (string)[];
          "updated_at": string;
          "url": string;
          "username": (string) | (null);
        };
    "DownloadClientSavedTestRequest": {
          "password"?: (string) | (null);
          "url"?: (string) | (null);
          "username"?: (string) | (null);
        };
    "DownloadClientTestRequest": {
          "password": string;
          "url": string;
          "username"?: string;
        };
    "DownloadClientTestResponse": {
          "latency_ms"?: (number) | (null);
          "message"?: (string) | (null);
          "status": "unknown" | "healthy" | "unavailable";
          "version"?: (string) | (null);
        };
    "DownloadClientUpdate": {
          "category"?: (string) | (null);
          "completed_download_handling"?: (boolean) | (null);
          "content_layout"?: ("default" | "original" | "subfolder") | (null);
          "enabled"?: (boolean) | (null);
          "expected_revision"?: (number) | (null);
          "first_last_first"?: (boolean) | (null);
          "name"?: (string) | (null);
          "older_priority"?: ("first" | "last") | (null);
          "password"?: (string) | (null);
          "poll_interval_seconds"?: (number) | (null);
          "recent_priority"?: ("first" | "last") | (null);
          "scope"?: (components['schemas']["DownloadScope"]) | (null);
          "sequential_order"?: (boolean) | (null);
          "tags"?: ((string)[]) | (null);
          "url"?: (string) | (null);
          "username"?: (string) | (null);
        };
    "DownloadClientsHealth": {
          "clients": (components['schemas']["DownloadClientHealthItem"])[];
          "configured": boolean;
          "healthy_clients": number;
          "status": "unknown" | "healthy" | "degraded" | "unavailable";
          "total_clients": number;
        };
    "DownloadResponse": {
          "category": (string) | (null);
          "client_id": string;
          "client_name": string;
          "completed_at"?: (string) | (null);
          "edition"?: (string) | (null);
          "first_seen_at": string;
          "id": string;
          "incoming"?: boolean;
          "incoming_kind"?: ("replacement" | "release") | (null);
          "info_hash": string;
          "is_present": boolean;
          "last_seen_at": string;
          "media_state"?: ("present" | "missing") | (null);
          "movie_id"?: (string) | (null);
          "name": string;
          "progress": (number) | (null);
          "quality"?: (string) | (null);
          "reconciliation_detail"?: (string) | (null);
          "reconciliation_state"?: ("incoming" | "attached" | "historical" | "conflict") | (null);
          "removed_at": (string) | (null);
          "reported_save_path": (string) | (null);
          "resolved_save_path": (string) | (null);
          "scope": components['schemas']["DownloadScope"];
          "state": (string) | (null);
          "tags": (string)[];
          "torrent_id": string;
          "total_size": (number) | (null);
        };
    "DownloadScope": "movies" | "shows";
    "DuplicateDirectoryPreview": {
          "access_mode": components['schemas']["AccessMode"];
          "directory_id": string;
          "exists": boolean;
          "files"?: (components['schemas']["DuplicateFilePreview"])[];
          "path": string;
          "storage_root": string;
        };
    "DuplicateFilePreview": {
          "is_symlink"?: boolean;
          "relative_path": string;
          "size"?: (number) | (null);
        };
    "DuplicateReleasePreview": {
          "directories"?: (components['schemas']["DuplicateDirectoryPreview"])[];
          "edition"?: (string) | (null);
          "quality"?: (string) | (null);
          "release_group"?: (string) | (null);
          "release_id": string;
          "release_name": string;
          "state": components['schemas']["ReleaseState"];
          "torrents"?: (components['schemas']["DuplicateTorrentPreview"])[];
        };
    "DuplicateResolveCommitRequest": {
          "confirmation_token": string;
        };
    "DuplicateResolvePreviewRequest": {
          "delete_media"?: boolean;
          "losing_release_ids": (string)[];
          "remove_torrents"?: boolean;
          "winner_release_id": string;
        };
    "DuplicateResolvePreviewResponse": {
          "confirmation_token": string;
          "delete_media": boolean;
          "expires_at": string;
          "losers": (components['schemas']["DuplicateReleasePreview"])[];
          "movie_id": string;
          "movie_title": string;
          "remove_torrents": boolean;
          "torrent_backups_will_be_kept"?: boolean;
          "warnings"?: (string)[];
          "winner": components['schemas']["DuplicateReleasePreview"];
        };
    "DuplicateTorrentPreview": {
          "archived": boolean;
          "clients"?: (string)[];
          "info_hash": string;
          "name": string;
          "qbit_present": boolean;
          "torrent_id": string;
        };
    "EffectiveCustomFormatScore": {
          "custom_format_id": string;
          "custom_format_name": string;
          "effective_score": number;
          "enabled": boolean;
          "override_score": (number) | (null);
          "profile_score": number;
        };
    "EpisodeMappingResponse": {
          "episode_ids": (string)[];
          "episode_numbers": (number)[];
          "manual_override": boolean;
          "media_file_id": string;
          "show_release_id": string;
        };
    "EpisodeMappingUpdate": {
          "episode_ids": (string)[];
        };
    "EpisodeMediaResponse": {
          "exists": boolean;
          "manual_mapping"?: boolean;
          "mapped_episode_numbers"?: (number)[];
          "media_file_id": string;
          "path": string;
          "quality"?: (string) | (null);
          "release_group"?: (string) | (null);
          "release_name"?: (string) | (null);
          "release_scope"?: (components['schemas']["ReleaseScope"]) | (null);
          "show_release_id"?: (string) | (null);
        };
    "EpisodeOrderingOption": {
          "description"?: (string) | (null);
          "episode_count": number;
          "id": (string) | (null);
          "name": string;
          "network"?: (string) | (null);
          "season_count": number;
          "selected": boolean;
          "type_label": string;
        };
    "EpisodeResponse": {
          "air_date"?: (string) | (null);
          "episode_number": number;
          "id": string;
          "media"?: (components['schemas']["EpisodeMediaResponse"])[];
          "monitored": boolean;
          "plex_state"?: "matched" | "pending" | "not_found" | "conflict" | "multiple_versions" | "unavailable" | "unknown";
          "presence_state": components['schemas']["PresenceState"];
          "quality"?: (string) | (null);
          "revision": number;
          "season_number": number;
          "title"?: (string) | (null);
          "tmdb_id"?: (number) | (null);
          "tvdb_id"?: (number) | (null);
        };
    "EpisodeUpdate": {
          "expected_revision"?: (number) | (null);
          "monitored"?: (boolean) | (null);
        };
    "EventResponse": {
          "created_at": string;
          "details": { [key: string]: unknown };
          "entity_id": (string) | (null);
          "entity_type": string;
          "event_type": string;
          "id": string;
          "message": string;
          "severity": components['schemas']["Severity"];
        };
    "FilesystemEntry": {
          "name": string;
          "path": string;
        };
    "FilesystemListing": {
          "current_path": string;
          "directories"?: (components['schemas']["FilesystemEntry"])[];
          "parent"?: (string) | (null);
          "problem"?: (string) | (null);
        };
    "HTTPValidationError": {
          "detail"?: (components['schemas']["ValidationError"])[];
        };
    "IdentityState": "matched" | "manual" | "uncertain" | "conflict" | "unmatched";
    "IndexerCreate": {
          "api_key": string;
          "categories"?: (number)[];
          "color"?: (string) | (null);
          "download_client_ids"?: (string)[];
          "enable_interactive_search"?: boolean;
          "enable_rss"?: boolean;
          "enabled"?: boolean;
          "minimum_seeders"?: number;
          "multi_languages"?: (string)[];
          "name": string;
          "priority"?: number;
          "scope"?: components['schemas']["IndexerScope"];
          "timeout_seconds"?: number;
          "torznab_url": string;
        };
    "IndexerHealthItem": {
          "enabled": boolean;
          "health": "unknown" | "healthy" | "unavailable";
          "id": string;
          "last_checked_at"?: (string) | (null);
          "last_error"?: (string) | (null);
          "last_success_at"?: (string) | (null);
          "latency_ms"?: (number) | (null);
          "name": string;
          "scope": components['schemas']["IndexerScope"];
        };
    "IndexerResponse": {
          "api_key_configured": boolean;
          "categories": (number)[];
          "color": (string) | (null);
          "created_at": string;
          "download_client_ids": (string)[];
          "enable_interactive_search": boolean;
          "enable_rss": boolean;
          "enabled": boolean;
          "health": "unknown" | "healthy" | "unavailable";
          "id": string;
          "last_checked_at": (string) | (null);
          "last_error": (string) | (null);
          "last_success_at": (string) | (null);
          "latency_ms": (number) | (null);
          "minimum_seeders": number;
          "multi_languages": (string)[];
          "name": string;
          "priority": number;
          "revision": number;
          "scope": components['schemas']["IndexerScope"];
          "timeout_seconds": number;
          "torznab_url": string;
          "updated_at": string;
        };
    "IndexerScope": "movies" | "shows" | "both";
    "IndexerTestRequest": {
          "api_key": string;
          "timeout_seconds"?: number;
          "torznab_url": string;
        };
    "IndexerTestResponse": {
          "latency_ms"?: (number) | (null);
          "message"?: (string) | (null);
          "status": "healthy" | "unavailable";
          "title"?: (string) | (null);
        };
    "IndexerUpdate": {
          "api_key"?: (string) | (null);
          "categories"?: ((number)[]) | (null);
          "color"?: (string) | (null);
          "download_client_ids"?: ((string)[]) | (null);
          "enable_interactive_search"?: (boolean) | (null);
          "enable_rss"?: (boolean) | (null);
          "enabled"?: (boolean) | (null);
          "expected_revision"?: (number) | (null);
          "minimum_seeders"?: (number) | (null);
          "multi_languages"?: ((string)[]) | (null);
          "name"?: (string) | (null);
          "priority"?: (number) | (null);
          "scope"?: (components['schemas']["IndexerScope"]) | (null);
          "timeout_seconds"?: (number) | (null);
          "torznab_url"?: (string) | (null);
        };
    "IndexersHealth": {
          "configured": boolean;
          "enabled": number;
          "healthy": number;
          "items": (components['schemas']["IndexerHealthItem"])[];
          "status": "unknown" | "healthy" | "degraded" | "unavailable";
          "total": number;
        };
    "IntegrationHealthResponse": {
          "database": components['schemas']["DatabaseHealth"];
          "indexers": components['schemas']["IndexersHealth"];
          "plex": components['schemas']["ServiceHealth"];
          "qbittorrent": components['schemas']["DownloadClientsHealth"];
          "storage_roots": (components['schemas']["StorageRootHealthItem"])[];
          "tmdb": components['schemas']["ServiceHealth"];
          "torrent_archive": components['schemas']["TorrentArchiveHealth"];
        };
    "JobAcceptedResponse": {
          "job_id": string;
        };
    "JobResponse": {
          "cancellable": boolean;
          "created_at": string;
          "error": ({ [key: string]: unknown }) | (null);
          "finished_at": (string) | (null);
          "id": string;
          "job_type": string;
          "progress": { [key: string]: unknown };
          "revision": number;
          "started_at": (string) | (null);
          "status": components['schemas']["JobStatus"];
          "summary": { [key: string]: unknown };
          "updated_at": string;
        };
    "JobStatus": "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
    "LibrarySummary": {
          "movies"?: number;
          "shows"?: number;
          "unlinked_directories"?: number;
        };
    "LivenessResponse": {
          "status": "ok";
        };
    "LoginRequest": {
          "password": string;
          "username": string;
        };
    "LoginResponse": {
          "authenticated"?: boolean;
          "csrf_token": string;
          "default_password_warning": boolean;
          "user": components['schemas']["AdminResponse"];
        };
    "ManualAttachRequest": {
          "expected_revision"?: (number) | (null);
          "path": string;
          "release_name"?: (string) | (null);
          "root_id": string;
        };
    "MediaDirectoryResponse": {
          "exists": boolean;
          "files": (string)[];
          "id": string;
          "missing_since": (string) | (null);
          "resolved_path": string;
        };
    "MediaDiscoveryBulk": {
          "action": "approve" | "ignore";
          "ids": (string)[];
        };
    "MediaDiscoveryBulkFailure": {
          "code": string;
          "id": string;
          "message": string;
        };
    "MediaDiscoveryBulkResult": {
          "failed"?: (components['schemas']["MediaDiscoveryBulkFailure"])[];
          "succeeded"?: (string)[];
        };
    "MediaDiscoveryCandidate": {
          "original_title"?: (string) | (null);
          "overview"?: (string) | (null);
          "poster_ref"?: (string) | (null);
          "title": string;
          "tmdb_id": number;
          "year"?: (number) | (null);
        };
    "MediaDiscoveryCount": {
          "count": number;
        };
    "MediaDiscoveryEditionUpdate": {
          "edition"?: (string) | (null);
          "mode": "automatic" | "standard" | "named";
        };
    "MediaDiscoveryFile": {
          "relative_path": string;
          "role": components['schemas']["MediaRole"];
        };
    "MediaDiscoveryResponse": {
          "candidates"?: (components['schemas']["MediaDiscoveryCandidate"])[];
          "confidence"?: (number) | (null);
          "created_at": string;
          "directory_id": string;
          "edition"?: (string) | (null);
          "edition_override_active"?: boolean;
          "exists": boolean;
          "file_count"?: number;
          "files"?: (components['schemas']["MediaDiscoveryFile"])[];
          "hdr"?: (string)[];
          "id": string;
          "library_edition_conflict"?: boolean;
          "manual_edition_override"?: (string) | (null);
          "media_type": components['schemas']["MediaType"];
          "parsed_edition"?: (string) | (null);
          "parsed_title"?: (string) | (null);
          "parsed_year"?: (number) | (null);
          "path": string;
          "quality"?: (string) | (null);
          "review_state": components['schemas']["DiscoveryReviewState"];
          "root_name": string;
          "selected"?: (components['schemas']["MediaDiscoveryCandidate"]) | (null);
          "suggested"?: (components['schemas']["MediaDiscoveryCandidate"]) | (null);
          "updated_at": string;
          "video_count"?: number;
        };
    "MediaDiscoverySearch": {
          "query"?: (string) | (null);
        };
    "MediaDiscoverySelection": {
          "original_title"?: (string) | (null);
          "poster_ref"?: (string) | (null);
          "title"?: (string) | (null);
          "tmdb_id": number;
          "year"?: (number) | (null);
        };
    "MediaProfileSettingsResponse": {
          "custom_format_scores": (components['schemas']["EffectiveCustomFormatScore"])[];
          "entity_id": string;
          "media_type": components['schemas']["MediaType"];
          "minimum_quality_definition": (components['schemas']["QualityDefinitionResponse"]) | (null);
          "minimum_quality_overridden": boolean;
          "profile_minimum_quality_definition": (components['schemas']["QualityDefinitionResponse"]) | (null);
          "quality_profile_id": (string) | (null);
          "quality_profile_name": (string) | (null);
          "revision": number;
        };
    "MediaProfileSettingsUpdate": {
          "custom_format_score_overrides"?: { [key: string]: number };
          "expected_revision"?: (number) | (null);
          "minimum_quality_definition_override_id"?: (string) | (null);
          "quality_profile_id"?: (string) | (null);
        };
    "MediaRole": "movie_video" | "episode_video" | "multi_episode_video" | "dvd_structure" | "bluray_structure" | "other_media";
    "MediaType": "movies" | "shows";
    "MovieBulkAction": "change_profile" | "add_tags" | "remove_tags" | "monitor" | "unmonitor" | "recheck_plex" | "reevaluate_parser" | "reevaluate_custom_formats";
    "MovieBulkRequest": {
          "action": components['schemas']["MovieBulkAction"];
          "movie_ids": (string)[];
          "quality_profile_id"?: (string) | (null);
          "tag_ids"?: (string)[];
        };
    "MovieBulkResponse": {
          "action": components['schemas']["MovieBulkAction"];
          "details"?: { [key: string]: unknown };
          "movie_ids": (string)[];
          "requested": number;
          "updated": number;
        };
    "MovieCreate": {
          "monitored"?: boolean;
          "tmdb_id": number;
        };
    "MovieDetailResponse": {
          "confidence": (number) | (null);
          "current_quality": (string) | (null);
          "edition": (string) | (null);
          "id": string;
          "identity_state": components['schemas']["IdentityState"];
          "incoming"?: boolean;
          "incoming_downloads"?: (components['schemas']["MovieIncomingDownloadResponse"])[];
          "incoming_kind"?: ("replacement" | "release") | (null);
          "last_observed_at"?: (string) | (null);
          "location": (string) | (null);
          "monitored": boolean;
          "overview": (string) | (null);
          "plex_state": "matched" | "pending" | "not_found" | "conflict" | "multiple_versions" | "unavailable" | "unknown";
          "poster_ref": (string) | (null);
          "problem_count"?: number;
          "problems"?: (components['schemas']["MovieProblemResponse"])[];
          "recent_events": (components['schemas']["MovieEventResponse"])[];
          "reconciliation": components['schemas']["MovieReconciliationResponse"];
          "release_count": number;
          "releases": (components['schemas']["MovieReleaseResponse"])[];
          "resource_id": string;
          "root_affected_count"?: number;
          "root_health"?: ("available" | "unavailable" | "unknown") | (null);
          "state": "Present" | "Missing" | "Conflict" | "Duplicate";
          "storage_root"?: (string) | (null);
          "tags"?: (components['schemas']["TagResponse"])[];
          "title": string;
          "tmdb_id": (number) | (null);
          "torrent_history"?: (components['schemas']["MovieTorrentHistoryResponse"])[];
          "year": (number) | (null);
        };
    "MovieDownloadSearchRequest": {
          "quality_profile_id": string;
          "tmdb_id": number;
        };
    "MovieEditionOptionsResponse": {
          "editions": (string)[];
        };
    "MovieEditionOverrideRequest": {
          "updates": (components['schemas']["MovieEditionOverrideUpdate"])[];
        };
    "MovieEditionOverrideResponse": {
          "duplicate_resolved": boolean;
          "movie_id": string;
          "releases": (components['schemas']["MovieEditionState"])[];
        };
    "MovieEditionOverrideUpdate": {
          "edition"?: (string) | (null);
          "mode": "automatic" | "standard" | "named";
          "release_id": string;
        };
    "MovieEditionState": {
          "edition_override_active": boolean;
          "effective_edition"?: (string) | (null);
          "manual_edition_override"?: (string) | (null);
          "parsed_edition"?: (string) | (null);
          "release_id": string;
          "state": components['schemas']["ReleaseState"];
        };
    "MovieEventResponse": {
          "created_at": string;
          "details"?: { [key: string]: unknown };
          "id": string;
          "message": string;
          "type": string;
        };
    "MovieIncomingDownloadResponse": {
          "client_name": string;
          "edition"?: (string) | (null);
          "id": string;
          "incoming_kind": "replacement" | "release";
          "name": string;
          "progress": number;
          "quality"?: (string) | (null);
          "release_id": string;
          "resolved_save_path"?: (string) | (null);
          "state": string;
          "torrent_id": string;
          "torrent_state": string;
        };
    "MovieProblemResponse": {
          "description": string;
          "details"?: { [key: string]: unknown };
          "id": string;
          "message": string;
          "reason": string;
          "severity": components['schemas']["Severity"];
          "title": string;
        };
    "MovieReconciliationResponse": {
          "degraded_count": number;
          "duplicate_count": number;
          "incoming_count": number;
          "missing_count": number;
          "plex_blocked": boolean;
          "qbit_media_disagreement": boolean;
          "replaced_count": number;
          "root_affected_count": number;
          "root_offline": boolean;
          "state": "Present" | "Missing" | "Conflict" | "Duplicate";
        };
    "MovieReleaseResponse": {
          "confidence": (number) | (null);
          "current_custom_format_score": (number) | (null);
          "directories": (components['schemas']["MediaDirectoryResponse"])[];
          "edition": (string) | (null);
          "edition_override_active"?: boolean;
          "first_seen_at": string;
          "id": string;
          "manual_edition_override"?: (string) | (null);
          "original_custom_format_score": (number) | (null);
          "parse_snapshot": { [key: string]: unknown };
          "parsed_edition"?: (string) | (null);
          "quality": (string) | (null);
          "raw_release_name": string;
          "release_group": (string) | (null);
          "selection_snapshot": ({ [key: string]: unknown }) | (null);
          "state": components['schemas']["ReleaseState"];
        };
    "MovieSummaryResponse": {
          "confidence": (number) | (null);
          "current_quality": (string) | (null);
          "edition": (string) | (null);
          "id": string;
          "identity_state": components['schemas']["IdentityState"];
          "incoming"?: boolean;
          "incoming_kind"?: ("replacement" | "release") | (null);
          "last_observed_at"?: (string) | (null);
          "location": (string) | (null);
          "monitored": boolean;
          "plex_state": "matched" | "pending" | "not_found" | "conflict" | "multiple_versions" | "unavailable" | "unknown";
          "poster_ref"?: (string) | (null);
          "problem_count"?: number;
          "release_count": number;
          "resource_id": string;
          "state": "Present" | "Missing" | "Conflict" | "Duplicate";
          "tags"?: (components['schemas']["TagResponse"])[];
          "title": string;
          "tmdb_id": (number) | (null);
          "year": (number) | (null);
        };
    "MovieTorrentHistoryResponse": {
          "archive_path"?: (string) | (null);
          "archive_state": components['schemas']["TorrentArchiveState"];
          "association_type": components['schemas']["AssociationType"];
          "completed_at"?: (string) | (null);
          "download_client_name"?: (string) | (null);
          "first_seen_at": string;
          "id": string;
          "info_hash": string;
          "manifest_path"?: (string) | (null);
          "manifest_schema_version"?: (number) | (null);
          "qbit_present": boolean;
          "release_id": string;
          "release_name": string;
          "torrent_name": string;
          "tracker"?: (string) | (null);
        };
    "ParserTestRequest": {
          "name": string;
        };
    "PasswordChangeRequest": {
          "current_password": string;
          "new_password": string;
        };
    "PlexConfigurationResponse": {
          "configured": boolean;
          "enabled"?: boolean;
          "health"?: "unknown" | "healthy" | "unavailable";
          "last_checked_at"?: (string) | (null);
          "last_error"?: (string) | (null);
          "last_success_at"?: (string) | (null);
          "latency_ms"?: (number) | (null);
          "machine_identifier"?: (string) | (null);
          "revision"?: (number) | (null);
          "token_configured"?: boolean;
          "url"?: (string) | (null);
        };
    "PlexConfigurationUpdate": {
          "enabled"?: boolean;
          "expected_revision"?: (number) | (null);
          "token"?: (string) | (null);
          "url": string;
        };
    "PlexHealthResponse": {
          "configured"?: boolean;
          "enabled"?: boolean;
          "last_error"?: (string) | (null);
          "last_success"?: (string) | (null);
          "latency_ms"?: (number) | (null);
          "machine_identifier"?: (string) | (null);
          "status"?: "unknown" | "healthy" | "unavailable";
        };
    "PlexTestRequest": {
          "token"?: (string) | (null);
          "url"?: (string) | (null);
        };
    "PlexTestResponse": {
          "latency_ms"?: (number) | (null);
          "machine_identifier"?: (string) | (null);
          "message"?: (string) | (null);
          "status": "healthy" | "unavailable";
        };
    "PresenceState": "present" | "missing" | "unmatched" | "conflict";
    "ProblemRecheckAllResponse": {
          "job_ids"?: (string)[];
          "requested": number;
        };
    "ProblemResolveRequest": {
          "action": "restore" | "dismiss" | "recheck" | "confirm_movie_match" | "confirm_show_match";
          "payload"?: { [key: string]: unknown };
        };
    "ProblemResponse": {
          "available_actions"?: ("restore" | "dismiss" | "recheck" | "confirm_movie_match" | "confirm_show_match")[];
          "created_at": string;
          "description"?: string;
          "details": { [key: string]: unknown };
          "entity_id": (string) | (null);
          "entity_type": string;
          "id": string;
          "label"?: string;
          "message": string;
          "reason": string;
          "resolution": ({ [key: string]: unknown }) | (null);
          "resolved_at": (string) | (null);
          "severity": components['schemas']["Severity"];
          "status": components['schemas']["ProblemStatus"];
          "subject"?: (string) | (null);
          "title"?: string;
          "workflow": components['schemas']["ProblemWorkflow"];
        };
    "ProblemStatus": "open" | "resolved" | "dismissed";
    "ProblemSummaryResponse": {
          "open"?: number;
          "suppressed"?: number;
          "workflows"?: { [key: string]: number };
        };
    "ProblemWorkflow": "manual" | "choice" | "config" | "waiting";
    "QualityDefinitionResponse": {
          "enabled": boolean;
          "id": string;
          "modifier": (string) | (null);
          "name": string;
          "rank": number;
          "resolution": (string) | (null);
          "scan_type": (string) | (null);
          "source": (string) | (null);
        };
    "QualityProfileCreate": {
          "custom_format_scores"?: (components['schemas']["QualityProfileScoreInput"])[];
          "minimum_quality_definition_id"?: (string) | (null);
          "name": string;
          "quality_definition_ids"?: ((string)[]) | (null);
        };
    "QualityProfileResponse": {
          "assigned_titles": number;
          "created_at": string;
          "custom_format_scores": (components['schemas']["QualityProfileScoreResponse"])[];
          "id": string;
          "minimum_quality_definition": (components['schemas']["QualityDefinitionResponse"]) | (null);
          "name": string;
          "qualities": (components['schemas']["QualityDefinitionResponse"])[];
          "revision": number;
          "updated_at": string;
        };
    "QualityProfileScoreInput": {
          "custom_format_id": string;
          "score": number;
        };
    "QualityProfileScoreResponse": {
          "custom_format_id": string;
          "custom_format_name": string;
          "enabled": boolean;
          "score": number;
        };
    "QualityProfileUpdate": {
          "custom_format_scores"?: ((components['schemas']["QualityProfileScoreInput"])[]) | (null);
          "expected_revision"?: (number) | (null);
          "minimum_quality_definition_id"?: (string) | (null);
          "name"?: (string) | (null);
          "quality_definition_ids"?: ((string)[]) | (null);
        };
    "ReadinessResponse": {
          "database": "available" | "unavailable";
          "status": "ok" | "not_ready";
        };
    "ReconciliationActionResponse": {
          "details"?: { [key: string]: unknown };
          "movie_id": string;
          "status": "attached";
        };
    "ReconciliationRootStatus": {
          "affected_count": number;
          "health": ("available" | "unavailable" | "unknown") | (null);
          "id": string;
          "name": string;
          "path": string;
        };
    "ReconciliationRunRequest": {
          "root_id"?: (string) | (null);
        };
    "ReconciliationRunResponse": {
          "active_job_ids"?: (string)[];
          "job_ids": (string)[];
          "skipped_root_ids"?: (string)[];
          "uninitialized_root_ids"?: (string)[];
        };
    "ReconciliationStatusResponse": {
          "generated_at": string;
          "incoming_count": number;
          "missing_release_count": number;
          "open_problem_count": number;
          "roots": (components['schemas']["ReconciliationRootStatus"])[];
        };
    "RecoveryCapabilitiesResponse": {
          "database_backend": string;
          "database_path"?: (string) | (null);
          "database_readable": boolean;
          "export_directory": string;
          "export_directory_writable": boolean;
          "migration_revision"?: (string) | (null);
          "reasons"?: (string)[];
          "retention_hours": number;
          "sqlite_version"?: (string) | (null);
          "supported": boolean;
          "torrent_archive_readable": boolean;
        };
    "RecoveryExportAcceptedResponse": {
          "job_id": string;
          "status"?: "queued";
          "warning"?: string;
        };
    "ReleaseScope": "episode" | "multi_episode" | "season_pack" | "other";
    "ReleaseState": "current" | "missing" | "replaced" | "removed" | "conflict" | "duplicate";
    "RemotePathMappingCreate": {
          "enabled"?: boolean;
          "integration_id"?: (string) | (null);
          "integration_type": "qbittorrent" | "plex" | "prowlarr" | "tmdb" | "tvdb";
          "local_prefix": string;
          "name": string;
          "remote_prefix": string;
          "storage_root_id"?: (string) | (null);
        };
    "RemotePathMappingResponse": {
          "enabled": boolean;
          "id": string;
          "integration_id": (string) | (null);
          "integration_type": "qbittorrent" | "plex" | "prowlarr" | "tmdb" | "tvdb";
          "local_prefix": string;
          "name": string;
          "remote_prefix": string;
          "storage_root_id": (string) | (null);
        };
    "RemotePathMappingUpdate": {
          "enabled"?: (boolean) | (null);
          "integration_id"?: (string) | (null);
          "integration_type"?: ("qbittorrent" | "plex" | "prowlarr" | "tmdb" | "tvdb") | (null);
          "local_prefix"?: (string) | (null);
          "name"?: (string) | (null);
          "remote_prefix"?: (string) | (null);
          "storage_root_id"?: (string) | (null);
        };
    "SearchIndexerStatus": {
          "elapsed_ms"?: (number) | (null);
          "error"?: (string) | (null);
          "id": string;
          "name": string;
          "results"?: number;
          "status": "queued" | "completed" | "failed";
        };
    "SearchJobResponse": {
          "created_at": string;
          "error": ({ [key: string]: unknown }) | (null);
          "finished_at": (string) | (null);
          "id": string;
          "indexers": (components['schemas']["SearchIndexerStatus"])[];
          "progress": components['schemas']["SearchProgressResponse"];
          "result_total": number;
          "results": (components['schemas']["SearchResultResponse"])[];
          "started_at": (string) | (null);
          "status": components['schemas']["JobStatus"];
          "target": components['schemas']["SearchTargetResponse"];
        };
    "SearchProgressResponse": {
          "completed_indexers"?: number;
          "percent"?: number;
          "total_indexers"?: number;
        };
    "SearchResultDownloadRequest": {
          "category"?: (string) | (null);
          "download_client_id": string;
          "include_year_in_show_folder"?: boolean;
          "show_folder_name"?: (string) | (null);
        };
    "SearchResultDownloadResponse": {
          "category"?: (string) | (null);
          "client_name": string;
          "download_client_id": string;
          "movie_id"?: (string) | (null);
          "save_path"?: (string) | (null);
          "search_result_id": string;
          "season_id"?: (string) | (null);
          "selected_at": string;
          "show_id"?: (string) | (null);
          "status": "submitted" | "already_submitted";
        };
    "SearchResultResponse": {
          "created_at": string;
          "custom_format_score": (number) | (null);
          "custom_format_snapshot": { [key: string]: unknown };
          "edition": (string) | (null);
          "expires_at": string;
          "id": string;
          "indexer_color"?: (string) | (null);
          "indexer_id": (string) | (null);
          "indexer_name": string;
          "job_id": string;
          "leechers": (number) | (null);
          "media_type": components['schemas']["MediaType"];
          "minimum_quality": (string) | (null);
          "minimum_quality_met": (boolean) | (null);
          "parser": { [key: string]: unknown };
          "published_at": (string) | (null);
          "quality": (string) | (null);
          "quality_allowed": boolean;
          "quality_preference": number;
          "quality_profile_id": (string) | (null);
          "quality_profile_name": (string) | (null);
          "release_group": (string) | (null);
          "seeders": (number) | (null);
          "selected_at": (string) | (null);
          "selected_download_client_id": (string) | (null);
          "size": (number) | (null);
          "target_entity_type": "tmdb_movie" | "tmdb_show_season" | "movie" | "episode" | "season";
          "title": string;
          "warnings": (string)[];
        };
    "SearchTargetResponse": {
          "entity_id"?: (string) | (null);
          "entity_type": "tmdb_movie" | "tmdb_show_season" | "movie" | "episode" | "season";
          "episode"?: (number) | (null);
          "media_type": components['schemas']["MediaType"];
          "overview"?: (string) | (null);
          "poster_ref"?: (string) | (null);
          "profile_entity_id"?: (string) | (null);
          "quality_profile_id"?: (string) | (null);
          "season"?: (number) | (null);
          "title": string;
          "tmdb_id"?: (number) | (null);
          "year"?: (number) | (null);
        };
    "SeasonResponse": {
          "counted"?: boolean;
          "episode_count": number;
          "episodes"?: (components['schemas']["EpisodeResponse"])[];
          "id": string;
          "missing_count": number;
          "monitored": boolean;
          "present_count": number;
          "revision": number;
          "season_number": number;
          "title"?: (string) | (null);
        };
    "SeasonUpdate": {
          "counted"?: (boolean) | (null);
          "expected_revision"?: (number) | (null);
          "monitored"?: (boolean) | (null);
        };
    "SecurityResponse": {
          "default_password_warning": boolean;
          "session_expires_at"?: (string) | (null);
        };
    "ServiceHealth": {
          "configured": boolean;
          "last_success"?: (string) | (null);
          "latency_ms"?: (number) | (null);
          "status": "unknown" | "healthy" | "unavailable";
        };
    "SetupCompleteRequest": {
          "complete"?: boolean;
        };
    "SetupStatusResponse": {
          "steps": (components['schemas']["SetupStep"])[];
          "wizard_complete": boolean;
          "wizard_required": boolean;
        };
    "SetupStep": {
          "complete": boolean;
          "detail": string;
          "key": string;
          "optional"?: boolean;
          "settings_tab"?: (string) | (null);
          "title": string;
        };
    "Severity": "info" | "warning" | "error";
    "ShowCreate": {
          "monitored"?: boolean;
          "tmdb_id": number;
        };
    "ShowDetailResponse": {
          "episode_count": number;
          "episodes_missing": number;
          "episodes_present": number;
          "id": string;
          "identity_state": components['schemas']["IdentityState"];
          "last_observed_at"?: (string) | (null);
          "monitored": boolean;
          "overview"?: (string) | (null);
          "plex_state": "matched" | "pending" | "not_found" | "conflict" | "multiple_versions" | "unavailable" | "unknown";
          "poster_ref"?: (string) | (null);
          "problem_count"?: number;
          "problems"?: (components['schemas']["ShowProblemResponse"])[];
          "recent_events"?: (components['schemas']["ShowEventResponse"])[];
          "resource_id": string;
          "revision": number;
          "season_count": number;
          "seasons"?: (components['schemas']["SeasonResponse"])[];
          "state": "Present" | "Missing" | "Conflict";
          "storage_roots"?: (components['schemas']["ShowStorageRootResponse"])[];
          "title": string;
          "tmdb_episode_group_id"?: (string) | (null);
          "tmdb_id": (number) | (null);
          "tvdb_id"?: (number) | (null);
          "year": (number) | (null);
        };
    "ShowDownloadPreferences": {
          "include_year_in_folder_name"?: boolean;
        };
    "ShowDownloadPreviewResponse": {
          "overview"?: (string) | (null);
          "poster_ref"?: (string) | (null);
          "seasons": (components['schemas']["ShowDownloadSeasonPreview"])[];
          "title": string;
          "tmdb_id": number;
          "year"?: (number) | (null);
        };
    "ShowDownloadSeasonPreview": {
          "air_date"?: (string) | (null);
          "episode_count"?: number;
          "poster_ref"?: (string) | (null);
          "season_number": number;
          "title"?: (string) | (null);
        };
    "ShowEventResponse": {
          "created_at": string;
          "details"?: { [key: string]: unknown };
          "id": string;
          "message": string;
          "type": string;
        };
    "ShowProblemResponse": {
          "description": string;
          "details"?: { [key: string]: unknown };
          "id": string;
          "message": string;
          "reason": string;
          "severity": components['schemas']["Severity"];
          "title": string;
        };
    "ShowSeasonDownloadSearchRequest": {
          "quality_profile_id": string;
          "season_number": number;
          "tmdb_id": number;
        };
    "ShowStorageRootResponse": {
          "health"?: ("available" | "unavailable" | "unknown") | (null);
          "id": string;
          "name": string;
          "path": string;
        };
    "ShowSummaryResponse": {
          "episode_count": number;
          "episodes_missing": number;
          "episodes_present": number;
          "id": string;
          "identity_state": components['schemas']["IdentityState"];
          "last_observed_at"?: (string) | (null);
          "monitored": boolean;
          "plex_state": "matched" | "pending" | "not_found" | "conflict" | "multiple_versions" | "unavailable" | "unknown";
          "poster_ref"?: (string) | (null);
          "problem_count"?: number;
          "resource_id": string;
          "revision": number;
          "season_count": number;
          "state": "Present" | "Missing" | "Conflict";
          "title": string;
          "tmdb_episode_group_id"?: (string) | (null);
          "tmdb_id": (number) | (null);
          "tvdb_id"?: (number) | (null);
          "year": (number) | (null);
        };
    "ShowUpdate": {
          "expected_revision"?: (number) | (null);
          "monitored"?: (boolean) | (null);
          "tmdb_episode_group_id"?: (string) | (null);
        };
    "SpecialsCountingResult": {
          "count_specials": boolean;
          "seasons_changed": number;
          "shows_affected": number;
        };
    "SpecialsCountingState": {
          "count_specials": boolean;
        };
    "StorageRootCreate": {
          "access_mode"?: components['schemas']["AccessMode"];
          "discovery_mode"?: components['schemas']["DiscoveryMode"];
          "enabled"?: boolean;
          "media_type": components['schemas']["MediaType"];
          "missing_grace_checks"?: number;
          "name": string;
          "path": string;
        };
    "StorageRootHealthItem": {
          "checked_at"?: (string) | (null);
          "id": string;
          "name": string;
          "status": "unknown" | "available" | "unavailable";
        };
    "StorageRootReorder": {
          "ids": (string)[];
        };
    "StorageRootResponse": {
          "access_mode": components['schemas']["AccessMode"];
          "affected_media_count"?: number;
          "created_at": string;
          "discovery_mode": components['schemas']["DiscoveryMode"];
          "enabled": boolean;
          "id": string;
          "last_health": ("available" | "unavailable" | "unknown") | (null);
          "last_health_checked_at": (string) | (null);
          "last_scan_at": (string) | (null);
          "media_count"?: number;
          "media_type": components['schemas']["MediaType"];
          "missing_grace_checks": number;
          "name": string;
          "resolved_root_path": string;
          "scan"?: (components['schemas']["StorageRootScan"]) | (null);
          "scanning"?: boolean;
          "updated_at": string;
        };
    "StorageRootScan": {
          "current"?: number;
          "job_id": string;
          "percent"?: number;
          "stage"?: (string) | (null);
          "status": "queued" | "running";
          "total"?: number;
        };
    "StorageRootUpdate": {
          "access_mode"?: (components['schemas']["AccessMode"]) | (null);
          "discovery_mode"?: (components['schemas']["DiscoveryMode"]) | (null);
          "enabled"?: (boolean) | (null);
          "media_type"?: (components['schemas']["MediaType"]) | (null);
          "missing_grace_checks"?: (number) | (null);
          "name"?: (string) | (null);
          "path"?: (string) | (null);
        };
    "TMDBConfigurationResponse": {
          "api_key_configured"?: boolean;
          "configured": boolean;
          "enabled"?: boolean;
          "health"?: "unknown" | "healthy" | "unavailable";
          "last_checked_at"?: (string) | (null);
          "last_error"?: (string) | (null);
          "last_success_at"?: (string) | (null);
          "latency_ms"?: (number) | (null);
          "revision"?: (number) | (null);
        };
    "TMDBConfigurationUpdate": {
          "api_key"?: (string) | (null);
          "enabled"?: boolean;
          "expected_revision"?: (number) | (null);
        };
    "TMDBMovieLookupResponse": {
          "cast"?: (string)[];
          "director"?: (string) | (null);
          "imdb_id"?: (string) | (null);
          "imdb_rating"?: (number) | (null);
          "imdb_votes"?: (number) | (null);
          "original_title"?: (string) | (null);
          "overview"?: (string) | (null);
          "popularity"?: (number) | (null);
          "poster_ref"?: (string) | (null);
          "title": string;
          "tmdb_id": number;
          "vote_average"?: (number) | (null);
          "vote_count"?: (number) | (null);
          "year"?: (number) | (null);
        };
    "TMDBShowLookupResponse": {
          "cast"?: (string)[];
          "director"?: (string) | (null);
          "imdb_id"?: (string) | (null);
          "imdb_rating"?: (number) | (null);
          "imdb_votes"?: (number) | (null);
          "original_title"?: (string) | (null);
          "overview"?: (string) | (null);
          "popularity"?: (number) | (null);
          "poster_ref"?: (string) | (null);
          "title": string;
          "tmdb_id": number;
          "vote_average"?: (number) | (null);
          "vote_count"?: (number) | (null);
          "year"?: (number) | (null);
        };
    "TMDBTestRequest": {
          "api_key"?: (string) | (null);
        };
    "TMDBTestResponse": {
          "latency_ms"?: (number) | (null);
          "message"?: (string) | (null);
          "status": "healthy" | "unavailable";
        };
    "TagCreate": {
          "name": string;
        };
    "TagResponse": {
          "created_at": string;
          "id": string;
          "name": string;
        };
    "TagUpdate": {
          "name": string;
        };
    "TorrentArchiveDetail": {
          "archive_path"?: (string) | (null);
          "archive_state": components['schemas']["TorrentArchiveState"];
          "associations"?: ({ [key: string]: unknown })[];
          "completed_at"?: (string) | (null);
          "edition"?: (string) | (null);
          "first_seen_at": string;
          "id": string;
          "info_hash": string;
          "last_seen_at": string;
          "manifest"?: { [key: string]: unknown };
          "manifest_path"?: (string) | (null);
          "manifest_schema_version"?: (number) | (null);
          "media_title"?: (string) | (null);
          "media_type"?: (components['schemas']["MediaType"]) | (null);
          "observations"?: ({ [key: string]: unknown })[];
          "original_download_client"?: (string) | (null);
          "previous_reported_path"?: (string) | (null);
          "previous_resolved_path"?: (string) | (null);
          "qbit_present"?: boolean;
          "quality"?: (string) | (null);
          "release_group"?: (string) | (null);
          "release_name"?: (string) | (null);
          "tmdb_id"?: (number) | (null);
          "torrent_name": string;
          "total_size"?: (number) | (null);
          "tvdb_id"?: (number) | (null);
        };
    "TorrentArchiveHealth": {
          "archived": number;
          "message"?: (string) | (null);
          "missing_or_failed": number;
          "path": string;
          "status": "healthy" | "unavailable";
          "tracked": number;
          "writable": boolean;
        };
    "TorrentArchiveState": "not_archived" | "archived" | "failed";
    "TorrentArchiveSummary": {
          "archive_path"?: (string) | (null);
          "archive_state": components['schemas']["TorrentArchiveState"];
          "completed_at"?: (string) | (null);
          "edition"?: (string) | (null);
          "first_seen_at": string;
          "id": string;
          "info_hash": string;
          "last_seen_at": string;
          "manifest_path"?: (string) | (null);
          "manifest_schema_version"?: (number) | (null);
          "media_title"?: (string) | (null);
          "media_type"?: (components['schemas']["MediaType"]) | (null);
          "original_download_client"?: (string) | (null);
          "previous_reported_path"?: (string) | (null);
          "previous_resolved_path"?: (string) | (null);
          "qbit_present"?: boolean;
          "quality"?: (string) | (null);
          "release_group"?: (string) | (null);
          "release_name"?: (string) | (null);
          "tmdb_id"?: (number) | (null);
          "torrent_name": string;
          "total_size"?: (number) | (null);
          "tvdb_id"?: (number) | (null);
        };
    "TorrentRestoreRequest": {
          "category"?: (string) | (null);
          "download_client_id": string;
          "save_path": string;
          "tags"?: ((string)[]) | (null);
        };
    "ValidationError": {
          "ctx"?: {};
          "input"?: unknown;
          "loc": ((string) | (number))[];
          "msg": string;
          "type": string;
        };
  };
}

export interface paths {
  "/api/v1/auth/login": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["LoginRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["LoginResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/logout": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            204: {
              headers: { [name: string]: unknown };
              content?: never;
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/me": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["AdminResponse"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/password": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["PasswordChangeRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["AdminResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/security": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["SecurityResponse"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/custom-formats": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
              "scope"?: (components['schemas']["CustomFormatScope"]) | (null);
              "enabled"?: (boolean) | (null);
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_CustomFormatResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["CustomFormatCreate"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/custom-formats/export": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatExportBundle"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/custom-formats/import": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["CustomFormatImportBundle"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatImportResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/custom-formats/layout": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatLayout"];
              };
            };
          };
        };
    put: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["CustomFormatLayout"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatLayout"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/custom-formats/test": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["CustomFormatTestRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatTestResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/custom-formats/test-all": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["CustomFormatTestAllRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatTestAllResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/custom-formats/{custom_format_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "custom_format_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "custom_format_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DeleteResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "custom_format_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["CustomFormatUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/custom-formats/{custom_format_id}/export": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "custom_format_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["CustomFormatExportBundle"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/discoveries": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "state"?: string;
              "media_type"?: (string) | (null);
              "page"?: number;
              "page_size"?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_MediaDiscoveryResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/discoveries/bulk": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MediaDiscoveryBulk"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaDiscoveryBulkResult"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/discoveries/count": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaDiscoveryCount"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/discoveries/{discovery_id}/approve": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "discovery_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaDiscoveryResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/discoveries/{discovery_id}/edition": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "discovery_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MediaDiscoveryEditionUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaDiscoveryResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/discoveries/{discovery_id}/ignore": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "discovery_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaDiscoveryResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/discoveries/{discovery_id}/restore": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "discovery_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaDiscoveryResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/discoveries/{discovery_id}/search": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "discovery_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MediaDiscoverySearch"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaDiscoveryResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/discoveries/{discovery_id}/select": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "discovery_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MediaDiscoverySelection"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaDiscoveryResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/download-clients": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_DownloadClientResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["DownloadClientCreate"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DownloadClientResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/download-clients/test": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["DownloadClientTestRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DownloadClientTestResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/download-clients/{client_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "client_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DownloadClientResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "client_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["DownloadClientUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DownloadClientResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "client_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DeleteResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/download-clients/{client_id}/categories": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "client_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["DownloadClientCategoryResponse"])[];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/download-clients/{client_id}/health": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "client_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DownloadClientTestResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/download-clients/{client_id}/health/refresh": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "client_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DownloadClientTestResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/download-clients/{client_id}/poll": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "client_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/download-clients/{client_id}/test": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "client_id": string;
            };
            cookie?: never;
          };
          requestBody?: {
            content: {
              "application/json": (components['schemas']["DownloadClientSavedTestRequest"]) | (null);
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DownloadClientTestResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/downloads": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
              "client_id"?: (string) | (null);
              "scope"?: (components['schemas']["DownloadScope"]) | (null);
              "state"?: (string) | (null);
              "include_removed"?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_DownloadResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/downloads/poll": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/episodes/{episode_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "episode_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["EpisodeUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["EpisodeResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/episodes/{episode_id}/interactive-search": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "episode_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/events": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
              "entity_type"?: (string) | (null);
              "entity_id"?: (string) | (null);
              "event_type"?: (string) | (null);
              "severity"?: (string) | (null);
              "sort"?: string;
              "date_from"?: (string) | (null);
              "date_to"?: (string) | (null);
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_EventResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete: {
          parameters: {
            query?: {
              "entity_type"?: (string) | (null);
              "entity_id"?: (string) | (null);
              "event_type"?: (string) | (null);
              "severity"?: (string) | (null);
              "date_from"?: (string) | (null);
              "date_to"?: (string) | (null);
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": {
                  [key: string]: number;
                };
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/events/stream": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": unknown;
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/events/{event_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post?: never;
    delete: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "event_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": {
                  [key: string]: string;
                };
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/filesystem": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "path"?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["FilesystemListing"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/health": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": {
                  [key: string]: string;
                };
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/indexers": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_IndexerResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["IndexerCreate"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["IndexerResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/indexers/test": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["IndexerTestRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["IndexerTestResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/indexers/{indexer_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "indexer_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["IndexerResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "indexer_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["IndexerUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["IndexerResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "indexer_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DeleteResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/indexers/{indexer_id}/test": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "indexer_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["IndexerTestResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/integrations/health": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["IntegrationHealthResponse"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/integrations/plex": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["PlexConfigurationResponse"];
              };
            };
          };
        };
    put: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["PlexConfigurationUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["PlexConfigurationResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/integrations/plex/health": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["PlexHealthResponse"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/integrations/plex/health/refresh": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["PlexTestResponse"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/integrations/plex/sync": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/integrations/plex/test": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["PlexTestRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["PlexTestResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/integrations/tmdb": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["TMDBConfigurationResponse"];
              };
            };
          };
        };
    put: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["TMDBConfigurationUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["TMDBConfigurationResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/integrations/tmdb/health/refresh": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["TMDBTestResponse"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/integrations/tmdb/test": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["TMDBTestRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["TMDBTestResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/interactive-search/movies": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MovieDownloadSearchRequest"];
            };
          };
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/interactive-search/shows/seasons": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["ShowSeasonDownloadSearchRequest"];
            };
          };
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/interactive-search/shows/{tmdb_id}/preview": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "tmdb_id": number;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ShowDownloadPreviewResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/jobs": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
              "status"?: (string) | (null);
              "job_type"?: (string) | (null);
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_JobResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/jobs/{job_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "job_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/jobs/{job_id}/cancel": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "job_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/library/summary": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["LibrarySummary"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/media-files/{media_file_id}/episode-mappings": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "media_file_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["EpisodeMappingUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["EpisodeMappingResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
              "query"?: (string) | (null);
              "state"?: (string) | (null);
              "tag"?: (string) | (null);
              "sort"?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_MovieSummaryResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MovieCreate"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MovieDetailResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/bulk": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MovieBulkRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["MovieBulkResponse"]) | (components['schemas']["JobAcceptedResponse"]);
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/edition-options": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MovieEditionOptionsResponse"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/lookup": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query: {
              "query": string;
              "year"?: (number) | (null);
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["TMDBMovieLookupResponse"])[];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/{resource_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MovieDetailResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/{resource_id}/actions/recheck-plex": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/{resource_id}/duplicates/resolve": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["DuplicateResolveCommitRequest"];
            };
          };
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/{resource_id}/duplicates/resolve-preview": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["DuplicateResolvePreviewRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DuplicateResolvePreviewResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/{resource_id}/events": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
            };
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_EventResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/{resource_id}/interactive-search": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/{resource_id}/profile-settings": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaProfileSettingsResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MediaProfileSettingsUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaProfileSettingsResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/movies/{resource_id}/releases/editions": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MovieEditionOverrideRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MovieEditionOverrideResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/movies/{resource_id}/tags/{tag_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
              "tag_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["TagResponse"])[];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
              "tag_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["TagResponse"])[];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/parser/test": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["ParserTestRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": {
                  [key: string]: unknown;
                };
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/problems": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
              "reason"?: (string) | (null);
              "entity_type"?: (string) | (null);
              "severity"?: (string) | (null);
              "category"?: (string) | (null);
              "workflow"?: (string) | (null);
              "status"?: (string) | (null);
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_ProblemResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/problems/count": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "status"?: (string) | (null);
              "workflow"?: (string) | (null);
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": {
                  [key: string]: number;
                };
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/problems/recheck": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ProblemRecheckAllResponse"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/problems/summary": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ProblemSummaryResponse"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/problems/{problem_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "problem_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ProblemResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "problem_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": {
                  [key: string]: string;
                };
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/problems/{problem_id}/resolve": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "problem_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["ProblemResolveRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ProblemResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/quality-definitions": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["QualityDefinitionResponse"])[];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/quality-definitions/{quality_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "quality_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["QualityDefinitionResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/quality-profiles": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["QualityProfileResponse"])[];
              };
            };
          };
        };
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["QualityProfileCreate"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["QualityProfileResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/quality-profiles/{profile_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "profile_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["QualityProfileResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "profile_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            204: {
              headers: { [name: string]: unknown };
              content?: never;
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "profile_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["QualityProfileUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["QualityProfileResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/reconciliation/movies/{movie_id}/manual-attach": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "movie_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["ManualAttachRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ReconciliationActionResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/reconciliation/run": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: {
            content: {
              "application/json": components['schemas']["ReconciliationRunRequest"];
            };
          };
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ReconciliationRunResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/reconciliation/status": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ReconciliationStatusResponse"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/recovery/capabilities": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["RecoveryCapabilitiesResponse"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/recovery/export": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["RecoveryExportAcceptedResponse"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/recovery/exports/{job_id}/download": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "job_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": unknown;
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/remote-path-mappings": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_RemotePathMappingResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["RemotePathMappingCreate"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["RemotePathMappingResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/remote-path-mappings/{mapping_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post?: never;
    delete: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "mapping_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DeleteResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "mapping_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["RemotePathMappingUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["RemotePathMappingResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/search-jobs/{job_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
            };
            header?: never;
            path: {
              "job_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["SearchJobResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/search-results/{result_id}/download": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "result_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["SearchResultDownloadRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["SearchResultDownloadResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/seasons/{season_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "season_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["SeasonUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["SeasonResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/seasons/{season_id}/interactive-search": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "season_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/setup/complete": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["SetupCompleteRequest"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["SetupStatusResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/setup/status": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["SetupStatusResponse"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/shows": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
              "query"?: (string) | (null);
              "state"?: (string) | (null);
              "sort"?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_ShowSummaryResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["ShowCreate"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ShowDetailResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/shows/download-preferences": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ShowDownloadPreferences"];
              };
            };
          };
        };
    put: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["ShowDownloadPreferences"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ShowDownloadPreferences"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/shows/lookup": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query: {
              "query": string;
              "year"?: (number) | (null);
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["TMDBShowLookupResponse"])[];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/shows/specials-counting": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["SpecialsCountingState"];
              };
            };
          };
        };
    put: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["SpecialsCountingState"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["SpecialsCountingResult"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/shows/{resource_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ShowDetailResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["ShowUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["ShowSummaryResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/shows/{resource_id}/actions/recheck-plex": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/shows/{resource_id}/episode-orderings": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["EpisodeOrderingOption"])[];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/shows/{resource_id}/events": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
            };
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_EventResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/shows/{resource_id}/metadata/refresh": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/shows/{resource_id}/profile-settings": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaProfileSettingsResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "resource_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["MediaProfileSettingsUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["MediaProfileSettingsResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/storage-roots": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_StorageRootResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["StorageRootCreate"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["StorageRootResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/storage-roots/health": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_StorageRootResponse_"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/storage-roots/reorder": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["StorageRootReorder"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_StorageRootResponse_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/storage-roots/{root_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "root_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["StorageRootResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete: {
          parameters: {
            query?: {
              "remove_entries"?: boolean;
            };
            header?: never;
            path: {
              "root_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["DeleteResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "root_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["StorageRootUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["StorageRootResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/storage-roots/{root_id}/scan": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "root_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/tags": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": (components['schemas']["TagResponse"])[];
              };
            };
          };
        };
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["TagCreate"];
            };
          };
          responses: {
            201: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["TagResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/tags/{tag_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post?: never;
    delete: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "tag_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            204: {
              headers: { [name: string]: unknown };
              content?: never;
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    options?: never;
    head?: never;
    patch: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "tag_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["TagUpdate"];
            };
          };
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["TagResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    trace?: never;
  };
  "/api/v1/torrent-archive": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: {
              "page"?: number;
              "page_size"?: number;
              "query"?: (string) | (null);
              "archive_state"?: (string) | (null);
            };
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["Collection_TorrentArchiveSummary_"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/torrent-archive/{torrent_id}": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "torrent_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["TorrentArchiveDetail"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/torrent-archive/{torrent_id}/restore": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "torrent_id": string;
            };
            cookie?: never;
          };
          requestBody: {
            content: {
              "application/json": components['schemas']["TorrentRestoreRequest"];
            };
          };
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/torrent-archive/{torrent_id}/retry": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get?: never;
    put?: never;
    post: {
          parameters: {
            query?: never;
            header?: never;
            path: {
              "torrent_id": string;
            };
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            202: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["JobAcceptedResponse"];
              };
            };
            422: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": components['schemas']["HTTPValidationError"];
              };
            };
          };
        };
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/healthz": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": {
                  [key: string]: string;
                };
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/readyz": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
          parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
          };
          requestBody?: never;
          responses: {
            200: {
              headers: { [name: string]: unknown };
              content: {
                "application/json": {
                  [key: string]: unknown;
                };
              };
            };
          };
        };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
}

