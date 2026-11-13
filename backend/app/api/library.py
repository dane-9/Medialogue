"""Removing titles from the library, and the History of titles that left it."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.api.downloads import _plan_response, get_qbittorrent_client_factory
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import IgnoredFolder, LibraryHistoryEntry, MediaType
from app.schemas.common import DeleteResponse
from app.schemas.downloads import DownloadDeletePlanResponse

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
    # A show is listed once it left as a whole; seasons removed from a show
    # still in the library are not.
    return [LibraryHistoryItem.model_validate(row, from_attributes=True) for row in rows if not row.season_numbers]


class LibraryRemovePlanRequest(BaseModel):
    movie_ids: list[UUID] = Field(default_factory=list, max_length=500)
    show_ids: list[UUID] = Field(default_factory=list, max_length=500)


class LibraryRemoveRequest(LibraryRemovePlanRequest):
    #: "forget" deletes the files and removes the titles; "replace" deletes the
    #: files and lists the titles in Replace Media as Missing; "keep_files"
    #: removes the titles, leaves the files and has scans skip their folders.
    mode: Literal["forget", "replace", "keep_files"]


@router.post("/remove-plan", response_model=DownloadDeletePlanResponse)
async def plan_library_remove(
    payload: LibraryRemovePlanRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DownloadDeletePlanResponse:
    """What removing these movies or shows would delete, for the user to confirm."""

    from app.services.download_deletion import plan_title_removal

    return _plan_response(await plan_title_removal(db, payload.movie_ids, payload.show_ids))


@router.post("/remove", response_model=DownloadDeletePlanResponse)
async def remove_from_library(
    payload: LibraryRemoveRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> DownloadDeletePlanResponse:
    """Remove movies or shows: with their files and torrents, or keeping the files."""

    from app.services.download_deletion import execute_title_removal

    return _plan_response(await execute_title_removal(
        db, payload.movie_ids, payload.show_ids, mode=payload.mode, client_factory=client_factory))


class IgnoredFolderItem(BaseModel):
    id: UUID
    resolved_path: str
    media_type: MediaType
    title: str
    year: int | None = None
    created_at: datetime


@router.get("/ignored-folders", response_model=list[IgnoredFolderItem])
async def list_ignored_folders(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[IgnoredFolderItem]:
    """Folders scans skip because their title was removed with the files kept."""

    rows = (await db.scalars(select(IgnoredFolder).order_by(IgnoredFolder.created_at.desc()))).all()
    return [IgnoredFolderItem.model_validate(row, from_attributes=True) for row in rows]


@router.delete("/ignored-folders/{folder_id}", response_model=DeleteResponse)
async def stop_ignoring_folder(
    folder_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    """Let scans pick the folder up again; the next scan of its media folder adds it."""

    row = await db.get(IgnoredFolder, folder_id)
    if row is None:
        raise AppError("NOT_FOUND", "That folder is not ignored.", status_code=404)
    await db.delete(row)
    await db.commit()
    return DeleteResponse(id=folder_id)
