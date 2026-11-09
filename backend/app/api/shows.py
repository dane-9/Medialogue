from __future__ import annotations

import logging

from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.api.tmdb import get_tmdb_client_factory
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.core.problem_types import problem_description, problem_title
from app.services.events import create_event
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Episode, EpisodeMediaMap, Event, MediaDirectory, MediaFile, PlexMatchState, PlexObservation, PresenceState, Problem, ProblemStatus, Season, Show, ShowRelease, StorageRoot
from app.schemas.common import LIBRARY_PAGE_LIMIT, Collection
from app.schemas.jobs import JobAcceptedResponse
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
from app.schemas.torrent_status import MediaTorrentStatusResponse
from app.services.plex_discover import lookup_tmdb_ratings
from app.services.events import scope_predicate, show_event_scope
from app.services.shows import PLEX_STATE_PRIORITY, add_show_from_tmdb, resolve_show_resource, set_media_file_episode_mappings, show_plex_state, show_problem_count
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job
from app.services.tmdb import get_tmdb_configuration, run_show_metadata_refresh, sync_show_metadata
from app.services.media_torrent_status import aggregate_torrent_status, release_torrent_statuses
from app.services.shared_results import shared_result

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shows"])


def _show_query():
    return select(Show).options(
        selectinload(Show.episodes),
        selectinload(Show.seasons).selectinload(Season.episodes).selectinload(Episode.media_maps).selectinload(EpisodeMediaMap.media_file).selectinload(MediaFile.media_directory),
        selectinload(Show.seasons).selectinload(Season.episodes).selectinload(Episode.media_maps).selectinload(EpisodeMediaMap.media_file).selectinload(MediaFile.episode_maps).selectinload(EpisodeMediaMap.episode),
        selectinload(Show.seasons).selectinload(Season.episodes).selectinload(Episode.media_maps).selectinload(EpisodeMediaMap.show_release).selectinload(ShowRelease.quality_definition),
        selectinload(Show.plex_observations),
    )


def _counted_episodes(show: Show) -> list[Episode]:
    """Episodes a show's totals are measured against.

    Driven by `Season.counted`, never by monitoring. Monitoring is about whether
    Medialogue keeps searching for something; a season you have finished
    collecting is normally unmonitored and must still count, or a show you fully
    own would silently shrink from 19/19 to 9/9.
    """

    counted_seasons = {season.id for season in show.seasons if season.counted}
    return [episode for episode in show.episodes if episode.season_id in counted_seasons]

def _show_state(show: Show) -> str:
    counted = _counted_episodes(show)
    if not counted:
        return "Present" if any(episode.presence_state == PresenceState.PRESENT for episode in show.episodes) else "Missing"
    return "Missing" if any(episode.presence_state != PresenceState.PRESENT for episode in counted) else "Present"


def _show_release_ids(show: Show) -> set[UUID]:
    return {
        mapping.show_release_id
        for episode in show.episodes
        for mapping in episode.media_maps
        if mapping.show_release_id is not None
    }


async def _summary(
    db: AsyncSession,
    show: Show,
    torrent_status: MediaTorrentStatusResponse | None = None,
) -> ShowSummaryResponse:
    counted = _counted_episodes(show)
    present = sum(episode.presence_state == PresenceState.PRESENT for episode in counted)
    missing = sum(episode.presence_state != PresenceState.PRESENT for episode in counted)
    last_observed = max(
        (
            mapping.media_file.media_directory.last_seen_at
            for episode in show.episodes
            for mapping in episode.media_maps
            if mapping.media_file is not None and mapping.media_file.media_directory is not None
        ),
        default=None,
    )
    if torrent_status is None:
        release_ids = _show_release_ids(show)
        release_statuses = await release_torrent_statuses(db, release_ids, media_type="shows")
        torrent_status = aggregate_torrent_status(
            release_statuses.get(release_id, MediaTorrentStatusResponse())
            for release_id in release_ids
        )
    return ShowSummaryResponse(
        id=show.id,
        resource_id=str(show.tmdb_id or show.id),
        tmdb_id=show.tmdb_id,
        tvdb_id=show.tvdb_id,
        tmdb_episode_group_id=show.tmdb_episode_group_id,
        title=show.title,
        year=show.year,
        monitored=show.monitored,
        identity_state=show.identity_state.value,
        state=_show_state(show),
        plex_state=await show_plex_state(db, show.id),
        season_count=len(show.seasons),
        episode_count=len(counted),
        episodes_present=present,
        episodes_missing=missing,
        problem_count=await show_problem_count(db, show.id),
        poster_ref=show.poster_ref,
        revision=show.revision,
        last_observed_at=last_observed,
        torrent_status=torrent_status,
    )


