from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MediaDiscoveryCandidate(BaseModel):
    tmdb_id: int
    title: str
    original_title: str | None = None
    year: int | None = None
    overview: str | None = None
    poster_ref: str | None = None


class MediaDiscoveryResponse(BaseModel):
    id: UUID
    directory_id: UUID
    media_type: str
    path: str
    root_name: str
    exists: bool
    review_state: str
    parsed_title: str | None = None
    parsed_year: int | None = None
    confidence: float | None = None
    quality: str | None = None
    edition: str | None = None
    hdr: list[str] = Field(default_factory=list)
    suggested: MediaDiscoveryCandidate | None = None
    selected: MediaDiscoveryCandidate | None = None
    candidates: list[MediaDiscoveryCandidate] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MediaDiscoverySelection(BaseModel):
    tmdb_id: int = Field(gt=0)


class MediaDiscoveryCount(BaseModel):
    count: int
