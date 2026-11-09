from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.domain import MediaType


UpgradeExecutionState = Literal[
    "submitted",
    "linking",
    "downloading",
    "validating",
    "handoff_attention",
    "cleanup_required",
    "completed",
    "failed",
]


class UpgradeCurrentRelease(BaseModel):
    release_id: str
    release_name: str
    quality: str | None = None
    edition: str | None = None
    custom_format_score: int = 0


class UpgradeMatchedFormat(BaseModel):
    custom_format_id: str
    custom_format_name: str
    contribution: int
    # Audio, Dynamic Range, ...: formats in one group compete for the same
    # part of a release. None when the format describes no single part.
    group: str | None = None


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
    current_edition: str | None = None
    movie_has_multiple_editions: bool = False
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
    candidate_edition: str | None = None
    result_count: int = 1
    custom_format_score: int
    score_delta: int
    size: int | None = None
    #: Size of the releases being replaced, from their torrents; None if unknown.
    current_size: int | None = None
    #: Where the current release came from: the indexer when Medialogue sent
    #: it, otherwise the tracker host its torrent announces to.
    current_indexer: str | None = None
    current_indexer_color: str | None = None
    current_tracker: str | None = None
    seeders: int | None = None
    leechers: int | None = None
    published_at: datetime | None = None
    upgrade_types: list[Literal["quality", "custom_format", "season_pack", "repack", "proper", "revision", "replacement"]]
    added_custom_formats: list[dict[str, Any]]
    #: Every Custom Format that matched, per side, with what it contributed.
    #: Read from the comparison snapshot so the row can explain its score
    #: without a second round trip.
    #: Every release the candidate would replace. One for a movie; one per
    #: episode when a season pack replaces loose episode torrents, where naming
    #: only the best of them hides most of what is being changed.
    current_releases: list[UpgradeCurrentRelease] = Field(default_factory=list)
    #: Episodes the season has on disk, and how many the season holds in total.
    current_episode_count: int | None = None
    season_episode_count: int | None = None
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
    # Live from the download client, while the upgrade downloads.
    download_speed: int | None = None
    download_eta_seconds: int | None = None
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


class UpgradeSearchResultResponse(BaseModel):
    id: UUID
    indexer_id: UUID | None = None
    indexer_name: str
    indexer_color: str | None = None
    title: str
    quality: str | None = None
    edition: str | None = None
    release_group: str | None = None
    custom_format_score: int | None = None
    size: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    published_at: datetime | None = None
    eligible: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    upgrade_types: list[str] = Field(default_factory=list)
    matched_format_names: list[str] = Field(default_factory=list)
    parser: dict[str, Any] = Field(default_factory=dict)
    recommended: bool = False
    selected: bool = False
    #: These are the very files already on disk, so nothing need be downloaded.
    same_file: bool = False
    #: Looked the same, but qBittorrent's check of the files on disk fell short.
    link_failed: bool = False


class UpgradeSearchResultsResponse(BaseModel):
    candidate_id: UUID
    searched_at: datetime
    results: list[UpgradeSearchResultResponse]


class NotAnUpgradeResponse(BaseModel):
    #: The release now offered instead; None when no other upgrade was found.
    next_title: str | None = None


class UpgradeSearchResultSelectRequest(BaseModel):
    result_id: UUID


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
    delete_old_media: bool


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


class MediaHistoryItem(BaseModel):
    """One finished upgrade or replacement."""

    id: str
    finished_at: datetime
    media_type: str | None = None
    title: str
    year: int | None = None
    season_number: int | None = None
    old_release: str | None = None
    old_quality: str | None = None
    new_release: str | None = None
    new_quality: str | None = None
    #: How it ended, in words: "Kept everything", "Old files deleted", ...
    outcome: str