async def _list_summaries(db: AsyncSession, shows: list[Show]) -> list[ShowSummaryResponse]:
    """Summaries for the library list, computed with a handful of grouped queries.

    `_summary` walks each show's whole episode, file and folder tree, which is
    right for one show and far too much for the library: this reads the same
    facts as counts and maxima per show instead, and must stay equal to it.
    """

    if not shows:
        return []
    show_ids = [show.id for show in shows]
    counted = Season.counted.is_(True)
    present = Episode.presence_state == PresenceState.PRESENT
    episode_rows = (
        await db.execute(
            select(
                Episode.show_id,
                func.count(Episode.id).filter(counted),
                func.count(Episode.id).filter(counted & present),
                func.count(Episode.id).filter(present),
            )
            .join(Season, Season.id == Episode.season_id)
            .where(Episode.show_id.in_(show_ids))
            .group_by(Episode.show_id)
        )
    ).all()
    episodes = {show_id: (total, counted_present, any_present) for show_id, total, counted_present, any_present in episode_rows}
    season_counts = dict(
        (
            await db.execute(
                select(Season.show_id, func.count(Season.id)).where(Season.show_id.in_(show_ids)).group_by(Season.show_id)
            )
        ).all()
    )
    mapped = (
        select(Episode.show_id, EpisodeMediaMap.media_file_id, EpisodeMediaMap.show_release_id)
        .select_from(EpisodeMediaMap)
        .join(Episode, Episode.id == EpisodeMediaMap.episode_id)
        .where(Episode.show_id.in_(show_ids))
        .subquery()
    )
    last_observed = dict(
        (
            await db.execute(
                select(mapped.c.show_id, func.max(MediaDirectory.last_seen_at))
                .join(MediaFile, MediaFile.id == mapped.c.media_file_id)
                .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
                .group_by(mapped.c.show_id)
            )
        ).all()
    )
    release_ids: dict[UUID, set[UUID]] = {show_id: set() for show_id in show_ids}
    for show_id, release_id in (
        await db.execute(
            select(mapped.c.show_id, mapped.c.show_release_id).where(mapped.c.show_release_id.is_not(None)).distinct()
        )
    ).all():
        release_ids[show_id].add(release_id)
    release_statuses = await release_torrent_statuses(
        db, (release_id for ids in release_ids.values() for release_id in ids), media_type="shows"
    )
    plex_states: dict[UUID, list[PlexMatchState]] = {}
    for show_id, match_state in (
        await db.execute(select(PlexObservation.show_id, PlexObservation.match_state).where(PlexObservation.show_id.in_(show_ids)))
    ).all():
        plex_states.setdefault(show_id, []).append(match_state)
    problems = await _problem_counts(db, show_ids)

    items = []
    for show in shows:
        total, counted_present, any_present = episodes.get(show.id, (0, 0, 0))
        if not total:
            state = "Present" if any_present else "Missing"
        else:
            state = "Missing" if counted_present < total else "Present"
        states = plex_states.get(show.id)
        items.append(
            ShowSummaryResponse(
                id=show.id,
                resource_id=str(show.tmdb_id or show.id),
                tmdb_id=show.tmdb_id,
                tvdb_id=show.tvdb_id,
                tmdb_episode_group_id=show.tmdb_episode_group_id,
                title=show.title,
                year=show.year,
                monitored=show.monitored,
                identity_state=show.identity_state.value,
                state=state,
                plex_state=max(states, key=lambda item: PLEX_STATE_PRIORITY.get(item, 0)).value if states else "unknown",
                season_count=season_counts.get(show.id, 0),
                episode_count=total,
                episodes_present=counted_present,
                episodes_missing=total - counted_present,
                problem_count=problems.get(show.id, 0),
                poster_ref=show.poster_ref,
                revision=show.revision,
                last_observed_at=last_observed.get(show.id),
                torrent_status=aggregate_torrent_status(
                    release_statuses.get(release_id, MediaTorrentStatusResponse()) for release_id in release_ids[show.id]
                ),
            )
        )
    return items


