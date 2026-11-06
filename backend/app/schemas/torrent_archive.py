from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.domain import MediaType, TorrentArchiveState


class TorrentArchiveSummary(BaseModel):
    id: UUID
    info_hash: str
    torrent_name: str
    total_size: int | None = None
    archive_state: TorrentArchiveState
    archive_path: str | None = None
    manifest_path: str | None = None
    manifest_schema_version: int | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    completed_at: datetime | None = None
    media_type: MediaType | None = None
    media_title: str | None = None
    media_year: int | None = None
    poster_ref: str | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    release_name: str | None = None
    quality: str | None = None
    edition: str | None = None
    release_group: str | None = None
    original_download_client: str | None = None
    previous_reported_path: str | None = None
    previous_resolved_path: str | None = None
    qbit_present: bool = False
    archive_verified: bool = False
    archive_error: str | None = None
    captured_at: str | None = None
    #: When the .torrent was first saved to the archive.
    archived_at: str | None = None
    source_state: str = "unknown"
    source_checked_at: str | None = None
    recovery_clients: list[str] = Field(default_factory=list)
    included_in_remote_backup: bool = False
    remote_backup_at: str | None = None


class TorrentArchiveDetail(TorrentArchiveSummary):
    manifest: dict[str, Any] = Field(default_factory=dict)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    associations: list[dict[str, Any]] = Field(default_factory=list)


class TorrentRestoreRequest(BaseModel):
    download_client_id: UUID
    save_path: str = Field(min_length=1)
    category: str | None = None
    tags: list[str] | None = None
