from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_csrf
from app.db.session import get_db
from app.schemas.duplicates import (
    MovieEditionOptionsResponse,
    MovieEditionOverrideRequest,
    MovieEditionOverrideResponse,
)
from app.parser import EDITION_OPTIONS
from app.services.problem_resolution import update_movie_release_editions

router = APIRouter(tags=["duplicates"])


@router.get("/movies/edition-options", response_model=MovieEditionOptionsResponse)
async def movie_edition_options() -> MovieEditionOptionsResponse:
    return MovieEditionOptionsResponse(editions=list(EDITION_OPTIONS))


@router.patch(
    "/movies/{resource_id}/releases/editions",
    response_model=MovieEditionOverrideResponse,
)
async def set_movie_release_editions(
    resource_id: str,
    payload: MovieEditionOverrideRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> MovieEditionOverrideResponse:
    result = await update_movie_release_editions(db, resource_id, payload.updates)
    await db.commit()
    return MovieEditionOverrideResponse.model_validate(result)
