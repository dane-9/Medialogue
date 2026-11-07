from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from app.models.domain import TorrentArchiveState

from .common import ORMModel


class DownloadScope(str, Enum):
    MOVIES = "movies"
    SHOWS = "shows"


class DownloadClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    url: AnyHttpUrl
    username: str = ""
    password: str = Field(min_length=1, max_length=4096)
    scope: DownloadScope
    category: str | None = Field(default=None, max_length=256)
    tags: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True
    poll_interval_seconds: int = Field(default=30, ge=5, le=3600)
    recent_priority: Literal["first", "last"] = "last"
    older_priority: Literal["first", "last"] = "last"
    sequential_order: bool = False
    first_last_first: bool = False
    content_layout: Literal["default", "original", "subfolder"] = "default"
    completed_download_handling: bool = True

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))


class DownloadClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    url: AnyHttpUrl | None = None
    username: str | None = None
    password: str | None = Field(default=None, max_length=4096)
    scope: DownloadScope | None = None
    category: str | None = Field(default=None, max_length=256)
    tags: list[str] | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    poll_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    recent_priority: Literal["first", "last"] | None = None
    older_priority: Literal["first", "last"] | None = None
    sequential_order: bool | None = None
    first_last_first: bool | None = None
    content_layout: Literal["default", "original", "subfolder"] | None = None
    completed_download_handling: bool | None = None
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))


class DownloadClientResponse(ORMModel):
    id: UUID
    name: str
    url: str
    username: str | None
    password_configured: bool
    scope: DownloadScope
    category: str | None
    tags: list[str]
    enabled: bool
    health: Literal["unknown", "healthy", "unavailable"] | None
    last_health_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    latency_ms: int | None = None
    last_error: str | None = None
    revision: int
    poll_interval_seconds: int
    recent_priority: Literal["first", "last"]
    older_priority: Literal["first", "last"]
    sequential_order: bool
    first_last_first: bool
    content_layout: Literal["default", "original", "subfolder"]
    completed_download_handling: bool
    created_at: datetime
    updated_at: datetime


class DownloadClientTestResponse(BaseModel):
    status: Literal["unknown", "healthy", "unavailable"]
    version: str | None = None
    latency_ms: int | None = None
    message: str | None = None


class DownloadClientCategoryResponse(BaseModel):
    name: str
    save_path: str
    resolved_save_path: str
    is_default: bool = False


class DownloadClientTestRequest(BaseModel):
    url: AnyHttpUrl
    username: str = ""
    password: str = Field(min_length=1, max_length=4096)


class DownloadClientSavedTestRequest(BaseModel):
    """Optional unsaved edits to test against an existing stored client.

    Omitting/blanking password intentionally preserves the write-only stored
    secret, so users can test URL/username edits without retyping it.
    """

    url: AnyHttpUrl | None = None
    username: str | None = None
    password: str | None = Field(default=None, max_length=4096)


class DownloadActionRequest(BaseModel):
    """Apply one qBittorrent action to a set of live download rows."""

    download_ids: list[UUID] = Field(min_length=1, max_length=500)
    action: Literal[
        "start", "stop", "recheck", "reannounce",
        "queue_top", "queue_up", "queue_down", "queue_bottom",
        "download_limit", "upload_limit", "share_ratio", "remove",
    ]
    #: Bytes/s for the rate limits, a ratio for share_ratio. Ignored otherwise.
    value: float | None = None


class DownloadFacetsResponse(BaseModel):
    """Rail counts and the category/tracker lists for one client."""

    counts: dict[str, int]
    categories: list[str]
    tags: list[str]
    trackers: list[str]


class DownloadDeletePlanRequest(BaseModel):
    download_ids: list[UUID] = Field(min_length=1, max_length=500)


class DownloadDeleteRequest(DownloadDeletePlanRequest):
    #: "replace" lists the title in Replace Media as Missing; "forget" removes
    #: it from Medialogue.
    library: Literal["replace", "forget"]


class DownloadDeleteTorrent(BaseModel):
    download_id: UUID
    name: str
    client_name: str
    #: False for a cross-seed of the deleted files that was not selected.
    selected: bool


class DownloadDeleteTitle(BaseModel):
    kind: Literal["movie", "show"]
    title: str
    year: int | None = None
    season_numbers: list[int] = Field(default_factory=list)


class DownloadDeletePlanResponse(BaseModel):
    folders: list[str]
    torrents: list[DownloadDeleteTorrent]
    titles: list[DownloadDeleteTitle]
    #: Why the deletion cannot go ahead; empty when it can.
    blocked: list[str]


class DownloadActionFailure(BaseModel):
    client_id: UUID
    client_name: str
    count: int
    error: str


class DownloadActionResponse(BaseModel):
    applied: int
    clients: int
    #: Torrents on a client that is disabled or no longer configured.
    skipped: int = 0
    #: Clients whose part of the batch failed; the rest was still applied.
    failures: list[DownloadActionFailure] = Field(default_factory=list)


class DownloadResponse(ORMModel):
    id: UUID
    torrent_id: UUID
    payload_group_id: str
    client_id: UUID
    client_name: str
    scope: DownloadScope
    info_hash: str
    name: str
    total_size: int | None
    reported_save_path: str | None
    resolved_save_path: str | None
    state: str | None
    progress: float | None
    category: str | None
    tags: list[str]
    is_present: bool
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None
    completed_at: datetime | None = None
    movie_id: UUID | None = None
    show_id: UUID | None = None
    release_id: UUID | None = None
    media_title: str | None = None
    media_poster_ref: str | None = None
    media_root_name: str | None = None
    media_year: int | None = None
    quality: str | None = None
    edition: str | None = None
    media_state: Literal["present", "missing"] | None = None
    reconciliation_state: Literal["incoming", "attached", "historical", "conflict"] | None = None
    reconciliation_detail: str | None = None
    incoming: bool = False
    incoming_kind: Literal["replacement", "release"] | None = None
    activity: Literal["downloading", "checking", "metadata", "moving", "queued", "stalled", "seeding", "paused", "error", "missing"]
    #: True when the values below are not current: the client is disabled or
    #: has not reported this torrent for several poll intervals.
    stale: bool = False
    stale_reason: str | None = None
    download_speed: int | None = None
    upload_speed: int | None = None
    eta_seconds: int | None = None
    amount_left: int | None = None
    ratio: float | None = None
    seeds: int | None = None
    seeds_total: int | None = None
    leeches: int | None = None
    leeches_total: int | None = None
    availability: float | None = None
    archive_state: TorrentArchiveState
    tracker_state: Literal["healthy", "degraded", "unavailable", "unregistered", "unknown"] = "unknown"
    tracker_host: str | None = None
