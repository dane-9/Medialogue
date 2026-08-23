from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import downloads as downloads_api
from app.api import indexers as indexers_api
from app.api import tmdb as tmdb_api
from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import (
    Episode,
    InteractiveSearchResult,
    Job,
    JobStatus,
    MediaProfileOverride,
    MediaType,
    Movie,
    QualityProfile,
    Season,
    Show,
)
from app.parser import parse_release_name
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.search import (
    MovieAcquisitionSearchRequest,
    SearchIndexerStatus,
    SearchJobResponse,
    SearchResultDownloadRequest,
    SearchResultDownloadResponse,
    SearchResultResponse,
    ShowAcquisitionPreviewResponse,
    ShowAcquisitionSeasonPreview,
    ShowSeasonAcquisitionSearchRequest,
)
from app.services.events import create_event
from app.services.integration_state import get_configured_download_client, get_configured_indexer
from app.services.jobs import create_job
from app.services.movies import create_movie_from_metadata
from app.services.search import SearchTarget, cleanup_expired_search_results, run_search_job
from app.services.shows import add_show_from_tmdb
from app.services.tmdb import get_show_details, get_tmdb_configuration

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


@router.post("/interactive-search/movies", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def search_unattached_movie(
    payload: MovieAcquisitionSearchRequest,
    background_tasks: BackgroundTasks,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(indexers_api.get_torznab_client_factory),
    tmdb_factory=Depends(tmdb_api.get_tmdb_client_factory),
) -> JobAcceptedResponse:
    if await db.scalar(select(Movie.id).where(Movie.tmdb_id == payload.tmdb_id)) is not None:
        raise AppError("MOVIE_ALREADY_EXISTS", "That TMDB Movie is already in the library.", status_code=409)
    if await db.get(QualityProfile, payload.quality_profile_id) is None:
        raise AppError("INVALID_QUALITY_PROFILE", "Quality Profile does not exist.", status_code=422)
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before searching for new Movies.", status_code=409)
    client = tmdb_factory(configuration.api_key)
    try:
        metadata = await client.get_movie(payload.tmdb_id)
    except Exception as exc:
        raise AppError("TMDB_UNAVAILABLE", f"TMDB Movie lookup failed: {exc}", status_code=503) from exc
    finally:
        await client.close()
    return await _start_search(
        target=SearchTarget(
            media_type=MediaType.MOVIES,
            entity_type="tmdb_movie",
            entity_id=None,
            quality_profile_id=payload.quality_profile_id,
            title=metadata.title,
            year=metadata.year,
            tmdb_id=metadata.tmdb_id,
            overview=metadata.overview,
            poster_ref=metadata.poster_path,
        ),
        background_tasks=background_tasks,
        db=db,
        client_factory=client_factory,
    )


@router.get("/interactive-search/shows/{tmdb_id}/preview", response_model=ShowAcquisitionPreviewResponse)
async def preview_unattached_show(
    tmdb_id: int,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    tmdb_factory=Depends(tmdb_api.get_tmdb_client_factory),
) -> ShowAcquisitionPreviewResponse:
    if tmdb_id <= 0:
        raise AppError("INVALID_TMDB_ID", "TMDB Show id must be positive.", status_code=422)
    if await db.scalar(select(Show.id).where(Show.tmdb_id == tmdb_id)) is not None:
        raise AppError("SHOW_ALREADY_EXISTS", "That TMDB Show is already in the library.", status_code=409)
    details = await get_show_details(db, tmdb_id, client_factory=tmdb_factory)
    if details is None:
        raise AppError("TMDB_UNAVAILABLE", "TMDB Show lookup failed.", status_code=503)
    return ShowAcquisitionPreviewResponse(
        tmdb_id=details.tmdb_id,
        title=details.title,
        year=details.year,
        overview=details.overview,
        poster_ref=details.poster_path,
        seasons=[
            ShowAcquisitionSeasonPreview(
                season_number=season.season_number,
                title=season.title,
                episode_count=season.episode_count,
                air_date=season.air_date.isoformat() if season.air_date else None,
                poster_ref=season.poster_path,
            )
            for season in details.seasons
        ],
    )


@router.post("/interactive-search/shows/seasons", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def search_unattached_show_season(
    payload: ShowSeasonAcquisitionSearchRequest,
    background_tasks: BackgroundTasks,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(indexers_api.get_torznab_client_factory),
    tmdb_factory=Depends(tmdb_api.get_tmdb_client_factory),
) -> JobAcceptedResponse:
    # This endpoint also remains usable after the first season pack has been
    # committed. That lets a multi-season acquisition retry/re-search a later
    # season while reusing the Show created by an earlier successful season.
    if await db.get(QualityProfile, payload.quality_profile_id) is None:
        raise AppError("INVALID_QUALITY_PROFILE", "Quality Profile does not exist.", status_code=422)
    details = await get_show_details(db, payload.tmdb_id, client_factory=tmdb_factory)
    if details is None:
        raise AppError("TMDB_UNAVAILABLE", "TMDB Show lookup failed.", status_code=503)
    if not any(season.season_number == payload.season_number for season in details.seasons):
        label = "Specials" if payload.season_number == 0 else f"Season {payload.season_number}"
        raise AppError("SEASON_NOT_FOUND", f"{label} does not exist for this TMDB Show.", status_code=404)
    return await _start_search(
        target=SearchTarget(
            media_type=MediaType.SHOWS,
            entity_type="tmdb_show_season",
            entity_id=None,
            quality_profile_id=payload.quality_profile_id,
            title=details.title,
            year=details.year,
            tmdb_id=details.tmdb_id,
            overview=details.overview,
            poster_ref=details.poster_path,
            season=payload.season_number,
        ),
        background_tasks=background_tasks,
        db=db,
        client_factory=client_factory,
    )


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
            int((item.custom_format_snapshot or {}).get("indexer_priority", 25)),
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
    tmdb_factory=Depends(tmdb_api.get_tmdb_client_factory),
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
    indexer = await get_configured_indexer(db, result.indexer_id) if result.indexer_id else None
    if indexer is None or not indexer.api_key:
        raise AppError("INDEXER_UNAVAILABLE", "The indexer configuration for this result no longer exists.", status_code=409)
    client = await get_configured_download_client(db, payload.download_client_id)
    if client is None or not client.enabled:
        raise AppError("DOWNLOAD_CLIENT_UNAVAILABLE", "Download client is not available.", status_code=404)
    if client.scope != result.media_type:
        raise AppError(
            "DOWNLOAD_CLIENT_SCOPE_MISMATCH",
            f"{client.name} is not eligible for {result.media_type.value} downloads.",
            status_code=409,
        )
    job = await db.get(Job, result.job_id)
    target = dict((job.summary or {}).get("target") or {}) if job is not None else {}
    unattached_movie = result.target_entity_id is None and result.target_entity_type == "tmdb_movie"
    unattached_show_season = result.target_entity_id is None and result.target_entity_type == "tmdb_show_season"
    target_show: Show | None = None
    target_season: Season | None = None
    target_season_number: int | None = None
    staged_new_show = False
    if unattached_movie:
        tmdb_id = int(target.get("tmdb_id") or 0)
        if not tmdb_id or not target.get("title"):
            raise AppError("SEARCH_TARGET_INVALID", "The TMDB search target is incomplete.", status_code=409)
        if await db.scalar(select(Movie.id).where(Movie.tmdb_id == tmdb_id)) is not None:
            raise AppError("MOVIE_ALREADY_EXISTS", "That TMDB Movie is already in the library.", status_code=409)
    if unattached_show_season:
        tmdb_id = int(target.get("tmdb_id") or 0)
        target_season_number = int(target["season"]) if target.get("season") is not None else None
        if not tmdb_id or not target.get("title") or target_season_number is None:
            raise AppError("SEARCH_TARGET_INVALID", "The TMDB Show season search target is incomplete.", status_code=409)
        parsed = parse_release_name(result.title)
        if parsed.identity.season_numbers != (target_season_number,) or parsed.identity.episode_numbers:
            raise AppError(
                "NOT_A_SEASON_PACK",
                "This result is not a single-season pack for the selected season.",
                status_code=409,
            )
        target_show = await db.scalar(select(Show).where(Show.tmdb_id == tmdb_id))
    selected_category = payload.category.strip() if payload.category and payload.category.strip() else (client.category or None)
    if result.selected_at is not None:
        existing_category = str((result.selection_snapshot or {}).get("category") or "") or None
        if result.selected_download_client_id != client.id or (payload.category is not None and existing_category != selected_category):
            raise AppError(
                "SEARCH_RESULT_ALREADY_SELECTED",
                "This result was already submitted with a different download destination.",
                status_code=409,
            )
        existing_show_id: UUID | None = None
        existing_season_id: UUID | None = None
        if result.target_entity_type == "season" and result.target_entity_id is not None:
            existing_season = await db.get(Season, result.target_entity_id)
            if existing_season is not None:
                existing_season_id = existing_season.id
                existing_show_id = existing_season.show_id
        return SearchResultDownloadResponse(
            search_result_id=result.id,
            download_client_id=client.id,
            client_name=client.name,
            status="already_submitted",
            selected_at=result.selected_at,
            movie_id=result.target_entity_id if result.target_entity_type == "movie" else None,
            show_id=existing_show_id,
            season_id=existing_season_id,
            category=existing_category,
        )
    if not result.download_url:
        raise AppError("SEARCH_RESULT_NOT_DOWNLOADABLE", "Indexer did not provide a download URL.", status_code=409)
    qbit = qbit_factory(client.url, client.username or "", client.password or "")
    torznab = None
    published_at = result.published_at
    if published_at is not None and published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    recent = bool(published_at and published_at >= now - timedelta(days=21))
    add_to_top = (client.recent_priority if recent else client.older_priority) == "first"
    category_destination: str | None = None
    if payload.category is not None:
        try:
            categories = await qbit.list_categories()
        except Exception as exc:
            await qbit.close()
            raise AppError("DOWNLOAD_CLIENT_UNAVAILABLE", f"Could not validate qBittorrent category: {exc}", status_code=503) from exc
        selected = next((item for item in categories if item.name == selected_category), None)
        if selected is None:
            await qbit.close()
            raise AppError("QBITTORRENT_CATEGORY_NOT_FOUND", "The selected qBittorrent category no longer exists.", status_code=409)
        category_destination = selected.resolved_save_path or None

    # For a brand-new Show, fully stage the logical Show/Season/Episode metadata
    # in the current DB transaction before touching qBittorrent. It remains
    # invisible to other transactions until the torrent submission succeeds;
    # a failed submission rolls the staged Show back completely.
    if unattached_show_season:
        if target_show is None:
            try:
                target_show = await add_show_from_tmdb(
                    db,
                    int(target["tmdb_id"]),
                    monitored=True,
                    client_factory=tmdb_factory,
                )
            except Exception as exc:
                await qbit.close()
                await db.rollback()
                raise AppError("TMDB_UNAVAILABLE", f"Could not prepare Show metadata: {exc}", status_code=503) from exc
            staged_new_show = True

        target_season = await db.scalar(
            select(Season).where(
                Season.show_id == target_show.id,
                Season.season_number == target_season_number,
            )
        )
        if target_season is None:
            await qbit.close()
            if staged_new_show:
                await db.rollback()
            raise AppError("SEASON_NOT_FOUND", "The selected season is not present in Show metadata.", status_code=409)

        profile_id_raw = target.get("quality_profile_id") or (result.custom_format_snapshot or {}).get("profile_id")
        existing_assignment = await db.scalar(
            select(MediaProfileOverride).where(MediaProfileOverride.show_id == target_show.id)
        )
        if existing_assignment is None and profile_id_raw:
            db.add(
                MediaProfileOverride(
                    media_type=MediaType.SHOWS,
                    show_id=target_show.id,
                    quality_profile_id=UUID(str(profile_id_raw)),
                    override_definition={},
                    revision=1,
                )
            )
    qbit_options = {
        "category": selected_category,
        "tags": tuple(client.tags or []),
        "sequential_order": client.sequential_order,
        "first_last_first": client.first_last_first,
        "content_layout": client.content_layout,
        "add_to_top": add_to_top,
    }
    try:
        if result.download_url.casefold().startswith("magnet:"):
            await qbit.add_url(
                result.download_url,
                **qbit_options,
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
                **qbit_options,
            )
    except Exception as exc:
        if unattached_show_season:
            await db.rollback()
        raise AppError("DOWNLOAD_SUBMISSION_FAILED", f"Could not submit torrent: {exc}", status_code=503) from exc
    finally:
        if torznab is not None:
            await torznab.close()
        await qbit.close()

    committed_movie: Movie | None = None
    if unattached_movie:
        committed_movie = await create_movie_from_metadata(
            db,
            tmdb_id=int(target["tmdb_id"]),
            title=str(target["title"]),
            year=int(target["year"]) if target.get("year") is not None else None,
            overview=str(target["overview"]) if target.get("overview") is not None else None,
            poster_ref=str(target["poster_ref"]) if target.get("poster_ref") is not None else None,
            monitored=True,
            source="manual_acquisition",
        )
        profile_id_raw = target.get("quality_profile_id") or (result.custom_format_snapshot or {}).get("profile_id")
        if profile_id_raw:
            profile_id = UUID(str(profile_id_raw))
            db.add(
                MediaProfileOverride(
                    media_type=MediaType.MOVIES,
                    movie_id=committed_movie.id,
                    quality_profile_id=profile_id,
                    override_definition={},
                    revision=1,
                )
            )
        result.target_entity_type = "movie"
        result.target_entity_id = committed_movie.id
    elif unattached_show_season:
        if target_show is None or target_season is None:
            raise AppError("SEARCH_TARGET_INVALID", "The Show season target could not be committed.", status_code=409)
        result.target_entity_type = "season"
        result.target_entity_id = target_season.id

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
        "category": selected_category,
        "category_destination": category_destination,
        "season_number": target_season_number if unattached_show_season else None,
        "download_client_options": {
            "recent": recent,
            "queue_position": "first" if add_to_top else "last",
            "sequential_order": client.sequential_order,
            "first_last_first": client.first_last_first,
            "content_layout": client.content_layout,
            "completed_download_handling": client.completed_download_handling,
        },
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
            "category": selected_category,
        },
    )
    await db.commit()
    return SearchResultDownloadResponse(
        search_result_id=result.id,
        download_client_id=client.id,
        client_name=client.name,
        status="submitted",
        selected_at=now,
        movie_id=result.target_entity_id if result.target_entity_type == "movie" else None,
        show_id=target_show.id if target_show is not None else None,
        season_id=target_season.id if target_season is not None else None,
        category=selected_category,
    )
