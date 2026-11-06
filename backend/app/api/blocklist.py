from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Episode, Movie, ReleaseBlocklist, Season, Show
from app.schemas.blocklist import BlocklistBulkDeleteRequest, BlocklistDeleteResponse, BlocklistEntryResponse
from app.schemas.common import Collection
from app.services.events import queue_live_event


router = APIRouter(tags=["blocklist"])


async def _media_identity(db: AsyncSession, row: ReleaseBlocklist) -> tuple[str | None, UUID | None]:
    if row.target_entity_id is None:
        return None, None
    if row.target_entity_type == "movie":
        return await db.scalar(select(Movie.title).where(Movie.id == row.target_entity_id)), row.target_entity_id
    if row.target_entity_type == "season":
        result = (
            await db.execute(
                select(Show.title, Season.season_number, Show.id)
                .join(Season, Season.show_id == Show.id)
                .where(Season.id == row.target_entity_id)
            )
        ).first()
        return (f"{result[0]} · Season {result[1]}", result[2]) if result else (None, None)
    if row.target_entity_type == "episode":
        result = (
            await db.execute(
                select(Show.title, Episode.season_number, Episode.episode_number, Show.id)
                .join(Episode, Episode.show_id == Show.id)
                .where(Episode.id == row.target_entity_id)
            )
        ).first()
        return (f"{result[0]} · S{result[1]:02d}E{result[2]:02d}", result[3]) if result else (None, None)
    return None, None


async def _response(db: AsyncSession, row: ReleaseBlocklist) -> BlocklistEntryResponse:
    media_title, media_resource_id = await _media_identity(db, row)
    return BlocklistEntryResponse(
        id=row.id,
        media_type=row.media_type,
        target_entity_type=row.target_entity_type,
        target_entity_id=row.target_entity_id,
        media_title=media_title,
        media_resource_id=media_resource_id,
        source_title=row.source_title,
        info_hash=row.info_hash,
        indexer_name=row.indexer_name,
        quality=row.quality,
        reason=row.reason,
        created_at=row.created_at,
    )


@router.get("/blocklist", response_model=Collection[BlocklistEntryResponse])
async def list_blocklist(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=250),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[BlocklistEntryResponse]:
    total = int(await db.scalar(select(func.count()).select_from(ReleaseBlocklist)) or 0)
    rows = (
        await db.scalars(
            select(ReleaseBlocklist)
            .order_by(ReleaseBlocklist.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Collection(
        items=[await _response(db, row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


@router.get("/blocklist/count")
async def blocklist_count(
    _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict[str, int]:
    return {"count": int(await db.scalar(select(func.count()).select_from(ReleaseBlocklist)) or 0)}


async def _delete_ids(db: AsyncSession, ids: list[UUID]) -> int:
    result = await db.execute(delete(ReleaseBlocklist).where(ReleaseBlocklist.id.in_(ids)))
    deleted = int(result.rowcount or 0)
    if deleted:
        # Broadcast only once the deletion is committed.
        queue_live_event(db, "blocklist.changed", entity_type="blocklist", data={"deleted": deleted})
    await db.commit()
    return deleted


@router.delete("/blocklist/{entry_id}", response_model=BlocklistDeleteResponse)
async def delete_blocklist_entry(
    entry_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> BlocklistDeleteResponse:
    return BlocklistDeleteResponse(deleted=await _delete_ids(db, [entry_id]))


@router.post("/blocklist/delete", response_model=BlocklistDeleteResponse)
async def delete_blocklist_entries(
    payload: BlocklistBulkDeleteRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> BlocklistDeleteResponse:
    return BlocklistDeleteResponse(deleted=await _delete_ids(db, payload.ids))


@router.delete("/blocklist", response_model=BlocklistDeleteResponse)
async def clear_blocklist(
    _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)
) -> BlocklistDeleteResponse:
    result = await db.execute(delete(ReleaseBlocklist))
    deleted = int(result.rowcount or 0)
    if deleted:
        # Broadcast only once the deletion is committed.
        queue_live_event(db, "blocklist.changed", entity_type="blocklist", data={"deleted": deleted})
    await db.commit()
    return BlocklistDeleteResponse(deleted=deleted)
