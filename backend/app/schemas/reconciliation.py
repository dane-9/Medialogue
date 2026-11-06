from uuid import UUID

from pydantic import BaseModel, Field


class ReconciliationRunRequest(BaseModel):
    root_id: UUID | None = None


class ReconciliationRunResponse(BaseModel):
    job_ids: list[UUID]
    skipped_root_ids: list[UUID] = Field(default_factory=list)
    active_job_ids: list[UUID] = Field(default_factory=list)
    uninitialized_root_ids: list[UUID] = Field(default_factory=list)
