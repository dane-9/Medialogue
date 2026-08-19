from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DuplicateResolvePreviewRequest(BaseModel):
    winner_release_id: UUID
    losing_release_ids: list[UUID] = Field(min_length=1)
    delete_media: bool = False
    remove_torrents: bool = False


class DuplicateFilePreview(BaseModel):
    relative_path: str
    size: int | None = None
    is_symlink: bool = False


class DuplicateDirectoryPreview(BaseModel):
    directory_id: UUID
    path: str
    storage_root: str
    access_mode: str
    exists: bool
    files: list[DuplicateFilePreview] = Field(default_factory=list)


class DuplicateTorrentPreview(BaseModel):
    torrent_id: UUID
    info_hash: str
    name: str
    archived: bool
    qbit_present: bool
    clients: list[str] = Field(default_factory=list)


class DuplicateReleasePreview(BaseModel):
    release_id: UUID
    release_name: str
    edition: str | None = None
    quality: str | None = None
    release_group: str | None = None
    state: str
    directories: list[DuplicateDirectoryPreview] = Field(default_factory=list)
    torrents: list[DuplicateTorrentPreview] = Field(default_factory=list)


class DuplicateResolvePreviewResponse(BaseModel):
    movie_id: UUID
    movie_title: str
    winner: DuplicateReleasePreview
    losers: list[DuplicateReleasePreview]
    delete_media: bool
    remove_torrents: bool
    torrent_backups_will_be_kept: bool = True
    confirmation_token: str
    expires_at: str
    warnings: list[str] = Field(default_factory=list)


class DuplicateResolveCommitRequest(BaseModel):
    confirmation_token: str = Field(min_length=16)


class DuplicateResolveCommitResponse(BaseModel):
    movie_id: UUID
    winner_release_id: UUID
    losing_release_ids: list[UUID]
    duplicate_resolved: bool
    deleted_directories: list[str] = Field(default_factory=list)
    removed_torrents: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    problem_status: str


class EpisodeDuplicateResolveRequest(BaseModel):
    winner_media_file_id: UUID
    # Part 15 deliberately does not delete individual Show files. This action
    # records a manual logical winner; physical duplicate evidence remains open
    # until the losing file is removed outside Medialogue or a later safe folder
    # deletion workflow is used.


class EpisodeDuplicateResolveResponse(BaseModel):
    episode_id: UUID
    winner_media_file_id: UUID
    losing_media_file_ids: list[UUID]
    problem_status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
