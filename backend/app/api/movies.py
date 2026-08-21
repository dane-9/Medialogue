from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin
from app.api.tmdb import get_tmdb_client_factory
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import (
    AssociationType,
    DownloadClient,
    Event,
    MediaDirectory,
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
from app.schemas.common import Collection
from app.schemas.jobs import EventResponse
from app.schemas.movies import (
    MediaDirectoryResponse,
    MovieDetailResponse,
    MovieReleaseResponse,
    MovieSummaryResponse,
    TMDBMovieLookupResponse,
)

from app.services.tmdb import get_tmdb_configuration
from app.services.events import movie_event_scope, scope_predicate

router = APIRouter(prefix="/movies", tags=["movies"])


def _movie_query():
    return select(Movie).options(
        selectinload(Movie.releases).selectinload(MovieRelease.directories).selectinload(MediaDirectory.files),
        selectinload(Movie.releases).selectinload(MovieRelease.quality_definition),
        selectinload(Movie.plex_observations),
        selectinload(Movie.tags),
    )


@router.get("", response_model=Collection[MovieSummaryResponse])
async def list_movies(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    query: str | None = None,
    state: str | None = None,
    tag: str | None = None,
    sort: str = "title",
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[MovieSummaryResponse]:
    statement = _movie_query()
    if query:
        pattern = f"%{query.strip()}%"
        statement = statement.where(or_(Movie.title.ilike(pattern), cast(Movie.year, String).ilike(pattern)))
    if tag:
        try:
            tag_id = UUID(tag)
        except ValueError:
            statement = statement.where(Movie.tags.any(func.lower(Tag.name) == tag.strip().casefold()))
        else:
            statement = statement.where(Movie.tags.any(Tag.id == tag_id))
    movies = list((await db.scalars(statement)).unique().all())
    problems = await _problem_counts(db, [movie.id for movie in movies])
    items = [_summary(movie, problems.get(movie.id, 0)) for movie in movies]
    if state:
        items = [item for item in items if item.state.casefold() == state.casefold()]
    reverse = sort.startswith("-")
    key = sort.lstrip("-")
    if key == "year":
        items.sort(key=lambda item: (item.year or 0, item.title.casefold()), reverse=reverse)
    else:
        items.sort(key=lambda item: item.title.casefold(), reverse=reverse)
    total = len(items)
    start = (page - 1) * page_size
    return Collection(
        items=items[start : start + page_size],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )




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
    try:
        matches = await client.search_movie(query, year)
    except Exception as exc:
        raise AppError("TMDB_UNAVAILABLE", f"TMDB Movie lookup failed: {exc}", status_code=503) from exc
    finally:
        await client.close()
    return [
        TMDBMovieLookupResponse(
            tmdb_id=item.tmdb_id,
            title=item.title,
            original_title=item.original_title,
            year=item.year,
            overview=item.overview,
            poster_ref=item.poster_path,
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
    summary = _summary(movie, problem_count).model_dump()
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
        observation_row = (
            await db.execute(
                select(TorrentClientObservation, DownloadClient)
                .join(DownloadClient, DownloadClient.id == TorrentClientObservation.download_client_id)
                .where(
                    TorrentClientObservation.torrent_id == torrent.id,
                    TorrentClientObservation.is_present.is_(True),
                )
                .order_by(TorrentClientObservation.last_seen_at.desc())
                .limit(1)
            )
        ).first()
        observation, client = observation_row if observation_row else (None, None)
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
                "incoming_kind": release.parse_snapshot.get("incoming_kind") or "release",
            }
        )

    torrent_rows = (
        await db.execute(
            select(MovieReleaseTorrent, MovieRelease, Torrent)
            .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
            .join(Torrent, Torrent.id == MovieReleaseTorrent.torrent_id)
            .where(MovieRelease.movie_id == movie.id)
            .order_by(MovieReleaseTorrent.created_at.desc())
        )
    ).all()
    torrent_history: list[dict[str, object]] = []
    seen_torrents: set[UUID] = set()
    for association, release, torrent in torrent_rows:
        if torrent.id in seen_torrents:
            continue
        seen_torrents.add(torrent.id)
        observation_row = (
            await db.execute(
                select(TorrentClientObservation, DownloadClient)
                .join(DownloadClient, DownloadClient.id == TorrentClientObservation.download_client_id)
                .where(TorrentClientObservation.torrent_id == torrent.id)
                .order_by(TorrentClientObservation.first_seen_at.asc())
                .limit(1)
            )
        ).first()
        observation, history_client = observation_row if observation_row else (None, None)
        qbit_present = bool(
            await db.scalar(
                select(func.count())
                .select_from(TorrentClientObservation)
                .where(
                    TorrentClientObservation.torrent_id == torrent.id,
                    TorrentClientObservation.is_present.is_(True),
                )
            )
        )
        torrent_history.append(
            {
                "id": str(torrent.id),
                "release_id": str(release.id),
                "release_name": release.raw_release_name,
                "info_hash": torrent.info_hash,
                "archive_state": torrent.archive_state.value,
                "archive_path": torrent.archive_path,
                "manifest_path": torrent.manifest_path,
                "manifest_schema_version": torrent.manifest_schema_version,
                "association_type": association.association_type.value,
                "download_client_name": history_client.name if history_client else None,
                "qbit_present": qbit_present,
                "first_seen_at": torrent.first_seen_at.isoformat(),
                "completed_at": torrent.completed_at.isoformat() if torrent.completed_at else None,
            }
        )

    active_directories = [
        directory
        for release in movie.releases
        if release.release_state in {ReleaseState.CURRENT, ReleaseState.MISSING, ReleaseState.DUPLICATE, ReleaseState.CONFLICT}
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
            item.reason in {"TORRENT_REMOVED_EXTERNALLY", "TORRENT_PATH_NOT_FOUND"}
            for item in open_problems
        ),
        "plex_blocked": any(item.reason == "PLEX_IDENTITY_MISMATCH" for item in open_problems),
        "root_offline": bool(root and root.last_health == "unavailable"),
        "root_affected_count": root_affected_count,
    }
    return MovieDetailResponse(
        **summary,
        overview=movie.overview,
        releases=[_release(item) for item in sorted(movie.releases, key=lambda item: item.first_seen_at, reverse=True)],
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
    )


@router.get("/{resource_id}/events", response_model=Collection[EventResponse])
async def get_movie_events(
    resource_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[EventResponse]:
    if resource_id.isdigit():
        movie = await db.scalar(select(Movie).where(Movie.tmdb_id == int(resource_id)))
    else:
        try:
            movie = await db.get(Movie, UUID(resource_id))
        except ValueError as exc:
            raise AppError("NOT_FOUND", "Movie was not found.", status_code=404) from exc
    if movie is None:
        raise AppError("NOT_FOUND", "Movie was not found.", status_code=404)
    predicate = scope_predicate(await movie_event_scope(db, movie.id))
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


async def _problem_counts(db: AsyncSession, movie_ids: list[UUID]) -> dict[UUID, int]:
    if not movie_ids:
        return {}
    rows = await db.execute(
        select(Problem.entity_id, func.count())
        .where(
            Problem.entity_type == "movie",
            Problem.entity_id.in_(movie_ids),
            Problem.status == ProblemStatus.OPEN,
        )
        .group_by(Problem.entity_id)
    )
    return {entity_id: count for entity_id, count in rows}


def _summary(movie: Movie, problem_count: int) -> MovieSummaryResponse:
    active = [
        release
        for release in movie.releases
        if release.release_state in {ReleaseState.CURRENT, ReleaseState.MISSING, ReleaseState.DUPLICATE, ReleaseState.CONFLICT}
    ]
    physical = [release for release in active if any(directory.exists for directory in release.directories)]
    if any(release.release_state == ReleaseState.DUPLICATE for release in active):
        state = "Duplicate"
    elif any(release.release_state == ReleaseState.CONFLICT for release in active):
        state = "Conflict"
    elif physical:
        state = "Present"
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
    confidence = None
    if current:
        raw_confidence = current.parse_snapshot.get("identity_confidence")
        confidence = float(raw_confidence) if raw_confidence is not None else None
    return MovieSummaryResponse(
        id=movie.id,
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
    )


def _release(release: MovieRelease) -> MovieReleaseResponse:
    confidence = release.parse_snapshot.get("identity_confidence")
    return MovieReleaseResponse(
        id=release.id,
        raw_release_name=release.raw_release_name,
        edition=release.effective_edition,
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
            )
            for directory in release.directories
        ],
        parse_snapshot=release.parse_snapshot,
    )
