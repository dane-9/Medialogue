from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.models.domain import JobStatus, Severity

from .common import ORMModel


class JobResponse(ORMModel):
    id: UUID
    job_type: str
    status: JobStatus
    progress: dict[str, Any]
    summary: dict[str, Any]
    error: dict[str, Any] | None
    cancellable: bool
    revision: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime

    @field_validator("created_at", "started_at", "finished_at", "updated_at", mode="after")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite drops timezone metadata even though job timestamps are UTC.
        # Explicit offsets prevent browsers from treating them as local time.
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class JobAcceptedResponse(BaseModel):
    job_id: UUID


class EventResponse(ORMModel):
    id: UUID
    event_type: str
    severity: Severity
    entity_type: str
    entity_id: UUID | None
    message: str
    details: dict[str, Any]
    created_at: datetime