async def _problem_counts(db: AsyncSession, show_ids: list[UUID]) -> dict[UUID, int]:
    """Open problems per show, counted the way `show_problem_count` counts them."""

    open_problem = Problem.status == ProblemStatus.OPEN
    counts: dict[UUID, int] = dict(
        (
            await db.execute(
                select(Problem.entity_id, func.count())
                .where(open_problem, Problem.entity_type == "show", Problem.entity_id.in_(show_ids))
                .group_by(Problem.entity_id)
            )
        ).all()
    )
    for show_id, count in (
        await db.execute(
            select(Episode.show_id, func.count())
            .select_from(Problem)
            .join(Episode, Episode.id == Problem.entity_id)
            .where(open_problem, Problem.entity_type == "episode", Episode.show_id.in_(show_ids))
            .group_by(Episode.show_id)
        )
    ).all():
        counts[show_id] = counts.get(show_id, 0) + count
    # A file mapped to several of a show's episodes is still one problem.
    for show_id, count in (
        await db.execute(
            select(Episode.show_id, func.count(func.distinct(Problem.id)))
            .select_from(Problem)
            .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == Problem.entity_id)
            .join(Episode, Episode.id == EpisodeMediaMap.episode_id)
            .where(open_problem, Problem.entity_type == "media_file", Episode.show_id.in_(show_ids))
            .group_by(Episode.show_id)
        )
    ).all():
        counts[show_id] = counts.get(show_id, 0) + count
    return counts


class SpecialsCountingState(BaseModel):
    count_specials: bool


class SpecialsCountingResult(SpecialsCountingState):
    seasons_changed: int
    shows_affected: int


class ShowDownloadPreferences(BaseModel):
    include_year_in_folder_name: bool = False


@router.get("/shows/specials-counting", response_model=SpecialsCountingState)
async def get_specials_counting(_: AdminUser = Depends(require_admin)) -> SpecialsCountingState:
    return SpecialsCountingState(count_specials=get_integration_config_store().get_count_specials())


