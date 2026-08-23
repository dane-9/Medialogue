from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import downloads as downloads_api
from app.api import indexers as indexers_api
from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import (
    Episode,
    InteractiveSearchResult,
    Job,
    JobStatus,
    MediaType,
    Movie,
    Season,
    Show,
)
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.search import (
    SearchIndexerStatus,
    SearchJobResponse,
    SearchResultDownloadRequest,
    SearchResultDownloadResponse,
    SearchResultResponse,
)
from app.services.events import create_event
from app.services.integration_state import get_configured_download_client, get_configured_indexer
from app.services.jobs import create_job
from app.services.search import SearchTarget, cleanup_expired_search_results, run_search_job

router = APIRouter(tags=["interactive search"])


def _search_result_response(row: InteractiveSearchResult) -> SearchResultResponse:
    return SearchResultResponse(
        id=row.id,
        job_id=row.job_id,
        indexer_id=row.indexer_id,
        indexer_name=row.indexer_name,
        media_type=row.media_type.value,
        target_entity_type=row.target_entity_type,
        title=row.title,
        size=row.size,
        seeders=row.seeders,
        published_at=row.published_at,
        quality=row.quality,
        quality_allowed=bool((row.custom_format_snapshot or {}).get("quality_allowed", True)),
        quality_preference=int((row.custom_format_snapshot or {}).get("quality_preference", 0)),
        edition=row.edition,
        release_group=row.release_group,
        custom_format_score=row.custom_format_score,
        quality_profile_id=(UUID(str((row.custom_format_snapshot or {}).get("profile_id"))) if (row.custom_format_snapshot or {}).get("profile_id") else None),
        quality_profile_name=(row.custom_format_snapshot or {}).get("profile_name"),
        minimum_quality=(row.custom_format_snapshot or {}).get("minimum_quality"),
        minimum_quality_met=(row.custom_format_snapshot or {}).get("minimum_quality_met"),
        custom_format_snapshot=dict(row.custom_format_snapshot or {}),
        parser=dict(row.parse_snapshot or {}),
        warnings=list(row.warnings or []),
        selected_at=row.selected_at,
        selected_download_client_id=row.selected_download_client_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


async def _start_search(
    *,
    target: SearchTarget,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
    client_factory,
) -> JobAcceptedResponse:
    await cleanup_expired_search_results(db)
    job = await create_job(
        db,
        "interactive_search",
        cancellable=True,
        summary={"target": target.to_dict(), "indexers": {}, "result_count": 0},
    )
    await db.commit()
    background_tasks.add_task(run_search_job, job.id, client_factory=client_factory)
    return JobAcceptedResponse(job_id=job.id)


@router.post("/movies/{resource_id}/interactive-search", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def search_movie(
    resource_id: str,
    background_tasks: BackgroundTasks,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(indexers_api.get_torznab_client_factory),
) -> JobAcceptedResponse:
    movie = None
    if resource_id.isdigit():
        movie = await db.scalar(select(Movie).where(Movie.tmdb_id == int(resource_id)))
    else:
        try:
            movie_id = UUID(resource_id)
        except ValueError as exc:
            raise AppError("NOT_FOUND", "Movie was not found.", status_code=404) from exc
        movie = await db.get(Movie, movie_id)
    if movie is None:
        raise AppError("NOT_FOUND", "Movie was not found.", status_code=404)
    return await _start_search(
        target=SearchTarget(
            media_type=MediaType.MOVIES,
            entity_type="movie",
            entity_id=movie.id,
            profile_entity_id=movie.id,
            title=movie.title,
            year=movie.year,
            tmdb_id=movie.tmdb_id,
        ),
        background_tasks=background_tasks,
        db=db,
        client_factory=client_factory,
    )


@router.post("/episodes/{episode_id}/interactive-search", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def search_episode(
    episode_id: UUID,
    background_tasks: BackgroundTasks,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(indexers_api.get_torznab_client_factory),
) -> JobAcceptedResponse:
    row = (
        await db.execute(
            select(Episode, Show)
            .join(Show, Show.id == Episode.show_id)
            .where(Episode.id == episode_id)
        )
    ).first()
    if row is None:
        raise AppError("NOT_FOUND", "Episode was not found.", status_code=404)
    episode, show = row
    return await _start_search(
        target=SearchTarget(
            media_type=MediaType.SHOWS,
            entity_type="episode",
            entity_id=episode.id,
            profile_entity_id=show.id,
            title=show.title,
            year=show.year,
            tmdb_id=show.tmdb_id,
            season=episode.season_number,
            episode=episode.episode_number,
        ),
        background_tasks=background_tasks,
        db=db,
        client_factory=client_factory,
    )


@router.post("/seasons/{season_id}/interactive-search", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def search_season(
    season_id: UUID,
    background_tasks: BackgroundTasks,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(indexers_api.get_torznab_client_factory),
) -> JobAcceptedResponse:
    row = (
        await db.execute(
            select(Season, Show)
            .join(Show, Show.id == Season.show_id)
            .where(Season.id == season_id)
        )
    ).first()
    if row is None:
        raise AppError("NOT_FOUND", "Season was not found.", status_code=404)
    season_row, show = row
    return await _start_search(
        target=SearchTarget(
            media_type=MediaType.SHOWS,
            entity_type="season",
            entity_id=season_row.id,
            profile_entity_id=show.id,
            title=show.title,
            year=show.year,
            tmdb_id=show.tmdb_id,
            season=season_row.season_number,
        ),
        background_tasks=background_tasks,
        db=db,
        client_factory=client_factory,
    )


@router.get("/search-jobs/{job_id}", response_model=SearchJobResponse)
async def get_search_job(
    job_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(250, ge=1, le=250),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SearchJobResponse:
    job = await db.get(Job, job_id)
    if job is None or job.job_type != "interactive_search":
        raise AppError("NOT_FOUND", "Interactive search job was not found.", status_code=404)
    await cleanup_expired_search_results(db)
    now = datetime.now(timezone.utc)
    active_clause = or_(
        InteractiveSearchResult.selected_at.is_not(None),
        InteractiveSearchResult.expires_at >= now,
    )
    total = int(
        await db.scalar(
            select(func.count())
            .select_from(InteractiveSearchResult)
            .where(InteractiveSearchResult.job_id == job.id, active_clause)
        )
        or 0
    )
    all_rows = (
        await db.scalars(
            select(InteractiveSearchResult)
            .where(InteractiveSearchResult.job_id == job.id, active_clause)
        )
    ).all()
    # Quality order is profile-specific and lives in the immutable search
    # snapshot. Rank eligible qualities first, then Custom Format score and
    # seeders. Apply pagination only after that complete ordering.
    ordered_rows = sorted(
        all_rows,
        key=lambda item: (
            -int(bool((item.custom_format_snapshot or {}).get("quality_allowed", True))),
            -int((item.custom_format_snapshot or {}).get("quality_preference", 0)),
            -int(item.custom_format_score or 0),
            -int(item.seeders or 0),
            item.created_at,
        ),
    )
    rows = ordered_rows[(page - 1) * page_size:page * page_size]
    summary = dict(job.summary or {})
    indexer_states = summary.get("indexers") or {}
    statuses = [
        SearchIndexerStatus(
            id=UUID(str(value.get("id") or key)),
            name=str(value.get("name") or "Indexer"),
            status=str(value.get("status") or "queued"),
            results=int(value.get("results") or 0),
            elapsed_ms=int(value["elapsed_ms"]) if value.get("elapsed_ms") is not None else None,
            error=str(value["error"]) if value.get("error") else None,
        )
        for key, value in indexer_states.items()
    ]
    return SearchJobResponse(
        id=job.id,
        status=job.status.value,
        target=dict(summary.get("target") or {}),
        progress=dict(job.progress or {}),
        indexers=statuses,
        results=[_search_result_response(row) for row in rows],
        result_total=total,
        error=dict(job.error) if job.error else None,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post("/search-results/{result_id}/download", response_model=SearchResultDownloadResponse)
async def download_search_result(
    result_id: UUID,
    payload: SearchResultDownloadRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    torznab_factory=Depends(indexers_api.get_torznab_client_factory),
    qbit_factory=Depends(downloads_api.get_qbit_client_factory),
) -> SearchResultDownloadResponse:
    result = await db.scalar(
        select(InteractiveSearchResult)
        .where(InteractiveSearchResult.id == result_id)
        .with_for_update()
    )
    if result is None:
        raise AppError("NOT_FOUND", "Search result was not found.", status_code=404)
    now = datetime.now(timezone.utc)
    expires_at = result.expires_at if result.expires_at.tzinfo else result.expires_at.replace(tzinfo=timezone.utc)
    if result.selected_at is None and expires_at < now:
        raise AppError("SEARCH_RESULT_EXPIRED", "This search result has expired. Run a new search.", status_code=410)
    if not bool((result.custom_format_snapshot or {}).get("quality_allowed", True)):
        raise AppError(
            "QUALITY_NOT_ALLOWED_BY_PROFILE",
            f"{result.quality or 'This release quality'} is not enabled in the assigned Quality Profile.",
            status_code=409,
        )
    client = await get_configured_download_client(db, payload.download_client_id)
    if client is None or not client.enabled:
        raise AppError("DOWNLOAD_CLIENT_UNAVAILABLE", "Download client is not available.", status_code=404)
    if client.scope != result.media_type:
        raise AppError(
            "DOWNLOAD_CLIENT_SCOPE_MISMATCH",
            f"{client.name} is not eligible for {result.media_type.value} downloads.",
            status_code=409,
        )
    if result.selected_at is not None:
        if result.selected_download_client_id != client.id:
            raise AppError(
                "SEARCH_RESULT_ALREADY_SELECTED",
                "This result was already submitted to another download client.",
                status_code=409,
            )
        return SearchResultDownloadResponse(
            search_result_id=result.id,
            download_client_id=client.id,
            client_name=client.name,
            status="already_submitted",
            selected_at=result.selected_at,
        )
    if not result.download_url:
        raise AppError("SEARCH_RESULT_NOT_DOWNLOADABLE", "Indexer did not provide a download URL.", status_code=409)
    indexer = await get_configured_indexer(db, result.indexer_id) if result.indexer_id else None
    if indexer is None or not indexer.api_key:
        raise AppError("INDEXER_UNAVAILABLE", "The indexer configuration for this result no longer exists.", status_code=409)

    qbit = qbit_factory(client.url, client.username or "", client.password or "")
    torznab = None
    try:
        if result.download_url.casefold().startswith("magnet:"):
            await qbit.add_url(
                result.download_url,
                category=client.category,
                tags=tuple(client.tags or []),
            )
        else:
            torznab = torznab_factory(
                indexer.torznab_url,
                indexer.api_key,
                timeout=float(indexer.timeout_seconds),
            )
            torrent_bytes = await torznab.fetch_torrent(result.download_url)
            safe_filename = "".join(character if character.isalnum() or character in " ._-" else "_" for character in result.title).strip()
            await qbit.add_torrent(
                torrent_bytes,
                filename=f"{safe_filename[:180] or 'download'}.torrent",
                category=client.category,
                tags=tuple(client.tags or []),
            )
    except Exception as exc:
        raise AppError("DOWNLOAD_SUBMISSION_FAILED", f"Could not submit torrent: {exc}", status_code=503) from exc
    finally:
        if torznab is not None:
            await torznab.close()
        await qbit.close()

    result.selected_at = now
    result.selected_download_client_id = client.id
    result.selection_snapshot = {
        "search_result_id": str(result.id),
        "job_id": str(result.job_id),
        "indexer_id": str(result.indexer_id) if result.indexer_id else None,
        "indexer_name": result.indexer_name,
        "release_name": result.title,
        "size": result.size,
        "seeders": result.seeders,
        "parser": dict(result.parse_snapshot or {}),
        "quality": result.quality,
        "edition": result.edition,
        "release_group": result.release_group,
        "custom_format_score": result.custom_format_score,
        "custom_format_snapshot": dict(result.custom_format_snapshot or {}),
        "download_client_id": str(client.id),
        "download_client_name": client.name,
        "selected_at": now.isoformat(),
    }
    # Selected results are immutable search-time evidence and therefore no
    # longer participate in the 24-hour cleanup of unselected results.
    await create_event(
        db,
        "search.download_submitted",
        entity_type=result.target_entity_type,
        entity_id=result.target_entity_id,
        message=f"Submitted {result.title} to {client.name}.",
        details={
            "search_result_id": str(result.id),
            "job_id": str(result.job_id),
            "indexer": result.indexer_name,
            "download_client_id": str(client.id),
        },
    )
    await db.commit()
    return SearchResultDownloadResponse(
        search_result_id=result.id,
        download_client_id=client.id,
        client_name=client.name,
        status="submitted",
        selected_at=now,
    )
