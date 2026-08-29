from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.models.domain import JobStatus


class ScheduledTaskResponse(BaseModel):
    id: UUID
    name: str
    job_type: str
    description: str
    interval_seconds: int
    interval_options: list[int]
    configurable: bool
    enabled: bool
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    next_run_at: datetime | None = None
    last_status: str | None = None
    last_duration_ms: int | None = None
    active_job_id: UUID | None = None
    active_job_status: JobStatus | None = None

    @field_validator("last_started_at", "last_finished_at", "next_run_at", mode="after")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite drops timezone metadata even though schedule timestamps are UTC.
        # Explicit offsets keep relative execution times correct in the UI.
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class ScheduledTaskRunResponse(BaseModel):
    job_id: UUID


class ScheduledTaskUpdateRequest(BaseModel):
    interval_seconds: int | None
