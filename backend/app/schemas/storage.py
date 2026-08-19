from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .common import ORMModel


class MediaType(str, Enum):
    MOVIES = "movies"
    SHOWS = "shows"


class AccessMode(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class StorageRootCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=4096)
    media_type: MediaType
    access_mode: AccessMode = AccessMode.READ_ONLY
    enabled: bool = True
    missing_grace_checks: int = Field(default=2, ge=1, le=100)

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str) -> str:
        from pathlib import Path

        if not Path(value).is_absolute():
            raise ValueError("storage root path must be absolute")
        return value


class StorageRootUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    media_type: MediaType | None = None
    access_mode: AccessMode | None = None
    enabled: bool | None = None
    missing_grace_checks: int | None = Field(default=None, ge=1, le=100)

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str | None) -> str | None:
        from pathlib import Path

        if value is not None and not Path(value).is_absolute():
            raise ValueError("storage root path must be absolute")
        return value


class StorageRootResponse(ORMModel):
    id: UUID
    name: str
    resolved_root_path: str
    media_type: MediaType
    access_mode: AccessMode
    enabled: bool
    missing_grace_checks: int
    last_health: str | None
    last_health_checked_at: datetime | None
    last_scan_at: datetime | None
    affected_media_count: int = 0
    created_at: datetime
    updated_at: datetime


class RemotePathMappingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    integration_type: str
    integration_id: UUID | None = None
    remote_prefix: str = Field(min_length=1, max_length=4096)
    local_prefix: str = Field(min_length=1, max_length=4096)
    storage_root_id: UUID | None = None
    enabled: bool = True


class RemotePathMappingResponse(ORMModel):
    id: UUID
    name: str
    integration_type: str
    integration_id: UUID | None
    remote_prefix: str
    local_prefix: str
    storage_root_id: UUID | None
    enabled: bool
