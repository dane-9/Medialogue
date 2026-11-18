from __future__ import annotations

import logging

from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.api.tmdb import get_tmdb_client_factory
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.core.problem_types import problem_description, problem_title
from app.db.session import get_db
from app.models.auth import AdminUser, AuthSession
from app.models.domain import (
    AssociationType,
    Event,
    MediaDirectory,
    MediaRole,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    PlexMatchState,
    Problem,
    ProblemStatus,
    ReleaseState,
    StorageRoot,
    Tag,
    Torrent,
    TorrentClientObservation,
)
from app.schemas.common import LIBRARY_PAGE_LIMIT, Collection
from app.schemas.movies import (
    MediaDirectoryResponse,
    MovieCreate,
    MovieDetailResponse,
    MovieReleaseResponse,
    MovieReleaseRematchRequest,
    MovieReleaseRematchResponse,
    MovieSummaryResponse,
    TMDBMovieLookupResponse,
)
from app.schemas.torrent_status import MediaTorrentStatusResponse

from app.services.movies import add_movie_from_tmdb
from app.services.libraries import title_category
from app.services.pending_grabs import pending_movie_grabs
from app.services.plex_discover import lookup_tmdb_ratings
from app.services.tmdb import get_tmdb_configuration
from app.services.events import movie_event_scope, scope_predicate
from app.services.media_torrent_status import aggregate_torrent_status, release_torrent_statuses
from app.services.shared_results import cached_shared_result, shared_result
from app.services.torrent_history import movie_torrent_history

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/movies", tags=["movies"])


def _movie_query(*, for_list: bool = False):
    # The library list skips the two heaviest parts of a release: its files
    # (every subtitle, sample and extra) and its parse snapshot, which holds the
    # full parser evidence. The list reads the snapshot's confidence on its own.
    releases = selectinload(Movie.releases)
    if for_list:
        releases = releases.defer(MovieRelease.parse_snapshot, raiseload=True)
    directories = selectinload(Movie.releases).selectinload(MovieRelease.directories)
    directory_details = () if for_list else (directories.selectinload(MediaDirectory.files), directories.selectinload(MediaDirectory.storage_root))
    return select(Movie).options(
        releases,
        directories,
        *directory_details,
        selectinload(Movie.releases).selectinload(MovieRelease.quality_definition),
        selectinload(Movie.plex_observations),
        selectinload(Movie.tags),
    )


@router.get("", response_model=Collection[MovieSummaryResponse])
async def list_movies(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=LIBRARY_PAGE_LIMIT),
    query: str | None = None,
    state: str | None = None,
    tag: str | None = None,
    sort: str = "title",
    library_id: UUID | None = None,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[MovieSummaryResponse]:
    key, build = movie_list_result(library_id, query=query, tag=tag)
    items = cached_shared_result(key)
    if items is None:
        if page_size <= 250 and not state and sort in {"title", "-title"}:
            return await _movie_title_page(db, page=page, page_size=page_size, query=query, tag=tag,
                                           library_id=library_id, reverse=sort.startswith("-"))
        items = await shared_result(key, db, build)
    return _movie_collection(items, page, page_size, state, sort)


def movie_list_result(library_id: UUID | None, *, query: str | None = None, tag: str | None = None):
    return ("movies", library_id, query, tag), partial(_list_movies, query=query, tag=tag, library_id=library_id)


def _movie_filters(query: str | None, tag: str | None, library_id: UUID | None):
    conditions = []
    if library_id is not None:
        conditions.append(Movie.library_id == library_id)
    if query:
        pattern = f"%{query.strip()}%"
        conditions.append(or_(Movie.title.ilike(pattern), cast(Movie.year, String).ilike(pattern)))
    if tag:
        try:
            tag_id = UUID(tag)
        except ValueError:
            conditions.append(Movie.tags.any(func.lower(Tag.name) == tag.strip().casefold()))
        else:
            conditions.append(Movie.tags.any(Tag.id == tag_id))
    return conditions


