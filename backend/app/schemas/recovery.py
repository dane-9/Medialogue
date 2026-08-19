from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RecoveryCapabilitiesResponse(BaseModel):
    supported: bool
    database_backend: str
    postgres_server_version: str | None = None
    postgres_server_major: int | None = None
    pg_basebackup_available: bool
    pg_basebackup_version: str | None = None
    pg_basebackup_major: int | None = None
    migration_revision: str | None = None
    custom_tablespaces: list[dict[str, Any]] = Field(default_factory=list)
    torrent_archive_readable: bool
    export_directory_writable: bool
    export_directory: str
    retention_hours: int
    reasons: list[str] = Field(default_factory=list)


class RecoveryExportAcceptedResponse(BaseModel):
    job_id: UUID
    status: str = "queued"
    warning: str = (
        "Recovery Bundles contain a physical database backup and sensitive integration credentials. "
        "Store the downloaded ZIP securely."
    )
