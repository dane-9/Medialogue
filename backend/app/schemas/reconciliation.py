from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ReconciliationRootStatus(BaseModel):
    id: UUID
    name: str
    path: str
    health: str | None
    affected_count: int


class ReconciliationStatusResponse(BaseModel):
    generated_at: datetime
    roots: list[ReconciliationRootStatus]
    incoming_count: int
    missing_release_count: int
    open_problem_count: int


class ReconciliationRunRequest(BaseModel):
    root_id: UUID | None = None


class ReconciliationRunResponse(BaseModel):
    job_ids: list[UUID]
    skipped_root_ids: list[UUID] = Field(default_factory=list)


class ManualAttachRequest(BaseModel):
    root_id: UUID
    path: str = Field(min_length=1, max_length=4096)
    release_name: str | None = Field(default=None, max_length=2048)
    expected_revision: int | None = Field(default=None, ge=1)


class ReconciliationActionResponse(BaseModel):
    movie_id: UUID
    status: str
    details: dict[str, Any] = Field(default_factory=dict)

