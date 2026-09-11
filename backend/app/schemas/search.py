from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.domain import JobStatus, MediaType

SearchTargetEntityType = Literal["tmdb_movie", "tmdb_show_season", "movie", "episode", "season"]
SearchIndexerState = Literal["queued", "completed", "failed"]


class MovieDownloadSearchRequest(BaseModel):
    tmdb_id: int = Field(gt=0)
    quality_profile_id: UUID


class ShowSeasonDownloadSearchRequest(BaseModel):
    tmdb_id: int = Field(gt=0)
    quality_profile_id: UUID
    season_number: int = Field(ge=0)


class ShowDownloadSeasonPreview(BaseModel):
    season_number: int
    title: str | None = None
    episode_count: int = 0
    air_date: str | None = None
    poster_ref: str | None = None


class ShowDownloadPreviewResponse(BaseModel):
    tmdb_id: int
    title: str
    year: int | None = None
    overview: str | None = None
    poster_ref: str | None = None
    seasons: list[ShowDownloadSeasonPreview]


class SearchTargetResponse(BaseModel):
    media_type: MediaType
    entity_type: SearchTargetEntityType
    entity_id: UUID | None = None
    profile_entity_id: UUID | None = None
    quality_profile_id: UUID | None = None
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    overview: str | None = None
    poster_ref: str | None = None
    season: int | None = None
    episode: int | None = None


class SearchProgressResponse(BaseModel):
    completed_indexers: int = 0
    total_indexers: int = 0
    percent: float = 0


class SearchResultResponse(BaseModel):
    id: UUID
    job_id: UUID
    indexer_id: UUID | None
    indexer_name: str
    indexer_color: str | None = None
    media_type: MediaType
    target_entity_type: SearchTargetEntityType
    title: str
    size: int | None
    seeders: int | None
    leechers: int | None
    published_at: datetime | None
    quality: str | None
    quality_allowed: bool
    quality_preference: int
    edition: str | None
    release_group: str | None
    custom_format_score: int | None
    quality_profile_id: UUID | None
    quality_profile_name: str | None
    minimum_quality: str | None
    minimum_quality_met: bool | None
    minimum_custom_format_score: int | None
    minimum_custom_format_score_met: bool | None
    custom_format_snapshot: dict[str, Any]
    parser: dict[str, Any]
    warnings: list[str]
    blocklisted: bool = False
    blocklist_id: UUID | None = None
    blocklist_reason: str | None = None
    selected_at: datetime | None
    selected_download_client_id: UUID | None
    created_at: datetime
    expires_at: datetime


class SearchIndexerStatus(BaseModel):
    id: UUID
    name: str
    status: SearchIndexerState
    results: int = 0
    elapsed_ms: int | None = None
    error: str | None = None


class SearchJobResponse(BaseModel):
    id: UUID
    status: JobStatus
    target: SearchTargetResponse
    progress: SearchProgressResponse
    indexers: list[SearchIndexerStatus]
    results: list[SearchResultResponse]
    result_total: int
    error: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SearchResultDownloadRequest(BaseModel):
    download_client_id: UUID
    category: str | None = Field(default=None, max_length=256)
    show_folder_name: str | None = Field(default=None, min_length=1, max_length=240)
    include_year_in_show_folder: bool = False


class SearchResultDownloadResponse(BaseModel):
    search_result_id: UUID
    download_client_id: UUID
    client_name: str
    status: Literal["submitted", "already_submitted"]
    selected_at: datetime
    movie_id: UUID | None = None
    show_id: UUID | None = None
    season_id: UUID | None = None
    category: str | None = None
    save_path: str | None = None
