"""Rows left out of a list's bulk action, kept on the server.

Unticking a folder in New Media, or a row in Upgrade or Replace Media, has to
survive a reload and show the same on every device, so the choice lives here
rather than in one browser.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import UiSelection

router = APIRouter(prefix="/ui-selections", tags=["ui-selections"])

ListKey = Literal["new-media-excluded", "upgrade-media-excluded", "replace-media-unticked", "replace-media-picked"]


class UiSelectionSet(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=50000)


@router.get("/{list_key}", response_model=UiSelectionSet)
async def get_selection(
    list_key: ListKey,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UiSelectionSet:
    rows = await db.scalars(select(UiSelection.item_id).where(UiSelection.list_key == list_key).order_by(UiSelection.item_id))
    return UiSelectionSet(ids=list(rows))


@router.put("/{list_key}", response_model=UiSelectionSet)
async def replace_selection(
    list_key: ListKey,
    payload: UiSelectionSet,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> UiSelectionSet:
    """Replace the whole set: what the page shows is what is stored."""

    ids = sorted({value for value in payload.ids if 0 < len(value) <= 256})
    await db.execute(delete(UiSelection).where(UiSelection.list_key == list_key))
    db.add_all(UiSelection(list_key=list_key, item_id=value) for value in ids)
    await db.commit()
    return UiSelectionSet(ids=ids)
