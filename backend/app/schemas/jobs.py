from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from .common import ORMModel


class JobResponse(ORMModel):
    id: UUID
    job_type: str
    status: str
    progress: dict[str, Any]
    summary: dict[str, Any]
    error: dict[str, Any] | None
    cancellable: bool
    revision: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class JobCreate(BaseModel):
    job_type: str = Field(min_length=1, max_length=128)
    cancellable: bool = True


class JobAcceptedResponse(BaseModel):
    job_id: UUID


class EventResponse(ORMModel):
    id: UUID
    event_type: str
    severity: str
    entity_type: str
    entity_id: UUID | None
    message: str
    details: dict[str, Any]
    created_at: datetime