async def _movie_title_page(
    db: AsyncSession, *, page: int, page_size: int, query: str | None, tag: str | None,
    library_id: UUID | None, reverse: bool,
) -> Collection[MovieSummaryResponse]:
    # Ordering lightweight title keys first keeps Unicode casefold ordering
    # identical to the full list. Only the selected movies need summaries.
    titles = (await db.execute(select(Movie.title, Movie.id).where(*_movie_filters(query, tag, library_id)))).all()
    titles.sort(key=lambda row: (row.title.casefold(), row.id), reverse=reverse)
    start = (page - 1) * page_size
    ids = [row.id for row in titles[start:start + page_size]]
    items = await _list_movies(db, query=query, tag=tag, library_id=library_id, movie_ids=ids) if ids else []
    by_id = {item.id: item for item in items}
    total = len(titles)
    return Collection(
        items=[by_id[movie_id] for movie_id in ids if movie_id in by_id],
        page=page, page_size=page_size, total=total, pages=(total + page_size - 1) // page_size,
    )


async def _list_movies(
    db: AsyncSession, *, query: str | None = None, tag: str | None = None, library_id: UUID | None = None,
    movie_ids: list[UUID] | None = None,
) -> list[MovieSummaryResponse]:
    statement = _movie_query(for_list=True).where(*_movie_filters(query, tag, library_id))
    if movie_ids is not None:
        statement = statement.where(Movie.id.in_(movie_ids))
    movies = list((await db.scalars(statement)).unique().all())
    movie_ids = [movie.id for movie in movies]
    problems = await _problem_counts(db, movie_ids)
    incoming_rows = (
        await db.execute(
            select(
                MovieRelease.movie_id,
                func.json_extract(MovieRelease.parse_snapshot, "$.incoming_kind"),
                MovieReleaseTorrent.torrent_id,
            )
            .join(MovieReleaseTorrent, MovieReleaseTorrent.movie_release_id == MovieRelease.id)
            .where(
                MovieRelease.movie_id.in_(movie_ids),
                MovieReleaseTorrent.association_type == AssociationType.INCOMING,
            )
            .order_by(MovieReleaseTorrent.created_at.desc())
        )
    ).all() if movies else []
    incoming_kinds: dict[UUID, str] = {}
    incoming_torrents: dict[UUID, UUID] = {}
    for movie_id, raw_kind, torrent_id in incoming_rows:
        kind = "replacement" if str(raw_kind).casefold() == "replacement" else "release"
        if movie_id not in incoming_kinds or (kind == "replacement" and incoming_kinds[movie_id] != "replacement"):
            incoming_kinds[movie_id] = kind
            incoming_torrents[movie_id] = torrent_id
    torrent_progress = dict(
        (
            await db.execute(
                select(TorrentClientObservation.torrent_id, func.max(TorrentClientObservation.progress))
                .where(
                    TorrentClientObservation.torrent_id.in_(set(incoming_torrents.values())),
                    TorrentClientObservation.is_present.is_(True),
                )
                .group_by(TorrentClientObservation.torrent_id)
            )
        ).all()
    ) if incoming_torrents else {}
    incoming_progress: dict[UUID, float] = {
        movie_id: float(torrent_progress.get(torrent_id) or 0) for movie_id, torrent_id in incoming_torrents.items()
    }
    # A grab qBittorrent has not reported yet is on its way all the same.
    for movie_id, grabs in (await pending_movie_grabs(db, movie_ids)).items():
        incoming_kinds.setdefault(movie_id, "release")
        observation = grabs[0].observation if grabs else None
        incoming_progress.setdefault(movie_id, float(observation.progress or 0) if observation else 0.0)
    active_by_movie = {
        movie.id: [
            release
            for release in movie.releases
            if release.release_state
            in {ReleaseState.CURRENT, ReleaseState.MISSING, ReleaseState.DUPLICATE}
        ]
        for movie in movies
    }
    release_statuses = await release_torrent_statuses(
        db,
        (release.id for releases in active_by_movie.values() for release in releases),
        media_type="movies",
    )
    items = [
        _summary(
            movie,
            problems.get(movie.id, 0),
            incoming_kinds.get(movie.id),
            aggregate_torrent_status(
                release_statuses.get(release.id, MediaTorrentStatusResponse())
                for release in active_by_movie[movie.id]
            ),
        )
        for movie in movies
    ]
    for item, movie in zip(items, movies):
        item.incoming_progress = incoming_progress.get(movie.id)
    return items


