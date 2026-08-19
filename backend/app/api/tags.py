from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Movie, Tag
from app.schemas.tags import TagCreate, TagResponse, TagUpdate
from app.services.events import create_event

router = APIRouter(tags=["tags"])


async def _find_movie(db: AsyncSession, resource_id: str) -> Movie:
    statement = select(Movie).options(selectinload(Movie.tags))
    if resource_id.isdigit():
        statement = statement.where(Movie.tmdb_id == int(resource_id))
    else:
        try:
            movie_id = UUID(resource_id)
        except ValueError as exc:
            raise AppError("NOT_FOUND", "Movie was not found.", status_code=404) from exc
        statement = statement.where(Movie.id == movie_id)
    movie = await db.scalar(statement)
    if movie is None:
        raise AppError("NOT_FOUND", "Movie was not found.", status_code=404)
    return movie


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


@router.patch("/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: UUID,
    payload: TagUpdate,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TagResponse:
    del admin
    row = await db.get(Tag, tag_id)
    if row is None:
        raise AppError("NOT_FOUND", "Tag was not found.", status_code=404)
    await _ensure_unique_name(db, payload.name, excluding=row.id)
    old_name = row.name
    row.name = payload.name
    await create_event(
        db,
        "tag.updated",
        entity_type="tag",
        entity_id=row.id,
        message=f"Tag '{old_name}' was renamed to '{row.name}'.",
        details={"old_name": old_name, "name": row.name},
    )
    await db.commit()
    await db.refresh(row)
    return TagResponse.model_validate(row)


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: UUID,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    del admin
    row = await db.get(Tag, tag_id)
    if row is None:
        raise AppError("NOT_FOUND", "Tag was not found.", status_code=404)
    name = row.name
    await db.delete(row)
    await create_event(
        db,
        "tag.deleted",
        entity_type="tag",
        entity_id=tag_id,
        message=f"Tag '{name}' was deleted.",
        details={"name": name},
    )
    await db.commit()


@router.post("/movies/{resource_id}/tags/{tag_id}", response_model=list[TagResponse])
async def add_movie_tag(
    resource_id: str,
    tag_id: UUID,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TagResponse]:
    del admin
    movie = await _find_movie(db, resource_id)
    tag = await db.get(Tag, tag_id)
    if tag is None:
        raise AppError("NOT_FOUND", "Tag was not found.", status_code=404)
    if all(item.id != tag.id for item in movie.tags):
        movie.tags.append(tag)
        movie.revision += 1
        await create_event(
            db,
            "movie.tags_updated",
            entity_type="movie",
            entity_id=movie.id,
            message=f"Tag '{tag.name}' was added to {movie.title}.",
            details={"action": "add", "tag_id": str(tag.id), "tag_name": tag.name},
        )
        await db.commit()
    return [TagResponse.model_validate(item) for item in sorted(movie.tags, key=lambda item: item.name.casefold())]


@router.delete("/movies/{resource_id}/tags/{tag_id}", response_model=list[TagResponse])
async def remove_movie_tag(
    resource_id: str,
    tag_id: UUID,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TagResponse]:
    del admin
    movie = await _find_movie(db, resource_id)
    removed = next((item for item in movie.tags if item.id == tag_id), None)
    if removed is not None:
        movie.tags.remove(removed)
        movie.revision += 1
        await create_event(
            db,
            "movie.tags_updated",
            entity_type="movie",
            entity_id=movie.id,
            message=f"Tag '{removed.name}' was removed from {movie.title}.",
            details={"action": "remove", "tag_id": str(removed.id), "tag_name": removed.name},
        )
        await db.commit()
    return [TagResponse.model_validate(item) for item in sorted(movie.tags, key=lambda item: item.name.casefold())]
