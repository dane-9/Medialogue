from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.domain import ReleaseState


class MovieEditionOverrideUpdate(BaseModel):
    release_id: UUID
    mode: Literal["automatic", "standard", "named"]
    edition: str | None = Field(default=None, max_length=128)


class MovieEditionOverrideRequest(BaseModel):
    updates: list[MovieEditionOverrideUpdate] = Field(min_length=1)


class MovieEditionState(BaseModel):
    release_id: UUID
    parsed_edition: str | None = None
    edition_override_active: bool
    manual_edition_override: str | None = None
    effective_edition: str | None = None
    state: ReleaseState


class MovieEditionOverrideResponse(BaseModel):
    movie_id: UUID
    duplicate_resolved: bool
    releases: list[MovieEditionState]


class MovieEditionOptionsResponse(BaseModel):
    editions: list[str]
