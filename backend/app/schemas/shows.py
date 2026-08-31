from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.domain import IdentityState, MediaRole, PresenceState, ReleaseScope, Severity
from app.schemas.torrent_status import MediaTorrentStatusResponse

ShowCollectionState = Literal["Present", "Missing", "Conflict"]
ShowPlexState = Literal[
    "matched",
    "pending",
    "not_found",
    "conflict",
    "multiple_versions",
    "unavailable",
    "unknown",
]
StorageRootHealth = Literal["available", "unavailable", "unknown"]


class ShowCreate(BaseModel):
    tmdb_id: int
    monitored: bool = True


class ShowUpdate(BaseModel):
    monitored: bool | None = None
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
    director: str | None = None
    cast: list[str] = Field(default_factory=list)
    imdb_id: str | None = None
    imdb_rating: float | None = None
    imdb_votes: int | None = None
    popularity: float | None = None
    vote_count: int | None = None
    vote_average: float | None = None


class EpisodeMediaResponse(BaseModel):
    media_file_id: UUID
    show_release_id: UUID | None = None
    path: str
    exists: bool
    quality: str | None = None
    release_group: str | None = None
    release_name: str | None = None
    release_scope: ReleaseScope | None = None
    mapped_episode_numbers: list[int] = Field(default_factory=list)
    manual_mapping: bool = False
    torrent_status: MediaTorrentStatusResponse = Field(default_factory=MediaTorrentStatusResponse)


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
    presence_state: PresenceState
    revision: int
    quality: str | None = None
    plex_state: ShowPlexState = "unknown"
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


class ShowEventResponse(BaseModel):
    id: UUID
    type: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ShowProblemResponse(BaseModel):
    id: UUID
    reason: str
    message: str
    title: str
    description: str
    severity: Severity
    details: dict[str, Any] = Field(default_factory=dict)


class ShowStorageRootResponse(BaseModel):
    id: UUID
    name: str
    path: str
    health: StorageRootHealth | None = None


class ShowSummaryResponse(BaseModel):
    id: UUID
    resource_id: str
    tmdb_id: int | None
    tvdb_id: int | None = None
    tmdb_episode_group_id: str | None = None
    title: str
    year: int | None
    monitored: bool
    identity_state: IdentityState
    state: ShowCollectionState
    plex_state: ShowPlexState
    season_count: int
    episode_count: int
    episodes_present: int
    episodes_missing: int
    problem_count: int = 0
    poster_ref: str | None = None
    revision: int
    last_observed_at: datetime | None = None
    torrent_status: MediaTorrentStatusResponse = Field(default_factory=MediaTorrentStatusResponse)


class ShowDetailResponse(ShowSummaryResponse):
    overview: str | None = None
    seasons: list[SeasonResponse] = Field(default_factory=list)
    recent_events: list[ShowEventResponse] = Field(default_factory=list)
    problems: list[ShowProblemResponse] = Field(default_factory=list)
    storage_roots: list[ShowStorageRootResponse] = Field(default_factory=list)
    last_observed_at: datetime | None = None
