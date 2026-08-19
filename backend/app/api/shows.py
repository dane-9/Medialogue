from __future__ import annotations

from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.api.tmdb import get_tmdb_client_factory
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import (
    Episode,
    EpisodeMediaMap,
    Event,
    MediaDirectory,
    MediaFile,
    PlexObservation,
    PresenceState,
    Problem,
    ProblemStatus,
    Season,
    Show,
    ShowRelease,
    StorageRoot,
)
from app.schemas.common import Collection
from app.schemas.jobs import EventResponse
from app.schemas.shows import (
    EpisodeMediaResponse,
    EpisodeMappingResponse,
    EpisodeMappingUpdate,
    EpisodeResponse,
    EpisodeUpdate,
    SeasonResponse,
    SeasonUpdate,
    ShowCreate,
    ShowDetailResponse,
    ShowSummaryResponse,
    ShowUpdate,
    TMDBShowLookupResponse,
)
from app.services.events import create_event, scope_predicate, show_event_scope
from app.services.shows import add_show_from_tmdb, resolve_show_resource, set_media_file_episode_mappings, show_plex_state, show_problem_count
from app.services.tmdb import get_tmdb_configuration, sync_show_metadata

router = APIRouter(tags=["shows"])


def _show_query():
    return select(Show).options(
        selectinload(Show.episodes),
        selectinload(Show.seasons).selectinload(Season.episodes).selectinload(Episode.media_maps).selectinload(EpisodeMediaMap.media_file).selectinload(MediaFile.media_directory),
        selectinload(Show.seasons).selectinload(Season.episodes).selectinload(Episode.media_maps).selectinload(EpisodeMediaMap.media_file).selectinload(MediaFile.episode_maps).selectinload(EpisodeMediaMap.episode),
        selectinload(Show.seasons).selectinload(Season.episodes).selectinload(Episode.media_maps).selectinload(EpisodeMediaMap.show_release).selectinload(ShowRelease.quality_definition),
        selectinload(Show.plex_observations),
    )


def _show_state(show: Show) -> str:
    monitored = [episode for episode in show.episodes if episode.monitored]
    if show.identity_state.value == "conflict":
        return "Conflict"
    if not monitored:
        return "Present" if any(episode.presence_state == PresenceState.PRESENT for episode in show.episodes) else "Missing"
    return "Missing" if any(episode.presence_state != PresenceState.PRESENT for episode in monitored) else "Present"


async def _summary(db: AsyncSession, show: Show) -> ShowSummaryResponse:
    present = sum(episode.presence_state == PresenceState.PRESENT for episode in show.episodes)
    missing = sum(episode.presence_state != PresenceState.PRESENT for episode in show.episodes)
    return ShowSummaryResponse(
        id=show.id,
        resource_id=str(show.tmdb_id or show.id),
        tmdb_id=show.tmdb_id,
        tvdb_id=show.tvdb_id,
        title=show.title,
        year=show.year,
        monitored=show.monitored,
        identity_state=show.identity_state.value,
        state=_show_state(show),
        plex_state=await show_plex_state(db, show.id),
        season_count=len(show.seasons),
        episode_count=len(show.episodes),
        episodes_present=present,
        episodes_missing=missing,
        problem_count=await show_problem_count(db, show.id),
        poster_ref=show.poster_ref,
        revision=show.revision,
    )


