from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.api.plex import get_plex_client_factory
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.schemas.bulk import MovieBulkAction, MovieBulkRequest, MovieBulkResponse
from app.schemas.jobs import JobAcceptedResponse
from app.services.bulk import (
    add_movie_tags,
    change_movie_profile,
    create_bulk_summary_event,
    load_tags,
    reevaluate_movie_custom_formats,
    remove_movie_tags,
    reparse_movie_releases,
    resolve_movies,
    run_long_bulk_movie_action,
    set_movie_monitoring,
)
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job

router = APIRouter(tags=["bulk operations"])


@router.post("/movies/bulk", response_model=MovieBulkResponse | JobAcceptedResponse)
async def bulk_movies(
    payload: MovieBulkRequest,
    response: Response,
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    plex_client_factory=Depends(get_plex_client_factory),
) -> MovieBulkResponse:
    del admin
    movies = await resolve_movies(db, payload.movie_ids)
    if payload.action in {
        MovieBulkAction.RECHECK_PLEX,
        MovieBulkAction.REEVALUATE_PARSER,
        MovieBulkAction.REEVALUATE_CUSTOM_FORMATS,
    }:
        job = await create_job(
            db,
            "bulk_movie_operation",
            summary={
                "action": payload.action.value,
                "requested": len(movies),
                "movie_titles": [movie.title for movie in movies[:20]],
                "message": f"Starting bulk {payload.action.value.replace('_', ' ')} for {len(movies)} movie{'s' if len(movies) != 1 else ''}…",
            },
        )
        await db.commit()
        response.status_code = status.HTTP_202_ACCEPTED
        publish_job_status(job)
        launch_runtime_job(
            job.id,
            lambda: run_long_bulk_movie_action(
                job.id,
                [str(movie.id) for movie in movies],
                payload.action.value,
                plex_client_factory=plex_client_factory,
            ),
        )
        return JobAcceptedResponse(job_id=job.id)

    updated = 0
    details: dict[str, object] = {}

    if payload.action is MovieBulkAction.MONITOR:
        updated = await set_movie_monitoring(db, movies, True)
    elif payload.action is MovieBulkAction.UNMONITOR:
        updated = await set_movie_monitoring(db, movies, False)
    elif payload.action is MovieBulkAction.ADD_TAGS:
        tags = await load_tags(db, payload.tag_ids)
        updated = await add_movie_tags(db, movies, tags)
        details["tag_ids"] = [str(tag.id) for tag in tags]
    elif payload.action is MovieBulkAction.REMOVE_TAGS:
        tags = await load_tags(db, payload.tag_ids)
        updated = await remove_movie_tags(db, movies, tags)
        details["tag_ids"] = [str(tag.id) for tag in tags]
    elif payload.action is MovieBulkAction.CHANGE_PROFILE:
        updated = await change_movie_profile(db, movies, payload.quality_profile_id)
        details["quality_profile_id"] = str(payload.quality_profile_id) if payload.quality_profile_id else None
    elif payload.action is MovieBulkAction.REEVALUATE_PARSER:
        release_count = 0
        for movie in movies:
            release_count += await reparse_movie_releases(db, movie)
        updated = len(movies)
        details["release_count"] = release_count
    elif payload.action is MovieBulkAction.REEVALUATE_CUSTOM_FORMATS:
        release_count = 0
        for movie in movies:
            release_count += await reevaluate_movie_custom_formats(db, movie)
        updated = len(movies)
        details["release_count"] = release_count
    else:  # pragma: no cover - Enum validation prevents this branch.
        raise AppError("INVALID_BULK_ACTION", "Unsupported bulk Movie action.", status_code=422)

    await create_bulk_summary_event(
        db,
        action=payload.action.value,
        movies=movies,
        updated=updated,
        details=details,
    )
    await db.commit()
    return MovieBulkResponse(
        action=payload.action,
        requested=len(movies),
        updated=updated,
        movie_ids=[str(movie.tmdb_id or movie.id) for movie in movies],
        details=details,
    )
