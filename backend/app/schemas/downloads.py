from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

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
    poll_interval_seconds: int = Field(default=15, ge=5, le=3600)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))


class DownloadClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    url: AnyHttpUrl | None = None
    username: str | None = None
    # Blank passwords mean "keep the configured secret" on update.
    password: str | None = Field(default=None, max_length=4096)
    scope: DownloadScope | None = None
    category: str | None = Field(default=None, max_length=256)
    tags: list[str] | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    poll_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
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
    health: str | None
    last_health_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    latency_ms: int | None = None
    last_error: str | None = None
    revision: int
    poll_interval_seconds: int
    created_at: datetime
    updated_at: datetime


class DownloadClientTestResponse(BaseModel):
    status: str
    version: str | None = None
    latency_ms: int | None = None
    message: str | None = None


class DownloadClientTestRequest(BaseModel):
    url: AnyHttpUrl
    username: str = ""
    password: str = Field(min_length=1, max_length=4096)


class DownloadPollResponse(BaseModel):
    client_id: UUID
    status: str
    observed: int
    relevant: int
    added: int
    completed: int
    removed: int
    ignored: int
    message: str | None = None


class DownloadResponse(ORMModel):
    id: UUID
    torrent_id: UUID
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
    quality: str | None = None
    edition: str | None = None
    media_state: str | None = None
    reconciliation_state: str | None = None
    reconciliation_detail: str | None = None
    incoming: bool = False
    incoming_kind: str | None = None