@router.get("/shows", response_model=Collection[ShowSummaryResponse])
async def list_shows(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    query: str | None = None,
    state: str | None = None,
    sort: str = "title",
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[ShowSummaryResponse]:
    statement = _show_query()
    if query:
        pattern = f"%{query.strip()}%"
        statement = statement.where(Show.title.ilike(pattern))
    shows = list((await db.scalars(statement)).unique().all())
    items = [await _summary(db, show) for show in shows]
    if state:
        items = [item for item in items if item.state.casefold() == state.casefold()]
    reverse = sort.startswith("-")
    key = sort.lstrip("-")
    if key == "year":
        items.sort(key=lambda item: (item.year or 0, item.title.casefold()), reverse=reverse)
    elif key == "missing":
        items.sort(key=lambda item: (item.episodes_missing, item.title.casefold()), reverse=reverse)
    else:
        items.sort(key=lambda item: item.title.casefold(), reverse=reverse)
    total = len(items)
    start = (page - 1) * page_size
    return Collection(items=items[start:start + page_size], page=page, page_size=page_size, total=total, pages=(total + page_size - 1) // page_size)


@router.get("/shows/lookup", response_model=list[TMDBShowLookupResponse])
async def lookup_shows(
    query: str = Query(..., min_length=1),
    year: int | None = None,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> list[TMDBShowLookupResponse]:
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before looking up Shows.", status_code=409)
    client = client_factory(configuration.api_key)
    try:
        matches = await client.search_show(query, year)
    except Exception as exc:
        raise AppError("TMDB_UNAVAILABLE", f"TMDB Show lookup failed: {exc}", status_code=503) from exc
    finally:
        await client.close()
    return [
        TMDBShowLookupResponse(
            tmdb_id=item.tmdb_id,
            title=item.title,
            original_title=item.original_title,
            year=item.year,
            overview=item.overview,
            poster_ref=item.poster_path,
        )
        for item in matches[:25]
    ]


@router.post("/shows", response_model=ShowDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_show(
    payload: ShowCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> ShowDetailResponse:
    existing = await db.scalar(select(Show).where(Show.tmdb_id == payload.tmdb_id))
    if existing is not None:
        raise AppError("SHOW_ALREADY_EXISTS", "That TMDB Show is already in the library.", status_code=409)
    try:
        show = await add_show_from_tmdb(db, payload.tmdb_id, monitored=payload.monitored, client_factory=client_factory)
    except Exception as exc:
        await db.rollback()
        raise AppError("TMDB_SHOW_METADATA_FAILED", f"Could not add Show from TMDB: {exc}", status_code=503) from exc
    await db.commit()
    return await _detail(db, str(show.tmdb_id or show.id))


@router.get("/shows/{resource_id}", response_model=ShowDetailResponse)
async def get_show(
    resource_id: str,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ShowDetailResponse:
    return await _detail(db, resource_id)


@router.patch("/shows/{resource_id}", response_model=ShowSummaryResponse)
async def update_show(
    resource_id: str,
    payload: ShowUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> ShowSummaryResponse:
    show = await resolve_show_resource(db, resource_id)
    if show is None:
        raise AppError("NOT_FOUND", "Show was not found.", status_code=404)
    if payload.expected_revision is not None and payload.expected_revision != show.revision:
        raise AppError("REVISION_CONFLICT", "Show changed; refresh and try again.", status_code=409)
    if payload.monitored is not None:
        show.monitored = payload.monitored
    show.revision += 1
    await db.commit()
    loaded = (await db.scalars(_show_query().where(Show.id == show.id))).unique().one()
    return await _summary(db, loaded)


@router.post("/shows/{resource_id}/metadata/refresh", response_model=ShowDetailResponse)
async def refresh_show_metadata(
    resource_id: str,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> ShowDetailResponse:
    show = await resolve_show_resource(db, resource_id)
    if show is None:
        raise AppError("NOT_FOUND", "Show was not found.", status_code=404)
    try:
        counts = await sync_show_metadata(db, show, client_factory=client_factory)
    except Exception as exc:
        await db.rollback()
        raise AppError("TMDB_SHOW_METADATA_FAILED", f"Could not refresh Show metadata: {exc}", status_code=503) from exc
    show.revision += 1
    await create_event(db, "show.metadata_refreshed", entity_type="show", entity_id=show.id, message=f"Refreshed metadata for {show.title}.", details=counts)
    await db.commit()
    return await _detail(db, resource_id)


@router.patch("/seasons/{season_id}", response_model=SeasonResponse)
async def update_season(
    season_id: UUID,
    payload: SeasonUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> SeasonResponse:
    season = await db.get(Season, season_id)
    if season is None:
        raise AppError("NOT_FOUND", "Season was not found.", status_code=404)
    if payload.expected_revision is not None and payload.expected_revision != season.revision:
        raise AppError("REVISION_CONFLICT", "Season changed; refresh and try again.", status_code=409)
    if payload.monitored is not None:
        season.monitored = payload.monitored
        episodes = (await db.scalars(select(Episode).where(Episode.season_id == season.id))).all()
        for episode in episodes:
            episode.monitored = payload.monitored
            episode.revision += 1
    season.revision += 1
    await db.commit()
    show = (await db.scalars(_show_query().where(Show.id == season.show_id))).unique().one()
    return next(item for item in await _season_responses(db, show) if item.id == season.id)


@router.patch("/episodes/{episode_id}", response_model=EpisodeResponse)
async def update_episode(
    episode_id: UUID,
    payload: EpisodeUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> EpisodeResponse:
    episode = await db.get(Episode, episode_id)
    if episode is None:
        raise AppError("NOT_FOUND", "Episode was not found.", status_code=404)
    if payload.expected_revision is not None and payload.expected_revision != episode.revision:
        raise AppError("REVISION_CONFLICT", "Episode changed; refresh and try again.", status_code=409)
    if payload.monitored is not None:
        episode.monitored = payload.monitored
    episode.revision += 1
    await db.commit()
    show = (await db.scalars(_show_query().where(Show.id == episode.show_id))).unique().one()
    seasons = await _season_responses(db, show)
    return next(item for season in seasons for item in season.episodes if item.id == episode.id)


@router.put("/media-files/{media_file_id}/episode-mappings", response_model=EpisodeMappingResponse)
async def correct_episode_mapping(
    media_file_id: UUID,
    payload: EpisodeMappingUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> EpisodeMappingResponse:
    try:
        result = await set_media_file_episode_mappings(db, media_file_id, payload.episode_ids)
    except LookupError as exc:
        raise AppError("NOT_FOUND", "Media file was not found.", status_code=404) from exc
    except ValueError as exc:
        raise AppError("INVALID_EPISODE_MAPPING", str(exc), status_code=422) from exc
    await db.commit()
    return EpisodeMappingResponse(**result)


@router.get("/shows/{resource_id}/events", response_model=Collection[EventResponse])
async def get_show_events(
    resource_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[EventResponse]:
    show = await resolve_show_resource(db, resource_id)
    predicate = scope_predicate(await show_event_scope(db, show.id))
    total = int(await db.scalar(select(func.count()).select_from(Event).where(predicate)) or 0)
    rows = (
        await db.scalars(
            select(Event)
            .where(predicate)
            .order_by(Event.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Collection(
        items=[EventResponse.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


async def _detail(db: AsyncSession, resource_id: str) -> ShowDetailResponse:
    statement = _show_query()
    if resource_id.isdigit():
        statement = statement.where(Show.tmdb_id == int(resource_id))
    else:
        try:
            show_id = UUID(resource_id)
        except ValueError as exc:
            raise AppError("NOT_FOUND", "Show was not found.", status_code=404) from exc
        statement = statement.where(Show.id == show_id)
    show = (await db.scalars(statement)).unique().one_or_none()
    if show is None:
        raise AppError("NOT_FOUND", "Show was not found.", status_code=404)
    summary = await _summary(db, show)
    seasons = await _season_responses(db, show)
    episode_ids = [episode.id for episode in show.episodes]
    media_file_ids = [mapping.media_file_id for episode in show.episodes for mapping in episode.media_maps]
    conditions = [(Problem.entity_type == "show") & (Problem.entity_id == show.id)]
    if episode_ids:
        conditions.append((Problem.entity_type == "episode") & Problem.entity_id.in_(episode_ids))
    if media_file_ids:
        conditions.append((Problem.entity_type == "media_file") & Problem.entity_id.in_(media_file_ids))
    problems = (await db.scalars(select(Problem).where(Problem.status == ProblemStatus.OPEN, or_(*conditions)))).all()
    event_predicate = scope_predicate(await show_event_scope(db, show.id))
    events = (
        await db.scalars(
            select(Event).where(event_predicate).order_by(Event.created_at.desc()).limit(40)
        )
    ).all()
    directory_ids = {mapping.media_file.media_directory_id for episode in show.episodes for mapping in episode.media_maps if mapping.media_file}
    directories = (await db.scalars(select(MediaDirectory).where(MediaDirectory.id.in_(directory_ids)))).all() if directory_ids else []
    root_ids = {directory.storage_root_id for directory in directories}
    roots = (await db.scalars(select(StorageRoot).where(StorageRoot.id.in_(root_ids)))).all() if root_ids else []
    last_seen = max((directory.last_seen_at for directory in directories), default=None)
    return ShowDetailResponse(
        **summary.model_dump(),
        overview=show.overview,
        seasons=seasons,
        recent_events=[{"id": str(event.id), "type": event.event_type, "message": event.message, "details": event.details, "created_at": event.created_at.isoformat()} for event in events],
        problems=[{"id": str(problem.id), "reason": problem.reason, "message": problem.message, "severity": problem.severity.value, "details": problem.details} for problem in problems],
        storage_roots=[{"id": str(root.id), "name": root.name, "path": root.resolved_root_path, "health": root.last_health} for root in roots],
        last_observed_at=last_seen,
    )


async def _season_responses(db: AsyncSession, show: Show) -> list[SeasonResponse]:
    plex_by_file = {item.media_file_id: item.match_state.value for item in show.plex_observations if item.media_file_id}
    result: list[SeasonResponse] = []
    for season in sorted(show.seasons, key=lambda item: item.season_number):
        episode_rows: list[EpisodeResponse] = []
        for episode in sorted(season.episodes, key=lambda item: item.episode_number):
            media_rows: list[EpisodeMediaResponse] = []
            best_quality = None
            plex_states: list[str] = []
            for mapping in episode.media_maps:
                media_file = mapping.media_file
                release = mapping.show_release
                if media_file is None:
                    continue
                directory = media_file.media_directory
                path = str(PurePosixPath(directory.resolved_path) / media_file.relative_path)
                quality = release.quality_definition.name if release and release.quality_definition else None
                if media_file.exists and quality and best_quality is None:
                    best_quality = quality
                if media_file.id in plex_by_file:
                    plex_states.append(plex_by_file[media_file.id])
                shared_numbers = sorted({
                    child.episode.episode_number
                    for child in media_file.episode_maps
                    if child.episode is not None and child.episode.season_number == episode.season_number
                })
                media_rows.append(EpisodeMediaResponse(
                    media_file_id=media_file.id,
                    show_release_id=release.id if release else None,
                    path=path,
                    exists=media_file.exists,
                    quality=quality,
                    release_group=release.release_group if release else None,
                    release_name=release.raw_release_name if release else None,
                    release_scope=release.release_scope.value if release else None,
                    mapped_episode_numbers=shared_numbers,
                    manual_mapping=mapping.manual_override,
                ))
            plex_state = "unknown"
            for candidate in ("conflict", "multiple_versions", "unavailable", "pending", "not_found", "matched"):
                if candidate in plex_states:
                    plex_state = candidate
                    break
            episode_rows.append(EpisodeResponse(
                id=episode.id,
                season_number=episode.season_number,
                episode_number=episode.episode_number,
                title=episode.title,
                air_date=episode.air_date,
                tmdb_id=episode.tmdb_id,
                tvdb_id=episode.tvdb_id,
                monitored=episode.monitored,
                presence_state=episode.presence_state.value,
                revision=episode.revision,
                quality=best_quality,
                plex_state=plex_state,
                media=media_rows,
            ))
        present = sum(item.presence_state == PresenceState.PRESENT.value for item in episode_rows)
        result.append(SeasonResponse(
            id=season.id,
            season_number=season.season_number,
            title=season.title,
            monitored=season.monitored,
            revision=season.revision,
            episode_count=len(episode_rows),
            present_count=present,
            missing_count=len(episode_rows) - present,
            episodes=episode_rows,
        ))
    return result
