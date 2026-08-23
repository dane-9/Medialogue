from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MovieAcquisitionSearchRequest(BaseModel):
    tmdb_id: int = Field(gt=0)
    quality_profile_id: UUID


class ShowSeasonAcquisitionSearchRequest(BaseModel):
    tmdb_id: int = Field(gt=0)
    quality_profile_id: UUID
    season_number: int = Field(ge=0)


class ShowAcquisitionSeasonPreview(BaseModel):
    season_number: int
    title: str | None = None
    episode_count: int = 0
    air_date: str | None = None
    poster_ref: str | None = None


class ShowAcquisitionPreviewResponse(BaseModel):
    tmdb_id: int
    title: str
    year: int | None = None
    overview: str | None = None
    poster_ref: str | None = None
    seasons: list[ShowAcquisitionSeasonPreview]



class SearchResultResponse(BaseModel):
    id: UUID
    job_id: UUID
    indexer_id: UUID | None
    indexer_name: str
    media_type: str
    target_entity_type: str
    title: str
    size: int | None
    seeders: int | None
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
    custom_format_snapshot: dict[str, Any]
    parser: dict[str, Any]
    warnings: list[str]
    selected_at: datetime | None
    selected_download_client_id: UUID | None
    created_at: datetime
    expires_at: datetime


class SearchIndexerStatus(BaseModel):
    id: UUID
    name: str
    status: str
    results: int = 0
    elapsed_ms: int | None = None
    error: str | None = None


class SearchJobResponse(BaseModel):
    id: UUID
    status: str
    target: dict[str, Any]
    progress: dict[str, Any]
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


class SearchResultDownloadResponse(BaseModel):
    search_result_id: UUID
    download_client_id: UUID
    client_name: str
    status: str
    selected_at: datetime
    movie_id: UUID | None = None
    show_id: UUID | None = None
    season_id: UUID | None = None
    category: str | None = None
