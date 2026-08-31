from datetime import datetime
from uuid import UUID

from typing import Literal

from app.models.domain import MediaRole, MediaType, NewMediaState
from pydantic import BaseModel, Field


class MediaDiscoveryCandidate(BaseModel):
    tmdb_id: int
    title: str
    original_title: str | None = None
    year: int | None = None
    overview: str | None = None
    poster_ref: str | None = None


class MediaDiscoveryFile(BaseModel):
    """One file inside a discovered folder."""

    relative_path: str
    role: MediaRole


class MediaDiscoveryResponse(BaseModel):
    id: UUID
    directory_id: UUID
    media_type: MediaType
    path: str
    root_name: str
    exists: bool
    review_state: NewMediaState
    parsed_title: str | None = None
    parsed_year: int | None = None
    confidence: float | None = None
    quality: str | None = None
    edition: str | None = None
    parsed_edition: str | None = None
    edition_override_active: bool = False
    manual_edition_override: str | None = None
    library_edition_conflict: bool = False
    #: Quality Profile the title will be created with. Null means the title is
    #: added without one, exactly as it was before this could be chosen.
    quality_profile_id: UUID | None = None
    quality_profile_name: str | None = None
    hdr: list[str] = Field(default_factory=list)
    suggested: MediaDiscoveryCandidate | None = None
    selected: MediaDiscoveryCandidate | None = None
    candidates: list[MediaDiscoveryCandidate] = Field(default_factory=list)
    files: list[MediaDiscoveryFile] = Field(default_factory=list)
    file_count: int = 0
    video_count: int = 0
    created_at: datetime
    updated_at: datetime


class MediaDiscoverySelection(BaseModel):
    """The identity chosen for a discovered folder.

    The optional fields carry what the user was already looking at when they
    clicked. Supplying them lets the server record the choice without a TMDB
    round trip for a title it has just been told about. They are display values
    only: approving fetches real metadata, so a wrong one cannot enter the
    library.
    """

    tmdb_id: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=512)
    original_title: str | None = Field(default=None, max_length=512)
    year: int | None = None
    poster_ref: str | None = Field(default=None, max_length=512)


class MediaDiscoverySearch(BaseModel):
    """A manual re-search for one folder. Omitting the query reuses the parsed title."""

    query: str | None = Field(default=None, max_length=512)


class MediaDiscoveryEditionUpdate(BaseModel):
    mode: Literal["automatic", "standard", "named"]
    edition: str | None = Field(default=None, max_length=128)


class MediaDiscoveryCount(BaseModel):
    count: int


class MediaDiscoveryBulk(BaseModel):
    """Approve or ignore a batch of New Media directories."""

    ids: list[UUID] = Field(min_length=1, max_length=500)
    action: Literal["approve", "ignore"]


class MediaDiscoveryQualityProfile(BaseModel):
    """The Quality Profile to give the titles these directories become.

    Null clears it, which is not the same as leaving it unset: the user is
    saying "add these with no profile" rather than "I have not decided".
    """

    quality_profile_id: UUID | None = None


class MediaDiscoveryQualityProfileBulk(MediaDiscoveryQualityProfile):
    """One profile applied across a New Media batch."""

    ids: list[UUID] = Field(min_length=1, max_length=500)


class MediaDiscoveryBulkFailure(BaseModel):
    id: UUID
    code: str
    message: str


class MediaDiscoveryBulkResult(BaseModel):
    """What happened per item, so a partial success is reportable.

    A batch is not all-or-nothing: one unreachable metadata lookup must not
    discard the items that already went through.
    """

    succeeded: list[UUID] = Field(default_factory=list)
    failed: list[MediaDiscoveryBulkFailure] = Field(default_factory=list)
