from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Tag
from app.schemas.tags import TagCreate, TagResponse
from app.services.events import create_event

router = APIRouter(tags=["tags"])


async def _ensure_unique_name(db: AsyncSession, name: str, *, excluding: UUID | None = None) -> None:
    statement = select(Tag).where(func.lower(Tag.name) == name.casefold())
    if excluding is not None:
        statement = statement.where(Tag.id != excluding)
    if await db.scalar(statement) is not None:
        raise AppError("TAG_NAME_EXISTS", "A tag with that name already exists.", status_code=409)


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TagResponse]:
    rows = (await db.scalars(select(Tag).order_by(func.lower(Tag.name), Tag.name))).all()
    return [TagResponse.model_validate(row) for row in rows]


@router.post("/tags", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    payload: TagCreate,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TagResponse:
    del admin
    await _ensure_unique_name(db, payload.name)
    row = Tag(name=payload.name)
    db.add(row)
    await db.flush()
    await create_event(
        db,
        "tag.created",
        entity_type="tag",
        entity_id=row.id,
        message=f"Tag '{row.name}' was created.",
        details={"name": row.name},
    )
    await db.commit()
    await db.refresh(row)
    return TagResponse.model_validate(row)
