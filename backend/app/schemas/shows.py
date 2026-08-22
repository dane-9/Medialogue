from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ShowCreate(BaseModel):
    tmdb_id: int
    monitored: bool = True


class ShowUpdate(BaseModel):
    monitored: bool | None = None
    # "" clears the selection and returns the show to TMDB's default structure.
    tmdb_episode_group_id: str | None = None
    expected_revision: int | None = None


class SeasonUpdate(BaseModel):
    monitored: bool | None = None
    counted: bool | None = None
    expected_revision: int | None = None


class EpisodeUpdate(BaseModel):
    monitored: bool | None = None
    expected_revision: int | None = None


class TMDBShowLookupResponse(BaseModel):
    tmdb_id: int
    title: str
    original_title: str | None = None
    year: int | None = None
    overview: str | None = None
    poster_ref: str | None = None


class EpisodeMediaResponse(BaseModel):
    media_file_id: UUID
    show_release_id: UUID | None = None
    path: str
    exists: bool
    quality: str | None = None
    release_group: str | None = None
    release_name: str | None = None
    release_scope: str | None = None
    mapped_episode_numbers: list[int] = Field(default_factory=list)
    manual_mapping: bool = False


class EpisodeMappingUpdate(BaseModel):
    episode_ids: list[UUID] = Field(min_length=1)


class EpisodeMappingResponse(BaseModel):
    media_file_id: UUID
    show_release_id: UUID
    episode_ids: list[UUID]
    episode_numbers: list[int]
    manual_override: bool


class EpisodeResponse(BaseModel):
    id: UUID
    season_number: int
    episode_number: int
    title: str | None = None
    air_date: date | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    monitored: bool
    presence_state: str
    revision: int
    quality: str | None = None
    plex_state: str = "unknown"
    media: list[EpisodeMediaResponse] = Field(default_factory=list)


class SeasonResponse(BaseModel):
    id: UUID
    season_number: int
    title: str | None = None
    monitored: bool
    counted: bool = True
    revision: int
    episode_count: int
    present_count: int
    missing_count: int
    episodes: list[EpisodeResponse] = Field(default_factory=list)


class ShowSummaryResponse(BaseModel):
    id: UUID
    resource_id: str
    tmdb_id: int | None
    tvdb_id: int | None = None
    tmdb_episode_group_id: str | None = None
    title: str
    year: int | None
    monitored: bool
    identity_state: str
    state: str
    plex_state: str
    season_count: int
    episode_count: int
    episodes_present: int
    episodes_missing: int
    problem_count: int = 0
    poster_ref: str | None = None
    revision: int


class ShowDetailResponse(ShowSummaryResponse):
    overview: str | None = None
    seasons: list[SeasonResponse] = Field(default_factory=list)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    problems: list[dict[str, Any]] = Field(default_factory=list)
    storage_roots: list[dict[str, Any]] = Field(default_factory=list)
    last_observed_at: datetime | None = None
