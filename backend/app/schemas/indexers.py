from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from .common import ORMModel


class IndexerScope(str, Enum):
    MOVIES = "movies"
    SHOWS = "shows"
    BOTH = "both"


def _positive(categories: list[int]) -> list[int]:
    """Torznab categories are positive; 0 would filter every search to nothing."""

    return [value for value in dict.fromkeys(categories) if value > 0]


class IndexerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    torznab_url: AnyHttpUrl
    api_key: str = Field(min_length=1, max_length=4096)
    scope: IndexerScope = IndexerScope.BOTH
    enabled: bool = True
    timeout_seconds: int = Field(default=15, ge=5, le=60)
    enable_rss: bool = True
    enable_interactive_search: bool = True
    use_for_upgrades: bool = True
    categories: list[int] = Field(default_factory=list, max_length=100)
    minimum_seeders: int = Field(default=1, ge=0, le=100000)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    multi_languages: list[str] = Field(default_factory=list, max_length=20)
    download_client_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @field_validator("categories")
    @classmethod
    def _positive_categories(cls, value: list[int]) -> list[int]:
        return _positive(value)


class IndexerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    torznab_url: AnyHttpUrl | None = None
    api_key: str | None = Field(default=None, max_length=4096)
    scope: IndexerScope | None = None
    enabled: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=5, le=60)
    enable_rss: bool | None = None
    enable_interactive_search: bool | None = None
    use_for_upgrades: bool | None = None
    categories: list[int] | None = Field(default=None, max_length=100)
    minimum_seeders: int | None = Field(default=None, ge=0, le=100000)
    color: str | None = Field(default=None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    multi_languages: list[str] | None = Field(default=None, max_length=20)
    download_client_ids: list[UUID] | None = Field(default=None, max_length=50)
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator("categories")
    @classmethod
    def _positive_categories(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else _positive(value)


class IndexerOrderRequest(BaseModel):
    """Every indexer's ID, first to last."""

    ids: list[UUID] = Field(max_length=500)


class IndexerResponse(ORMModel):
    id: UUID
    name: str
    torznab_url: str
    api_key_configured: bool
    scope: IndexerScope
    enabled: bool
    timeout_seconds: int
    enable_rss: bool
    enable_interactive_search: bool
    use_for_upgrades: bool
    categories: list[int]
    minimum_seeders: int
    priority: int  # Position in the user's order, 1 first; breaks ties between equal results.
    color: str | None
    multi_languages: list[str]
    download_client_ids: list[UUID]
    health: Literal["unknown", "healthy", "unavailable"]
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
    status: Literal["healthy", "unavailable"]
    latency_ms: int | None = None
    title: str | None = None
    message: str | None = None
