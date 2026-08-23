from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from .common import ORMModel


class ProblemResponse(ORMModel):
    id: UUID
    reason: str
    status: str
    workflow: str
    severity: str
    entity_type: str
    entity_id: UUID | None
    message: str
    subject: str | None = None
    details: dict[str, Any]
    resolution: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None
    available_actions: list[str] = Field(default_factory=list)


class ProblemResolveRequest(BaseModel):
    action: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


class ProblemSummaryResponse(BaseModel):
    open: int = 0
    suppressed: int = 0
    workflows: dict[str, int] = Field(default_factory=dict)


class ProblemRecheckAllResponse(BaseModel):
    requested: int
    job_ids: list[UUID] = Field(default_factory=list)
