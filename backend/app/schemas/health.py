from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from .downloads import DownloadScope
from .indexers import IndexerScope


class ReadinessResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    database: Literal["available", "unavailable"]


class DatabaseHealth(BaseModel):
    status: Literal["healthy"]


class ServiceHealth(BaseModel):
    configured: bool
    status: Literal["unknown", "healthy", "unavailable"]
    last_success: datetime | None = None
    latency_ms: int | None = None


class DownloadClientHealthItem(BaseModel):
    id: UUID
    name: str
    scope: DownloadScope
    enabled: bool
    health: Literal["unknown", "healthy", "unavailable"]
    last_polled_at: datetime | None = None
    last_health_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    latency_ms: int | None = None
    last_error: str | None = None


class DownloadClientsHealth(BaseModel):
    configured: bool
    status: Literal["unknown", "healthy", "degraded", "unavailable"]
    healthy_clients: int
    total_clients: int
    clients: list[DownloadClientHealthItem]


class IndexerHealthItem(BaseModel):
    id: UUID
    name: str
    scope: IndexerScope
    enabled: bool
    health: Literal["unknown", "healthy", "unavailable"]
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    latency_ms: int | None = None
    last_error: str | None = None


class IndexersHealth(BaseModel):
    configured: bool
    enabled: int
    healthy: int
    total: int
    status: Literal["unknown", "healthy", "degraded", "unavailable"]
    items: list[IndexerHealthItem]


class TorrentArchiveHealth(BaseModel):
    status: Literal["healthy", "unavailable"]
    path: str
    writable: bool
    message: str | None = None
    archived: int
    tracked: int
    missing_or_failed: int


class StorageRootHealthItem(BaseModel):
    id: UUID
    name: str
    status: Literal["unknown", "available", "unavailable"]
    checked_at: datetime | None = None


class IntegrationHealthResponse(BaseModel):
    database: DatabaseHealth
    plex: ServiceHealth
    tmdb: ServiceHealth
    qbittorrent: DownloadClientsHealth
    indexers: IndexersHealth
    torrent_archive: TorrentArchiveHealth
    storage_roots: list[StorageRootHealthItem]
