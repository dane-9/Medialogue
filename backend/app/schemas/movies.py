from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.domain import AssociationType, IdentityState, ReleaseState, Severity, TorrentArchiveState
from app.schemas.tags import TagResponse

MovieCollectionState = Literal["Present", "Missing", "Conflict", "Duplicate"]
MoviePlexState = Literal[
    "matched",
    "pending",
    "not_found",
    "conflict",
    "multiple_versions",
    "unavailable",
    "unknown",
]
IncomingKind = Literal["replacement", "release"]
StorageRootHealth = Literal["available", "unavailable", "unknown"]


class MediaDirectoryResponse(BaseModel):
    id: UUID
    resolved_path: str
    exists: bool
    missing_since: datetime | None
    files: list[str]


class MovieReleaseResponse(BaseModel):
    id: UUID
    raw_release_name: str
    edition: str | None
    parsed_edition: str | None = None
    edition_override_active: bool = False
    manual_edition_override: str | None = None
    quality: str | None
    release_group: str | None
    state: ReleaseState
    confidence: float | None
    original_custom_format_score: int | None
    current_custom_format_score: int | None
    selection_snapshot: dict[str, Any] | None
    first_seen_at: datetime
    directories: list[MediaDirectoryResponse]
    parse_snapshot: dict[str, Any]


class MovieIncomingDownloadResponse(BaseModel):
    id: UUID
    torrent_id: UUID
    release_id: UUID
    name: str
    client_name: str
    progress: float
    state: str
    torrent_state: str
    quality: str | None = None
    edition: str | None = None
    resolved_save_path: str | None = None
    incoming_kind: IncomingKind


class MovieEventResponse(BaseModel):
    id: UUID
    type: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MovieProblemResponse(BaseModel):
    id: UUID
    reason: str
    message: str
    title: str
    description: str
    severity: Severity
    details: dict[str, Any] = Field(default_factory=dict)


class MovieReconciliationResponse(BaseModel):
    state: MovieCollectionState
    incoming_count: int
    missing_count: int
    degraded_count: int
    replaced_count: int
    duplicate_count: int
    qbit_media_disagreement: bool
    plex_blocked: bool
    root_offline: bool
    root_affected_count: int


class MovieTorrentHistoryResponse(BaseModel):
    id: UUID
    torrent_name: str
    release_id: UUID
    release_name: str
    info_hash: str
    tracker: str | None = None
    archive_state: TorrentArchiveState
    archive_path: str | None = None
    manifest_path: str | None = None
    manifest_schema_version: int | None = None
    association_type: AssociationType
    download_client_name: str | None = None
    qbit_present: bool
    first_seen_at: datetime
    completed_at: datetime | None = None


class MovieSummaryResponse(BaseModel):
    id: UUID
    resource_id: str
    tmdb_id: int | None
    title: str
    year: int | None
    monitored: bool
    identity_state: IdentityState
    state: MovieCollectionState
    current_quality: str | None
    edition: str | None
    plex_state: MoviePlexState
    confidence: float | None
    location: str | None
    release_count: int
    problem_count: int = 0
    poster_ref: str | None = None
    tags: list[TagResponse] = Field(default_factory=list)
    incoming: bool = False
    incoming_kind: IncomingKind | None = None
    last_observed_at: datetime | None = None


class MovieDetailResponse(MovieSummaryResponse):
    overview: str | None
    poster_ref: str | None
    releases: list[MovieReleaseResponse]
    recent_events: list[MovieEventResponse]
    torrent_history: list[MovieTorrentHistoryResponse] = Field(default_factory=list)
    incoming_downloads: list[MovieIncomingDownloadResponse] = Field(default_factory=list)
    problems: list[MovieProblemResponse] = Field(default_factory=list)
    reconciliation: MovieReconciliationResponse
    storage_root: str | None = None
    root_health: StorageRootHealth | None = None
    root_affected_count: int = 0
    last_observed_at: datetime | None = None


class MovieCreate(BaseModel):
    tmdb_id: int
    monitored: bool = True


class TMDBMovieLookupResponse(BaseModel):
    tmdb_id: int
    title: str
    original_title: str | None = None
    year: int | None = None
    overview: str | None = None
    poster_ref: str | None = None
    director: str | None = None
    cast: list[str] = Field(default_factory=list)
    imdb_id: str | None = None
    imdb_rating: float | None = None
    imdb_votes: int | None = None
    popularity: float | None = None
    vote_count: int | None = None
    vote_average: float | None = None
