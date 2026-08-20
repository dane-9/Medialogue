from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.tags import TagResponse


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
    quality: str | None
    release_group: str | None
    state: str
    confidence: float | None
    original_custom_format_score: int | None
    current_custom_format_score: int | None
    selection_snapshot: dict[str, Any] | None
    first_seen_at: datetime
    directories: list[MediaDirectoryResponse]
    parse_snapshot: dict[str, Any]


class MovieSummaryResponse(BaseModel):
    id: UUID
    resource_id: str
    tmdb_id: int | None
    title: str
    year: int | None
    monitored: bool
    identity_state: str
    state: str
    current_quality: str | None
    edition: str | None
    plex_state: str
    confidence: float | None
    location: str | None
    release_count: int
    problem_count: int = 0
    poster_ref: str | None = None
    tags: list[TagResponse] = Field(default_factory=list)


class MovieDetailResponse(MovieSummaryResponse):
    overview: str | None
    poster_ref: str | None
    releases: list[MovieReleaseResponse]
    recent_events: list[dict[str, Any]]
    torrent_history: list[dict[str, Any]] = Field(default_factory=list)
    incoming_downloads: list[dict[str, Any]] = Field(default_factory=list)
    problems: list[dict[str, Any]] = Field(default_factory=list)
    reconciliation: dict[str, Any] = Field(default_factory=dict)
    storage_root: str | None = None
    root_health: str | None = None
    root_affected_count: int = 0
    last_observed_at: datetime | None = None


class TMDBMovieLookupResponse(BaseModel):
    tmdb_id: int
    title: str
    original_title: str | None = None
    year: int | None = None
    overview: str | None = None
    poster_ref: str | None = None
