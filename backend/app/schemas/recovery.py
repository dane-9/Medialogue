from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class RecoveryCapabilitiesResponse(BaseModel):
    supported: bool
    database_backend: str
    sqlite_version: str | None = None
    database_path: str | None = None
    database_readable: bool
    migration_revision: str | None = None
    torrent_archive_readable: bool
    export_directory_writable: bool
    export_directory: str
    retention_hours: int
    reasons: list[str] = Field(default_factory=list)


class RecoveryExportAcceptedResponse(BaseModel):
    job_id: UUID
    status: str = "queued"
    warning: str = (
        "Recovery Bundles contain a complete database backup and sensitive integration credentials. "
        "Store the downloaded ZIP securely."
    )
