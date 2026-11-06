from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RecoveryReimportItem(BaseModel):
    torrent_id: UUID
    info_hash: str
    torrent_name: str
    media_type: Literal["movies", "shows"] | None = None
    media_title: str | None = None
    release_name: str | None = None
    status: Literal["ready", "already_present", "blocked"]
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    download_client_id: UUID | None = None
    download_client_name: str | None = None
    save_path: str | None = None
    resolved_save_path: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    total_file_count: int = 0
    selected_file_count: int = 0
    skipped_file_count: int = 0


class RecoveryReimportPlanResponse(BaseModel):
    generated_at: str
    expires_at: str
    confirmation_token: str
    total: int
    ready: int
    already_present: int
    blocked: int
    items: list[RecoveryReimportItem] = Field(default_factory=list)


class RecoveryReimportRequest(BaseModel):
    confirmation_token: str = Field(min_length=1)
    torrent_ids: list[UUID] = Field(min_length=1)
