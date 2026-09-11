from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.domain import MediaType


class BlocklistEntryResponse(BaseModel):
    id: UUID
    media_type: MediaType
    target_entity_type: str
    target_entity_id: UUID | None
    media_resource_id: UUID | None = None
    media_title: str | None = None
    source_title: str
    info_hash: str | None
    indexer_name: str | None
    quality: str | None
    reason: str
    created_at: datetime


class BlocklistBulkDeleteRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=500)


class BlocklistDeleteResponse(BaseModel):
    deleted: int
