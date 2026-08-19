from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field

from .common import ORMModel


class IndexerScope(str, Enum):
    MOVIES = "movies"
    SHOWS = "shows"
    BOTH = "both"


class IndexerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    torznab_url: AnyHttpUrl
    api_key: str = Field(min_length=1, max_length=4096)
    scope: IndexerScope = IndexerScope.BOTH
    enabled: bool = True
    timeout_seconds: int = Field(default=15, ge=5, le=60)


class IndexerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    torznab_url: AnyHttpUrl | None = None
    # Blank/omitted API keys preserve the stored secret.
    api_key: str | None = Field(default=None, max_length=4096)
    scope: IndexerScope | None = None
    enabled: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=5, le=60)
    expected_revision: int | None = Field(default=None, ge=1)


class IndexerResponse(ORMModel):
    id: UUID
    name: str
    torznab_url: str
    api_key_configured: bool
    scope: IndexerScope
    enabled: bool
    timeout_seconds: int
    health: str
    last_checked_at: datetime | None
    last_success_at: datetime | None
    latency_ms: int | None
    last_error: str | None
    revision: int
    created_at: datetime
    updated_at: datetime


class IndexerTestRequest(BaseModel):
    torznab_url: AnyHttpUrl
    api_key: str = Field(min_length=1, max_length=4096)
    timeout_seconds: int = Field(default=15, ge=5, le=60)


class IndexerTestResponse(BaseModel):
    status: str
    latency_ms: int | None = None
    title: str | None = None
    message: str | None = None
