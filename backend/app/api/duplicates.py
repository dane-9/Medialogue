from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_csrf
from app.api.downloads import get_qbit_client_factory
from app.db.session import get_db
from app.schemas.duplicates import (
    DuplicateResolveCommitRequest,
    DuplicateResolvePreviewRequest,
    DuplicateResolvePreviewResponse,
    MovieEditionOptionsResponse,
    MovieEditionOverrideRequest,
    MovieEditionOverrideResponse,
)
from app.schemas.jobs import JobAcceptedResponse
from app.services.jobs import create_job, publish_job_status
from app.parser import EDITION_OPTIONS
from app.services.problem_resolution import duplicate_preview, run_duplicate_resolution, update_movie_release_editions
from app.services.runtime_jobs import launch_runtime_job

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


@router.post(
    "/movies/{resource_id}/duplicates/resolve-preview",
    response_model=DuplicateResolvePreviewResponse,
)
async def preview_movie_duplicate_resolution(
    resource_id: str,
    payload: DuplicateResolvePreviewRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DuplicateResolvePreviewResponse:
    result = await duplicate_preview(
        db,
        resource_id,
        payload.winner_release_id,
        payload.losing_release_ids,
        delete_media=payload.delete_media,
        remove_torrents=payload.remove_torrents,
    )
    return DuplicateResolvePreviewResponse.model_validate(result)


@router.post(
    "/movies/{resource_id}/duplicates/resolve",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resolve_movie_duplicate(
    resource_id: str,
    payload: DuplicateResolveCommitRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    qbit_client_factory=Depends(get_qbit_client_factory),
) -> JobAcceptedResponse:
    job = await create_job(
        db,
        "duplicate_resolution",
        cancellable=False,
        summary={
            "movie_id": resource_id,
            "message": "Applying the confirmed duplicate resolution…",
        },
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(
        job.id,
        lambda: run_duplicate_resolution(
            job.id,
            resource_id,
            payload.confirmation_token,
            qbit_client_factory=qbit_client_factory,
        ),
    )
    return JobAcceptedResponse(job_id=job.id)
