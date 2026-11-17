"""Saved filters for the Movies and Shows lists.

The list pages filter what they already loaded, so the server only keeps the
filters: what each condition means lives with the page that applies it.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import CustomFilter, MediaType

router = APIRouter(prefix="/custom-filters", tags=["custom-filters"])

FilterPredicate = Literal[
    "equal", "notEqual",
    "contains", "notContains",
    "startsWith", "notStartsWith", "endsWith", "notEndsWith",
    "greaterThan", "greaterThanOrEqual", "lessThan", "lessThanOrEqual",
    "inLast", "notInLast",
]


class CustomFilterCondition(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    type: FilterPredicate
    value: list[str | float | bool] = Field(min_length=1, max_length=100)


class CustomFilterWrite(BaseModel):
    media_type: MediaType
    label: str = Field(min_length=1, max_length=128)
    filters: list[CustomFilterCondition] = Field(min_length=1, max_length=30)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("The label cannot be blank.")
        return value


class CustomFilterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    media_type: MediaType
    label: str
    filters: list[CustomFilterCondition]


async def _ensure_unique_label(db: AsyncSession, payload: CustomFilterWrite, *, excluding: UUID | None = None) -> None:
    statement = select(CustomFilter).where(
        CustomFilter.media_type == payload.media_type,
        func.lower(CustomFilter.label) == payload.label.casefold(),
    )
    if excluding is not None:
        statement = statement.where(CustomFilter.id != excluding)
    if await db.scalar(statement) is not None:
        raise AppError("CUSTOM_FILTER_LABEL_EXISTS", "A filter with that label already exists.", status_code=409)


async def _get(db: AsyncSession, filter_id: UUID) -> CustomFilter:
    row = await db.get(CustomFilter, filter_id)
    if row is None:
        raise AppError("NOT_FOUND", "Custom filter was not found.", status_code=404)
    return row


@router.get("", response_model=list[CustomFilterResponse])
async def list_custom_filters(
    media_type: MediaType | None = Query(None),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[CustomFilterResponse]:
    statement = select(CustomFilter).order_by(func.lower(CustomFilter.label))
    if media_type is not None:
        statement = statement.where(CustomFilter.media_type == media_type)
    return [CustomFilterResponse.model_validate(row) for row in await db.scalars(statement)]


@router.post("", response_model=CustomFilterResponse, status_code=status.HTTP_201_CREATED)
async def create_custom_filter(
    payload: CustomFilterWrite,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> CustomFilterResponse:
    await _ensure_unique_label(db, payload)
    row = CustomFilter(
        media_type=payload.media_type,
        label=payload.label,
        filters=[condition.model_dump() for condition in payload.filters],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return CustomFilterResponse.model_validate(row)


@router.put("/{filter_id}", response_model=CustomFilterResponse)
async def update_custom_filter(
    filter_id: UUID,
    payload: CustomFilterWrite,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> CustomFilterResponse:
    row = await _get(db, filter_id)
    if payload.media_type != row.media_type:
        raise AppError("CUSTOM_FILTER_MEDIA_TYPE", "A filter cannot move between Movies and Shows.", status_code=409)
    await _ensure_unique_label(db, payload, excluding=row.id)
    row.label = payload.label
    row.filters = [condition.model_dump() for condition in payload.filters]
    await db.commit()
    await db.refresh(row)
    return CustomFilterResponse.model_validate(row)


@router.delete("/{filter_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_custom_filter(
    filter_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> Response:
    row = await _get(db, filter_id)
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
