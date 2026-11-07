"""Titles that left the library, for the History on Movies and Shows."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import LibraryHistoryEntry, MediaType

router = APIRouter(prefix="/library", tags=["library"])


class LibraryHistoryItem(BaseModel):
    id: UUID
    media_type: MediaType
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    poster_ref: str | None = None
    season_numbers: list[int] = Field(default_factory=list)
    release_name: str | None = None
    quality: str | None = None
    size: int | None = None
    reason: str
    removed_at: datetime


@router.get("/history", response_model=list[LibraryHistoryItem])
async def library_history(
    media_type: MediaType = Query(),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[LibraryHistoryItem]:
    """Movies or shows that were removed from the library, newest first."""

    rows = (await db.scalars(
        select(LibraryHistoryEntry).where(LibraryHistoryEntry.media_type == media_type)
        .order_by(LibraryHistoryEntry.removed_at.desc()).limit(500)
    )).all()
    return [LibraryHistoryItem.model_validate(row, from_attributes=True) for row in rows]