def _movie_collection(
    items: list[MovieSummaryResponse], page: int, page_size: int, state: str | None, sort: str,
) -> Collection[MovieSummaryResponse]:
    if state:
        items = [item for item in items if item.state.casefold() == state.casefold()]
    reverse = sort.startswith("-")
    key = sort.lstrip("-")
    if key == "year":
        ordered = sorted(items, key=lambda item: (item.year or 0, item.title.casefold()), reverse=reverse)
    elif key == "quality":
        ordered = sorted(items, key=lambda item: (item.current_quality or "", item.title.casefold()), reverse=reverse)
    elif key == "last_observed":
        ordered = sorted(items, key=lambda item: (item.last_observed_at.isoformat() if item.last_observed_at else "", item.title.casefold()), reverse=reverse)
    elif key == "problems":
        ordered = sorted(items, key=lambda item: (item.problem_count, item.title.casefold()), reverse=reverse)
    else:
        ordered = sorted(items, key=lambda item: (item.title.casefold(), item.id), reverse=reverse)
    total = len(ordered)
    start = (page - 1) * page_size
    return Collection(
        items=ordered[start : start + page_size],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


@router.post("", response_model=MovieDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_movie(
    payload: MovieCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> MovieDetailResponse:
    """Add a Movie to the library from a TMDB id.

    Mirrors POST /shows. Interactive search resolves its target against the
    movies table, so a title has to exist here before it can be searched for or
    grabbed. The new record has no releases and no directory: it reads as
    Missing until a grabbed release is discovered on disk.
    """
    existing = await db.scalar(select(Movie).where(Movie.tmdb_id == payload.tmdb_id))
    if existing is not None:
        raise AppError("MOVIE_ALREADY_EXISTS", "That TMDB Movie is already in the library.", status_code=409)
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before adding Movies.", status_code=409)
    try:
        movie = await add_movie_from_tmdb(
            db,
            payload.tmdb_id,
            monitored=payload.monitored,
            client_factory=client_factory,
            api_key=configuration.api_key,
            source="manual_add",
        )
    except AppError:
        raise
    except Exception as exc:
        await db.rollback()
        raise AppError("TMDB_MOVIE_METADATA_FAILED", f"Could not add Movie from TMDB: {exc}", status_code=503) from exc
    await db.commit()
    return await get_movie(resource_id=str(movie.id), _=None, db=db)


@router.get("/lookup", response_model=list[TMDBMovieLookupResponse])
async def lookup_movies(
    query: str = Query(..., min_length=1),
    year: int | None = None,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> list[TMDBMovieLookupResponse]:
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before looking up Movies.", status_code=409)
    client = client_factory(configuration.api_key)
    credits: dict[int, tuple[str | None, tuple[str, ...]]] = {}
    try:
        matches = await client.search_movie(query, year)
        for item in matches[:6]:
            try:
                credits[item.tmdb_id] = await client.get_movie_credits(item.tmdb_id)
            except Exception:
                logger.debug("TMDB credits lookup failed", extra={"tmdb_id": item.tmdb_id}, exc_info=True)
                credits[item.tmdb_id] = (None, ())
    except Exception as exc:
        raise AppError("TMDB_UNAVAILABLE", f"TMDB Movie lookup failed: {exc}", status_code=503) from exc
    finally:
        await client.close()
    ratings = await lookup_tmdb_ratings(db, [item.tmdb_id for item in matches[:25]], "movie")
    return [
        TMDBMovieLookupResponse(
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


@router.get("/{resource_id}", response_model=MovieDetailResponse)
async def get_movie(
    resource_id: str,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MovieDetailResponse:
    statement = _movie_query()
    if resource_id.isdigit():
        statement = statement.where(Movie.tmdb_id == int(resource_id))
    else:
        try:
            movie_id = UUID(resource_id)
        except ValueError as exc:
            raise AppError("NOT_FOUND", "Movie was not found.", status_code=404) from exc
        statement = statement.where(Movie.id == movie_id)
    movie = (await db.scalars(statement)).unique().one_or_none()
    if movie is None:
        raise AppError("NOT_FOUND", "Movie was not found.", status_code=404)
    movie_release_ids = [release.id for release in movie.releases]
    open_problems = (
        await db.scalars(
            select(Problem).where(
                Problem.status == ProblemStatus.OPEN,
                or_(
                    (Problem.entity_type == "movie") & (Problem.entity_id == movie.id),
                    (Problem.entity_type == "movie_release")
                    & Problem.entity_id.in_(movie_release_ids),
                ),
            )
        )
    ).all()
    problem_count = len(open_problems)
    event_predicate = scope_predicate(await movie_event_scope(db, movie.id))
    events = (
        await db.scalars(
            select(Event)
            .where(event_predicate)
            .order_by(Event.created_at.desc())
            .limit(20)
        )
    ).all()
    active_release_ids = [
        release.id
        for release in movie.releases
        if release.release_state
        in {ReleaseState.CURRENT, ReleaseState.MISSING, ReleaseState.DUPLICATE}
    ]
    release_statuses = await release_torrent_statuses(
        db, active_release_ids, media_type="movies"
    )
    summary_model = _summary(
        movie,
        problem_count,
        torrent_status=aggregate_torrent_status(
            release_statuses.get(release_id, MediaTorrentStatusResponse())
            for release_id in active_release_ids
        ),
    )
    incoming_rows = (
        await db.execute(
            select(MovieReleaseTorrent, MovieRelease, Torrent)
            .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
            .join(Torrent, Torrent.id == MovieReleaseTorrent.torrent_id)
            .where(
                MovieRelease.movie_id == movie.id,
                MovieReleaseTorrent.association_type == AssociationType.INCOMING,
            )
            .order_by(MovieReleaseTorrent.created_at.desc())
        )
    ).all()
    incoming: list[dict[str, object]] = []
    for association, release, torrent in incoming_rows:
        observation = await db.scalar(
            select(TorrentClientObservation)
            .where(
                TorrentClientObservation.torrent_id == torrent.id,
                TorrentClientObservation.is_present.is_(True),
            )
            .order_by(TorrentClientObservation.last_seen_at.desc())
            .limit(1)
        )
        client = (
            get_integration_config_store().get_download_client(observation.download_client_id)
            if observation is not None
            else None
        )
        incoming.append(
            {
                "id": str(association.id),
                "torrent_id": str(torrent.id),
                "release_id": str(release.id),
                "name": torrent.name,
                "client_name": client.name if client else "Unknown client",
                "progress": float(observation.progress) if observation and observation.progress is not None else 0,
                "state": observation.state if observation else "incoming",
                "torrent_state": observation.state if observation else "incoming",
                "quality": release.quality_definition.name if release.quality_definition else None,
                "edition": release.effective_edition,
                "resolved_save_path": observation.resolved_save_path if observation else None,
                "eta_seconds": _eta(observation),
                "incoming_kind": (
                    "replacement"
                    if str(release.parse_snapshot.get("incoming_kind") or "release").casefold() == "replacement"
                    else "release"
                ),
            }
        )

    for grab in (await pending_movie_grabs(db, [movie.id])).get(movie.id, []):
        observation = grab.observation
        incoming.append(
            {
                "id": str(grab.result.id),
                "torrent_id": str(grab.torrent.id) if grab.torrent else None,
                "release_id": None,
                "name": grab.torrent.name if grab.torrent else grab.result.title,
                "client_name": grab.client_name,
                "progress": float(observation.progress) if observation and observation.progress is not None else 0,
                # Sent: qBittorrent took it, and no poll has reported it yet.
                "state": observation.state if observation else "sent",
                "torrent_state": observation.state if observation else "sent",
                "quality": grab.result.quality,
                "edition": grab.result.edition,
                "resolved_save_path": observation.resolved_save_path if observation else None,
                "eta_seconds": _eta(observation),
                "incoming_kind": "release",
            }
        )
    incoming_kind = next(
        ("replacement" if str(item.get("incoming_kind")).casefold() == "replacement" else "release" for item in incoming),
        None,
    )
    summary_model.incoming = bool(incoming)
    summary_model.incoming_kind = incoming_kind
    summary_model.incoming_progress = float(incoming[0]["progress"]) if incoming else None
    if summary_model.state == "Missing" and incoming:
        summary_model.state = "Downloading"
    summary = summary_model.model_dump(exclude={"last_observed_at"})

    torrent_history = await movie_torrent_history(db, movie.id)

    active_directories = [
        directory
        for release in movie.releases
        if release.release_state in {ReleaseState.CURRENT, ReleaseState.MISSING, ReleaseState.DUPLICATE}
        for directory in release.directories
    ]
    root = await db.get(StorageRoot, active_directories[0].storage_root_id) if active_directories else None
    root_affected_count = 0
    if root and root.last_health == "unavailable":
        root_affected_count = int(
            await db.scalar(
                select(func.count()).select_from(MediaDirectory).where(MediaDirectory.storage_root_id == root.id)
            )
            or 0
        )
    reconciliation = {
        "state": summary["state"],
        "incoming_count": len(incoming),
        "missing_count": sum(item.release_state == ReleaseState.MISSING for item in movie.releases),
        "degraded_count": sum(
            directory.exists and directory.missing_check_count > 0 for directory in active_directories
        ),
        "replaced_count": sum(item.release_state == ReleaseState.REPLACED for item in movie.releases),
        "duplicate_count": sum(item.release_state == ReleaseState.DUPLICATE for item in movie.releases),
        "qbit_media_disagreement": any(
            item.reason == "TORRENT_PATH_NOT_FOUND"
            for item in open_problems
        ),
        # Plex movie metadata is advisory and never blocks TMDB-backed identity.
        "plex_blocked": False,
        "root_offline": bool(root and root.last_health == "unavailable"),
        "root_affected_count": root_affected_count,
    }
    return MovieDetailResponse(
        **summary,
        overview=movie.overview,
        releases=[
            _release(item, release_statuses.get(item.id, MediaTorrentStatusResponse()))
            for item in sorted(movie.releases, key=lambda item: item.first_seen_at, reverse=True)
        ],
        torrent_history=torrent_history,
        recent_events=[
            {
                "id": str(event.id),
                "type": event.event_type,
                "message": event.message,
                "details": event.details,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
        incoming_downloads=incoming,
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
            for problem in open_problems
        ],
        reconciliation=reconciliation,
        storage_root=root.name if root else None,
        root_health=root.last_health if root else None,
        root_affected_count=root_affected_count,
        last_observed_at=max((directory.last_seen_at for directory in active_directories), default=None),
        folder_category=await title_category(db, movie),
    )


@router.post("/{resource_id}/releases/rematch", response_model=MovieReleaseRematchResponse)
async def rematch_movie_releases(
    resource_id: UUID,
    payload: MovieReleaseRematchRequest,
    _: AuthSession = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    tmdb_client_factory=Depends(get_tmdb_client_factory),
) -> MovieReleaseRematchResponse:
    """Move releases to the movie they really are: one in the library, or a new one.

    Files, folders and torrents go with them; nothing on disk changes.
    """

    from app.services.release_rematch import move_releases

    movie = await db.get(Movie, resource_id)
    if movie is None:
        raise AppError("NOT_FOUND", "Movie was not found.", status_code=404)
    outcome = await move_releases(
        db, movie, payload.release_ids, payload.tmdb_id,
        remove_emptied=payload.remove_emptied, tmdb_client_factory=tmdb_client_factory,
    )
    target = outcome.target
    response = MovieReleaseRematchResponse(
        movie_id=target.id, title=target.title, year=target.year,
        created=outcome.created, source_removed=outcome.source_removed,
    )
    await db.commit()
    return response


async def _problem_counts(db: AsyncSession, movie_ids: list[UUID]) -> dict[UUID, int]:
    if not movie_ids:
        return {}
    release_rows = (
        await db.execute(
            select(MovieRelease.id, MovieRelease.movie_id).where(MovieRelease.movie_id.in_(movie_ids))
        )
    ).all()
    release_to_movie = {release_id: movie_id for release_id, movie_id in release_rows}
    release_ids = list(release_to_movie)
    rows = await db.execute(
        select(Problem.entity_type, Problem.entity_id, func.count())
        .where(
            Problem.status == ProblemStatus.OPEN,
            or_(
                (Problem.entity_type == "movie") & Problem.entity_id.in_(movie_ids),
                (Problem.entity_type == "movie_release") & Problem.entity_id.in_(release_ids),
            ),
        )
        .group_by(Problem.entity_type, Problem.entity_id)
    )
    counts: dict[UUID, int] = {}
    for entity_type, entity_id, count in rows:
        movie_id = entity_id if entity_type == "movie" else release_to_movie.get(entity_id)
        if movie_id is not None:
            counts[movie_id] = counts.get(movie_id, 0) + count
    return counts


def _eta(observation: TorrentClientObservation | None) -> int | None:
    # qBittorrent reports 8640000 (100 days) for "never".
    value = observation.eta_seconds if observation is not None else None
    return value if value is not None and 0 < value < 8_640_000 else None


def _summary(
    movie: Movie,
    problem_count: int,
    incoming_kind: str | None = None,
    torrent_status: MediaTorrentStatusResponse | None = None,
) -> MovieSummaryResponse:
    active = [
        release
        for release in movie.releases
        if release.release_state in {ReleaseState.CURRENT, ReleaseState.MISSING, ReleaseState.DUPLICATE}
    ]
    physical = [release for release in active if any(directory.exists for directory in release.directories)]
    if any(release.release_state == ReleaseState.DUPLICATE for release in active):
        state = "Duplicate"
    elif physical:
        state = "Present"
    elif incoming_kind is not None:
        state = "Downloading"
    else:
        state = "Missing"
    current = next((release for release in physical if release.release_state == ReleaseState.CURRENT), None)
    current = current or (physical[0] if physical else (active[0] if active else None))
    directory = next((item for item in (current.directories if current else []) if item.exists), None)
    # A summary must not depend on polling order.  A later pending observation
    # must not hide an active conflict, and a stale matched release must not
    # hide a current multiple-version result.  Prefer observations belonging
    # to active releases when that relationship is available.
    active_release_ids = {release.id for release in active}
    observations = [
        item
        for item in movie.plex_observations
        if not active_release_ids or item.movie_release_id in active_release_ids
    ]
    plex_priority = {
        PlexMatchState.CONFLICT: 5,
        PlexMatchState.MULTIPLE_VERSIONS: 4,
        PlexMatchState.UNAVAILABLE: 3,
        PlexMatchState.PENDING: 2,
        PlexMatchState.NOT_FOUND: 1,
        PlexMatchState.MATCHED: 0,
    }
    plex = max(
        observations,
        key=lambda item: (plex_priority.get(item.match_state, 0), item.last_seen_at),
        default=None,
    )
    confidence = current.identity_confidence if current else None
    last_observed = max(
        (directory.last_seen_at for release in active for directory in release.directories),
        default=None,
    )
    return MovieSummaryResponse(
        id=movie.id,
        library_id=movie.library_id,
        resource_id=str(movie.tmdb_id or movie.id),
        tmdb_id=movie.tmdb_id,
        title=movie.title,
        year=movie.year,
        monitored=movie.monitored,
        identity_state=movie.identity_state.value,
        state=state,
        current_quality=current.quality_definition.name if current and current.quality_definition else None,
        edition=current.effective_edition if current else None,
        plex_state=plex.match_state.value if plex else "unknown",
        confidence=confidence,
        location=directory.resolved_path if directory else None,
        release_count=len(active),
        problem_count=problem_count,
        poster_ref=movie.poster_ref,
        tags=sorted(movie.tags, key=lambda item: item.name.casefold()),
        incoming=incoming_kind is not None,
        incoming_kind=incoming_kind,
        last_observed_at=last_observed,
        torrent_status=torrent_status or MediaTorrentStatusResponse(),
    )


def _media_filename(directory: MediaDirectory) -> str | None:
    """The video file that represents this copy.

    media_role is what distinguishes the feature from samples, extras and
    subtitle sidecars, so the role decides rather than the filename.
    """

    files = [item for item in directory.files if item.exists] or list(directory.files)
    primary = next((item for item in files if item.media_role == MediaRole.MOVIE_VIDEO), None)
    chosen = primary or next(iter(files), None)
    return chosen.filename if chosen else None


def _release(
    release: MovieRelease,
    torrent_status: MediaTorrentStatusResponse | None = None,
) -> MovieReleaseResponse:
    confidence = release.parse_snapshot.get("identity_confidence")
    return MovieReleaseResponse(
        id=release.id,
        raw_release_name=release.raw_release_name,
        edition=release.effective_edition,
        parsed_edition=release.parsed_edition,
        edition_override_active=bool(release.edition_override_active or release.manual_edition_override is not None),
        manual_edition_override=release.manual_edition_override,
        quality=release.quality_definition.name if release.quality_definition else None,
        release_group=release.release_group,
        state=release.release_state.value,
        confidence=float(confidence) if confidence is not None else None,
        original_custom_format_score=release.original_custom_format_score,
        current_custom_format_score=release.current_custom_format_score,
        selection_snapshot=dict(release.selection_snapshot) if release.selection_snapshot else None,
        first_seen_at=release.first_seen_at,
        directories=[
            MediaDirectoryResponse(
                id=directory.id,
                resolved_path=directory.resolved_path,
                exists=directory.exists,
                missing_since=directory.missing_since,
                files=[item.relative_path for item in directory.files],
                storage_root_name=directory.storage_root.name if directory.storage_root else None,
                media_filename=_media_filename(directory),
            )
            for directory in release.directories
        ],
        parse_snapshot=release.parse_snapshot,
        torrent_status=torrent_status or MediaTorrentStatusResponse(),
    )
