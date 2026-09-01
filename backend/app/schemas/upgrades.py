from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.domain import MediaType


UpgradeExecutionState = Literal[
    "submitted",
    "downloading",
    "validating",
    "handoff_attention",
    "cleanup_required",
    "completed",
    "failed",
]


class UpgradeMatchedFormat(BaseModel):
    custom_format_id: str
    custom_format_name: str
    contribution: int


class UpgradeCandidateResponse(BaseModel):
    id: UUID
    media_type: MediaType
    movie_id: UUID | None = None
    season_id: UUID | None = None
    media_title: str
    media_year: int | None = None
    #: TMDB id of the movie or show, for the identity card on the row.
    tmdb_id: int | None = None
    season_number: int | None = None
    current_paths: list[str] = Field(default_factory=list)
    #: Media folder this candidate's current release lives in, so the page can
    #: group by folder the way New Media does.
    root_name: str | None = None
    #: Artwork for the title, so a row is recognisable before it is read.
    poster_ref: str | None = None
    #: The folder holding the current release: the movie's own folder, or the
    #: season folder inside the show container. Null when a season's files are
    #: split across folders, where no single name would be true.
    release_folder: str | None = None
    current_release_ids: list[UUID]
    current_release_name: str
    current_quality: str | None = None
    current_custom_format_score: int
    #: Null means the title inherits the default Quality Profile.
    quality_profile_id: UUID | None = None
    quality_profile_name: str | None = None
    quality_profile_inherited: bool = True
    indexer_id: UUID | None = None
    indexer_name: str
    #: The indexer's configured badge colour, so the row can tint it the way
    #: search results do.
    indexer_color: str | None = None
    title: str
    quality: str | None = None
    custom_format_score: int
    score_delta: int
    size: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    published_at: datetime | None = None
    upgrade_types: list[Literal["quality", "custom_format", "season_pack", "repack", "proper", "revision"]]
    added_custom_formats: list[dict[str, Any]]
    #: Every Custom Format that matched, per side, with what it contributed.
    #: Read from the comparison snapshot so the row can explain its score
    #: without a second round trip.
    current_matched_formats: list[UpgradeMatchedFormat] = Field(default_factory=list)
    candidate_matched_formats: list[UpgradeMatchedFormat] = Field(default_factory=list)
    execution_eligible: bool
    execution_block_reason: str | None = None
    comparison_snapshot: dict[str, Any]
    execution_state: UpgradeExecutionState | None = None
    selected_download_client_id: UUID | None = None
    selected_download_client_name: str | None = None
    selected_category: str | None = None
    torrent_id: UUID | None = None
    download_progress: float | None = None
    download_state: str | None = None
    execution_error: str | None = None
    submitted_at: datetime | None = None
    validated_at: datetime | None = None
    old_torrents_removed_at: datetime | None = None
    cleanup_completed_at: datetime | None = None
    last_searched_at: datetime
    created_at: datetime
    updated_at: datetime


class UpgradeCandidateCount(BaseModel):
    count: int
    #: Media folders excluded from upgrade searches because they are read-only.
    #: Reported once here rather than per row, since it is one folder setting.
    read_only_roots: list[str] = Field(default_factory=list)


class UpgradeSearchResponse(BaseModel):
    job_id: UUID


class UpgradeQualityProfileUpdate(BaseModel):
    quality_profile_id: UUID | None = None


class UpgradeQualityProfileBulkUpdate(UpgradeQualityProfileUpdate):
    candidate_ids: list[UUID] = Field(min_length=1, max_length=1000)


class UpgradeQualityProfileUpdateResponse(BaseModel):
    job_id: UUID
    candidate_ids: list[UUID]
    quality_profile_id: UUID | None = None
    quality_profile_name: str | None = None
    updated_entities: int


class UpgradeExecuteRequest(BaseModel):
    download_client_id: UUID
    category: str | None = Field(default=None, max_length=256)


class UpgradeCleanupCommitRequest(BaseModel):
    confirmation_token: str = Field(min_length=16, max_length=16384)


class UpgradeCleanupTarget(BaseModel):
    path: str
    kind: Literal["directory", "file"]
    inventory: list[dict[str, Any]] = Field(default_factory=list)


class UpgradeCleanupPreviewResponse(BaseModel):
    candidate_id: UUID
    media_type: MediaType
    eligible: bool
    blocked: list[str]
    targets: list[UpgradeCleanupTarget]
    total_bytes: int
    confirmation_token: str
    expires_at: datetime
    warnings: list[str]


class UpgradeCleanupResult(BaseModel):
    candidate_id: UUID
    state: Literal["completed"]
    deleted_paths: list[str]