@router.put("/shows/specials-counting", response_model=SpecialsCountingResult)
async def set_specials_counting(
    payload: SpecialsCountingState,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> SpecialsCountingResult:
    """Store the preference and apply it to Season 0 on every show.

    Only the counting flag moves. Monitoring is left alone, so turning Specials
    off does not quietly stop Medialogue searching for anything you had asked it
    to look for.
    """

    count = bool(payload.count_specials)
    get_integration_config_store().save_count_specials(count)

    specials = (await db.scalars(select(Season).where(Season.season_number == 0))).all()
    changed = [season for season in specials if season.counted != count]
    for season in changed:
        season.counted = count
        season.revision += 1
    if changed:
        await create_event(
            db,
            "show.specials_counting_changed",
            entity_type="library",
            entity_id=None,
            message=f"Specials {'counted in' if count else 'excluded from'} episode totals across {len({season.show_id for season in changed})} shows.",
            details={"count_specials": count, "seasons_changed": len(changed)},
        )
    await db.commit()
    return SpecialsCountingResult(
        count_specials=count,
        seasons_changed=len(changed),
        shows_affected=len({season.show_id for season in changed}),
    )


@router.get("/shows/download-preferences", response_model=ShowDownloadPreferences)
async def get_show_download_preferences(_: AdminUser = Depends(require_admin)) -> ShowDownloadPreferences:
    return ShowDownloadPreferences(
        include_year_in_folder_name=get_integration_config_store().get_show_folder_include_year()
    )


@router.put("/shows/download-preferences", response_model=ShowDownloadPreferences)
async def set_show_download_preferences(
    payload: ShowDownloadPreferences,
    _: object = Depends(require_csrf),
) -> ShowDownloadPreferences:
    value = get_integration_config_store().save_show_folder_include_year(
        payload.include_year_in_folder_name
    )
    return ShowDownloadPreferences(include_year_in_folder_name=value)


class EpisodeOrderingOption(BaseModel):
    id: str | None
    name: str
    type_label: str
    season_count: int
    episode_count: int
    description: str | None = None
    network: str | None = None
    selected: bool


@router.get("/shows/{resource_id}/episode-orderings", response_model=list[EpisodeOrderingOption])
async def list_episode_orderings(
    resource_id: str,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> list[EpisodeOrderingOption]:
    """Every ordering this show can use, default first.

    TMDB's default season structure is presented as an option in its own right
    so the choice reads as one list rather than "the normal way, plus extras".
    """

    show = await resolve_show_resource(db, resource_id)
    if show is None:
        raise AppError("NOT_FOUND", "Show was not found.", status_code=404)
    if show.tmdb_id is None:
        raise AppError("TMDB_ID_REQUIRED", "This Show has no TMDB identity, so it has no alternate orderings.", status_code=409)

    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before reading episode orderings.", status_code=409)

    client = client_factory(configuration.api_key)
    try:
        details = await client.get_show(show.tmdb_id)
        groups = await client.list_episode_groups(show.tmdb_id)
    except Exception as exc:
        raise AppError("TMDB_UNAVAILABLE", f"Could not read episode orderings: {exc}", status_code=503) from exc
    finally:
        await client.close()

    options = [
        EpisodeOrderingOption(
            id=None,
            name="TMDB default",
            type_label="Default",
            season_count=len(details.seasons),
            episode_count=sum(season.episode_count for season in details.seasons),
            description="The season structure on the TMDB record itself.",
            selected=show.tmdb_episode_group_id is None,
        )
    ]
    options.extend(
        EpisodeOrderingOption(
            id=group.id,
            name=group.name,
            type_label=group.type_label,
            season_count=group.group_count,
            episode_count=group.episode_count,
            description=group.description or None,
            network=group.network,
            selected=show.tmdb_episode_group_id == group.id,
        )
        for group in groups
    )
    return options


@router.get("/shows", response_model=Collection[ShowSummaryResponse])
async def list_shows(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=LIBRARY_PAGE_LIMIT),
    query: str | None = None,
    state: str | None = None,
    sort: str = "title",
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[ShowSummaryResponse]:
    return await shared_result(
        ("shows", page, page_size, query, state, sort),
        lambda: _list_shows(db, page=page, page_size=page_size, query=query, state=state, sort=sort),
    )


async def _list_shows(
    db: AsyncSession, *, page: int, page_size: int, query: str | None, state: str | None, sort: str
) -> Collection[ShowSummaryResponse]:
    statement = select(Show)
    if query:
        pattern = f"%{query.strip()}%"
        statement = statement.where(Show.title.ilike(pattern))
    shows = list((await db.scalars(statement)).all())
    items = await _list_summaries(db, shows)
    if state:
        items = [item for item in items if item.state.casefold() == state.casefold()]
    reverse = sort.startswith("-")
    key = sort.lstrip("-")
    if key == "year":
        items.sort(key=lambda item: (item.year or 0, item.title.casefold()), reverse=reverse)
    elif key == "missing":
        items.sort(key=lambda item: (item.episodes_missing, item.title.casefold()), reverse=reverse)
    elif key == "problems":
        items.sort(key=lambda item: (item.problem_count, item.title.casefold()), reverse=reverse)
    elif key == "last_observed":
        items.sort(key=lambda item: (item.last_observed_at.isoformat() if item.last_observed_at else "", item.title.casefold()), reverse=reverse)
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
    credits: dict[int, tuple[str | None, tuple[str, ...]]] = {}
    try:
        matches = await client.search_show(query, year)
        for item in matches[:6]:
            try:
                credits[item.tmdb_id] = await client.get_show_credits(item.tmdb_id)
            except Exception:
                logger.debug("TMDB credits lookup failed", extra={"tmdb_id": item.tmdb_id}, exc_info=True)
                credits[item.tmdb_id] = (None, ())
    except Exception as exc:
        raise AppError("TMDB_UNAVAILABLE", f"TMDB Show lookup failed: {exc}", status_code=503) from exc
    finally:
        await client.close()
    ratings = await lookup_tmdb_ratings(db, [item.tmdb_id for item in matches[:25]], "show")
    return [
        TMDBShowLookupResponse(
            tmdb_id=item.tmdb_id,
            title=item.title,
            original_title=item.original_title,
            year=item.year,
            overview=item.overview,
            poster_ref=item.poster_path,
            director=credits.get(item.tmdb_id, (None, ()))[0],
            cast=list(credits.get(item.tmdb_id, (None, ()))[1]),
            popularity=item.popularity,
            vote_count=item.vote_count,
            vote_average=item.vote_average,
            imdb_id=ratings.get(item.tmdb_id).imdb_id if item.tmdb_id in ratings else None,
            imdb_rating=ratings.get(item.tmdb_id).rating if item.tmdb_id in ratings else None,
            imdb_votes=ratings.get(item.tmdb_id).votes if item.tmdb_id in ratings else None,
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
    client_factory=Depends(get_tmdb_client_factory),
) -> ShowSummaryResponse:
    show = await resolve_show_resource(db, resource_id)
    if show is None:
        raise AppError("NOT_FOUND", "Show was not found.", status_code=404)
    if payload.expected_revision is not None and payload.expected_revision != show.revision:
        raise AppError("REVISION_CONFLICT", "Show changed; refresh and try again.", status_code=409)
    if payload.monitored is not None:
        show.monitored = payload.monitored
    reorder = False
    if payload.tmdb_episode_group_id is not None:
        selection = payload.tmdb_episode_group_id.strip() or None
        reorder = selection != show.tmdb_episode_group_id
        show.tmdb_episode_group_id = selection
    show.revision += 1
    if reorder:
        try:
            await sync_show_metadata(db, show, client_factory=client_factory)
        except Exception as exc:
            await db.rollback()
            raise AppError("TMDB_SHOW_METADATA_FAILED", f"Could not apply the selected ordering: {exc}", status_code=503) from exc
    await db.commit()
    loaded = (await db.scalars(_show_query().where(Show.id == show.id))).unique().one()
    return await _summary(db, loaded)


@router.post("/shows/{resource_id}/metadata/refresh", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def refresh_show_metadata(
    resource_id: str,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> JobAcceptedResponse:
    show = await resolve_show_resource(db, resource_id)
    if show is None:
        raise AppError("NOT_FOUND", "Show was not found.", status_code=404)
    job = await create_job(
        db,
        "tmdb_show_metadata_refresh",
        summary={
            "show_id": str(show.id),
            "show_title": show.title,
            "message": f"Refreshing TMDB metadata for {show.title}…",
        },
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_show_metadata_refresh(job.id, show.id, client_factory=client_factory))
    return JobAcceptedResponse(job_id=job.id)


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
    if payload.counted is not None:
        season.counted = payload.counted
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
    release_ids = _show_release_ids(show)
    release_statuses = await release_torrent_statuses(db, release_ids, media_type="shows")
    summary = await _summary(
        db,
        show,
        aggregate_torrent_status(
            release_statuses.get(release_id, MediaTorrentStatusResponse())
            for release_id in release_ids
        ),
    )
    seasons = await _season_responses(db, show, release_statuses=release_statuses)
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
        **summary.model_dump(exclude={"last_observed_at"}),
        overview=show.overview,
        seasons=seasons,
        recent_events=[{"id": str(event.id), "type": event.event_type, "message": event.message, "details": event.details, "created_at": event.created_at.isoformat()} for event in events],
        problems=[
            {
                "id": str(problem.id),
                "reason": problem.reason,
                "message": problem.message,
                "title": problem_title(problem.reason),
                "description": problem_description(problem.reason, problem.message),
                "severity": problem.severity.value,
                "details": problem.details,
            }
            for problem in problems
        ],
        storage_roots=[{"id": str(root.id), "name": root.name, "path": root.resolved_root_path, "health": root.last_health} for root in roots],
        last_observed_at=last_seen,
    )


async def _season_responses(
    db: AsyncSession,
    show: Show,
    *,
    release_statuses: dict[UUID, MediaTorrentStatusResponse] | None = None,
) -> list[SeasonResponse]:
    if release_statuses is None:
        release_ids = _show_release_ids(show)
        release_statuses = await release_torrent_statuses(db, release_ids, media_type="shows")
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
                    torrent_status=(
                        release_statuses.get(release.id, MediaTorrentStatusResponse())
                        if release is not None
                        else MediaTorrentStatusResponse()
                    ),
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
            counted=season.counted,
            revision=season.revision,
            episode_count=len(episode_rows),
            present_count=present,
            missing_count=len(episode_rows) - present,
            episodes=episode_rows,
        ))
    return result
