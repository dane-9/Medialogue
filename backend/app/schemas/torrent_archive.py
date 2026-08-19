from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TorrentArchiveSummary(BaseModel):
    id: UUID
    info_hash: str
    torrent_name: str
    total_size: int | None = None
    archive_state: str
    archive_path: str | None = None
    manifest_path: str | None = None
    manifest_schema_version: int | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    completed_at: datetime | None = None
    media_type: str | None = None
    media_title: str | None = None
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


class TorrentArchiveDetail(TorrentArchiveSummary):
    manifest: dict[str, Any] = Field(default_factory=dict)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    associations: list[dict[str, Any]] = Field(default_factory=list)


class TorrentRestoreRequest(BaseModel):
    download_client_id: UUID
    save_path: str = Field(min_length=1)
    category: str | None = None
    tags: list[str] | None = None


class TorrentRestoreResponse(BaseModel):
    torrent_id: UUID
    download_client_id: UUID
    client_name: str
    info_hash: str
    save_path: str
    resolved_save_path: str
    status: str = "submitted"


class TorrentArchiveRetryResponse(BaseModel):
    torrent_id: UUID
    archive_state: str
    archive_path: str | None = None
    manifest_path: str | None = None
    message: str | None = None
