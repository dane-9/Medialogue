from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .common import ORMModel


class MediaType(str, Enum):
    MOVIES = "movies"
    SHOWS = "shows"


class AccessMode(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class DiscoveryMode(str, Enum):
    AUTOMATIC = "automatic"
    REVIEW = "review"


class StorageRootCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=4096)
    media_type: MediaType
    access_mode: AccessMode = AccessMode.READ_ONLY
    discovery_mode: DiscoveryMode = DiscoveryMode.AUTOMATIC
    enabled: bool = True
    missing_grace_checks: int = Field(default=2, ge=1, le=100)

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        from pathlib import Path

        if not Path(value).is_absolute():
            raise ValueError("media folder path must be absolute")
        return value


class StorageRootUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    media_type: MediaType | None = None
    access_mode: AccessMode | None = None
    discovery_mode: DiscoveryMode | None = None
    enabled: bool | None = None
    missing_grace_checks: int | None = Field(default=None, ge=1, le=100)

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str | None) -> str | None:
        from pathlib import Path

        if value is not None and not Path(value).is_absolute():
            raise ValueError("media folder path must be absolute")
        return value


class StorageRootReorder(BaseModel):
    """Ids in the order the list should be shown, top first."""

    ids: list[UUID] = Field(min_length=1)


class LibrarySummary(BaseModel):
    """Counts the Media Folders page needs to explain the library's state."""

    movies: int = 0
    shows: int = 0
    unlinked_directories: int = 0


class FilesystemEntry(BaseModel):
    name: str
    path: str


class FilesystemListing(BaseModel):
    """One directory's subfolders, for picking a root by browsing."""

    current_path: str
    parent: str | None = None
    directories: list[FilesystemEntry] = Field(default_factory=list)
    problem: str | None = None


class StorageRootScan(BaseModel):
    """The scan running against a root right now.

    A boolean was enough to grey out a row, but not to say what the scan is
    doing, so the row could show "Scanning" for minutes with nothing behind it.
    """

    job_id: UUID
    status: Literal["queued", "running"]
    stage: str | None = None
    current: int = 0
    total: int = 0
    percent: float = 0


class StorageRootResponse(ORMModel):
    id: UUID
    name: str
    resolved_root_path: str
    media_type: MediaType
    access_mode: AccessMode
    discovery_mode: DiscoveryMode
    enabled: bool
    missing_grace_checks: int
    last_health: Literal["available", "unavailable", "unknown"] | None
    last_health_checked_at: datetime | None
    last_scan_at: datetime | None
    affected_media_count: int = 0
    #: Media in this root whose directory no longer exists on disk. A root can
    #: be perfectly reachable and still have missing media, so this is separate
    #: from last_health: without it a partially missing folder is
    #: indistinguishable from a healthy one in the folder list.
    missing_media_count: int = 0
    #: A scan is queued or running for this root right now. Without it a scan in
    #: progress is indistinguishable from one that never happened, because
    #: last_scan_at is only written when a scan succeeds.
    scanning: bool = False
    scan: StorageRootScan | None = None
    media_count: int = 0
    #: Live watching of this folder: "watching" (changes show up within a
    #: minute), "unavailable" (events are not delivered here; scheduled scans
    #: only), or None (not watched: never scanned, switched off, or disabled).
    watch_state: Literal["watching", "unavailable"] | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("last_scan_at", "last_health_checked_at", mode="after")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite drops timezone metadata even though these columns contain UTC.
        # Returning an explicit offset keeps browser-relative ages correct in
        # every local timezone.
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


RemotePathIntegrationType = Literal['qbittorrent', 'plex', 'prowlarr', 'tmdb', 'tvdb']


class RemotePathMappingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    integration_type: RemotePathIntegrationType
    integration_id: UUID | None = None
    remote_prefix: str = Field(min_length=1, max_length=4096)
    local_prefix: str = Field(min_length=1, max_length=4096)
    storage_root_id: UUID | None = None
    enabled: bool = True


class RemotePathMappingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    integration_type: RemotePathIntegrationType | None = None
    integration_id: UUID | None = None
    remote_prefix: str | None = Field(default=None, min_length=1, max_length=4096)
    local_prefix: str | None = Field(default=None, min_length=1, max_length=4096)
    storage_root_id: UUID | None = None
    enabled: bool | None = None


class RemotePathMappingResponse(ORMModel):
    id: UUID
    name: str
    integration_type: RemotePathIntegrationType
    integration_id: UUID | None
    remote_prefix: str
    local_prefix: str
    storage_root_id: UUID | None
    enabled: bool
