from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.domain import ProblemStatus, ProblemWorkflow, Severity

from .common import ORMModel


ProblemAction = Literal["restore", "dismiss", "recheck", "confirm_movie_match", "confirm_show_match", "choose_replacement_target"]


class ProblemResponse(ORMModel):
    id: UUID
    reason: str
    label: str = ""
    title: str = ""
    description: str = ""
    status: ProblemStatus
    workflow: ProblemWorkflow
    severity: Severity
    entity_type: str
    entity_id: UUID | None
    message: str
    subject: str | None = None
    #: Artwork for the title this Problem is about, when it has one.
    poster_ref: str | None = None
    #: TMDB id of that title, which is what actually settles an identity dispute.
    tmdb_id: int | None = None
    details: dict[str, Any]
    resolution: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None
    available_actions: list[ProblemAction] = Field(default_factory=list)


class ProblemResolveRequest(BaseModel):
    action: ProblemAction
    payload: dict[str, Any] = Field(default_factory=dict)


class ProblemSummaryResponse(BaseModel):
    open: int = 0
    suppressed: int = 0
    workflows: dict[str, int] = Field(default_factory=dict)


class ProblemRecheckAllResponse(BaseModel):
    requested: int
    job_ids: list[UUID] = Field(default_factory=list)
