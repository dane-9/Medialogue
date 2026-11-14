from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from .common import ORMModel
from .storage import MediaType


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("name must not be blank")
    return value


class LibraryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    kind: MediaType

    _name = field_validator("name")(_clean_name)


class LibraryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    kind: MediaType | None = None

    _name = field_validator("name")(_clean_name)


class LibraryReorder(BaseModel):
    """Ids in the order the sidebar should show them, top first."""

    ids: list[UUID] = Field(min_length=1)


class LibraryFolders(BaseModel):
    """Every media folder that should be in the library. Folders in it now and
    not listed leave it; listed folders in another library move here."""

    storage_root_ids: list[UUID] = Field(default_factory=list)


class LibraryResponse(ORMModel):
    id: UUID
    name: str
    kind: MediaType
    sort_order: int
    folder_count: int = 0
    title_count: int = 0
    created_at: datetime
    updated_at: datetime
