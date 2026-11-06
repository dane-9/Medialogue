from __future__ import annotations

from datetime import datetime
from pathlib import PurePath, PurePosixPath

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import downloads as downloads_api
from app.api import indexers as indexers_api
from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import (
    AccessMode,
    Episode,
    MediaDirectory,
    MediaFile,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    ReleaseState,
    MediaProfileOverride,
    QualityProfile,
    Schedule,
    Season,
    Show,
    ShowReleaseTorrent,
    StorageRoot,
    Torrent,
    TorrentClientObservation,
    UpgradeCandidate,
)
from app.parser import parse_release
from app.services.format_breakdown import comparison_formats
from app.services.release_source import source_of_releases
from app.schemas.upgrades import (
    UpgradeCandidateCount,
    UpgradeCurrentRelease,
    UpgradeMatchedFormat,
    UpgradeCandidateResponse,
    UpgradeCleanupCommitRequest,
    UpgradeCleanupPreviewResponse,
    UpgradeCleanupResult,
    UpgradeExecuteRequest,
    UpgradeQualityProfileBulkUpdate,
    UpgradeQualityProfileUpdate,
    UpgradeQualityProfileUpdateResponse,
    UpgradeSearchResponse,
    UpgradeSearchResultResponse,
    UpgradeSearchResultsResponse,
    UpgradeSearchResultSelectRequest,
)
from app.services.events import create_event
from app.services.movie_editions import edition_label, edition_slot, same_edition
from app.services.quality_profiles import (
    get_assignment,
    load_effective_profile,
    refresh_movie_release_scores,
    refresh_show_release_scores,
)
from app.services.scheduler import ensure_default_schedules, trigger_schedule
from app.services.jobs import create_job
from app.services.runtime_jobs import launch_runtime_job
from app.services.upgrade_candidates import run_upgrade_search_job
from app.services.upgrade_execution import (
    cleanup_preview,
    commit_cleanup,
    retry_upgrade_handoff,
    run_automatic_cleanups,
    submit_upgrade_candidate,
)

router = APIRouter(tags=["upgrade candidates"])


def _indexer_color(indexer_id: UUID | None) -> str | None:
    """The badge colour configured for an indexer, if it still exists."""

    if indexer_id is None:
        return None
    for item in get_integration_config_store().list_indexers():
        if item.id == indexer_id:
            return item.color
    return None


async def _current_size(db: AsyncSession, row: UpgradeCandidate) -> int | None:
    """Size of the releases an upgrade replaces, from their torrents.

    Cross-seeds of one release share a payload, so each release counts its
    largest torrent once. Unknown when no current release has a torrent.
    """

    release_ids = [UUID(value) for value in row.current_release_ids or []]
    if not release_ids:
        return None
    if row.media_type == MediaType.MOVIES:
        link, column = MovieReleaseTorrent, MovieReleaseTorrent.movie_release_id
    else:
        link, column = ShowReleaseTorrent, ShowReleaseTorrent.show_release_id
    rows = (
        await db.execute(
            select(column, func.max(Torrent.total_size))
            .join(Torrent, Torrent.id == link.torrent_id)
            .where(column.in_(release_ids), Torrent.total_size.is_not(None))
            .group_by(column)
        )
    ).all()
    sizes = [int(size) for _release_id, size in rows if size]
    return sum(sizes) if sizes else None


async def _response(db: AsyncSession, row: UpgradeCandidate) -> UpgradeCandidateResponse:
    media_title = "Unknown media"
    media_year: int | None = None
    tmdb_id: int | None = None
    season_number: int | None = None
    current_episode_count: int | None = None
    season_episode_count: int | None = None
    current_paths: list[str] = []
    root_name: str | None = None
    poster_ref: str | None = None
    release_folder: str | None = None
    current_edition: str | None = None
    movie_has_multiple_editions = False
    profile_media_type = row.media_type
    profile_entity_id: UUID | None = None
    if row.media_type == MediaType.MOVIES:
        movie = await db.get(Movie, row.movie_id) if row.movie_id else None
        if movie is not None:
            profile_entity_id = movie.id
            media_title = movie.title
            media_year = movie.year
            tmdb_id = movie.tmdb_id
            poster_ref = movie.poster_ref
            edition_values = await db.scalars(
                select(MovieRelease.effective_edition).where(
                    MovieRelease.movie_id == movie.id,
                    MovieRelease.release_state == ReleaseState.CURRENT,
                )
            )
            movie_has_multiple_editions = len({edition_slot(value) for value in edition_values}) > 1
        release_ids = [UUID(value) for value in row.current_release_ids]
        if release_ids:
            current_edition = await db.scalar(
                select(MovieRelease.effective_edition).where(MovieRelease.id == release_ids[0])
            )
            current_paths = list(
                dict.fromkeys(
                    await db.scalars(
                        select(MediaDirectory.resolved_path).where(
                            MediaDirectory.movie_release_id.in_(release_ids),
                            MediaDirectory.exists.is_(True),
                        )
                    )
                )
            )
            root_name = await db.scalar(
                select(StorageRoot.name)
                .join(MediaDirectory, MediaDirectory.storage_root_id == StorageRoot.id)
                .where(MediaDirectory.movie_release_id.in_(release_ids))
                .limit(1)
            )
            if current_paths:
                release_folder = PurePath(current_paths[0]).name or None
    else:
        season = await db.get(Season, row.season_id) if row.season_id else None
        show = await db.get(Show, season.show_id) if season is not None else None
        if show is not None:
            profile_entity_id = show.id
            media_title = show.title
            media_year = show.year
            tmdb_id = show.tmdb_id
            poster_ref = show.poster_ref
        if season is not None:
            season_number = season.season_number
            # What the season holds, so a pack that fills gaps can be told
            # apart from one that swaps like for like.
            season_episode_count = int(
                await db.scalar(select(func.count()).select_from(Episode).where(Episode.season_id == season.id)) or 0
            ) or None
        release_ids = [UUID(value) for value in row.current_release_ids]
        if release_ids:
            # Show MediaDirectory is the Show container. Keep only paths that
            # actually contain one of the old release's mapped media files.
            from app.models.domain import EpisodeMediaMap
            current_paths = list(
                dict.fromkeys(
                    await db.scalars(
                        select(MediaDirectory.resolved_path)
                        .join(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
                        .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                        .where(EpisodeMediaMap.show_release_id.in_(release_ids), MediaFile.exists.is_(True))
                    )
                )
            )
            # The media folder this candidate belongs to, so the page can group
            # by folder the way New Media does.
            root_name = await db.scalar(
                select(StorageRoot.name)
                .join(MediaDirectory, MediaDirectory.storage_root_id == StorageRoot.id)
                .join(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .where(EpisodeMediaMap.show_release_id.in_(release_ids))
                .limit(1)
            )
            current_episode_count = int(
                await db.scalar(
                    select(func.count(func.distinct(EpisodeMediaMap.episode_id)))
                    .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
                    .where(EpisodeMediaMap.show_release_id.in_(release_ids), MediaFile.exists.is_(True))
                )
                or 0
            ) or None
            relative_paths = await db.scalars(
                select(MediaFile.relative_path)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .where(EpisodeMediaMap.show_release_id.in_(release_ids), MediaFile.exists.is_(True))
            )
            # Reported only when the whole season agrees on one folder, so a
            # season split across folders falls back to the show container
            # rather than showing whichever one happened to come first.
            folders = set()
            for value in relative_paths:
                parts = PurePosixPath(value.replace("\\", "/")).parts
                if len(parts) > 1:
                    folders.add(parts[0])
            release_folder = next(iter(folders)) if len(folders) == 1 else None

    progress = None
    download_state = None
    download_speed = None
    download_eta_seconds = None
    if row.torrent_id is not None and row.selected_download_client_id is not None:
        observation = await db.scalar(
            select(TorrentClientObservation).where(
                TorrentClientObservation.torrent_id == row.torrent_id,
                TorrentClientObservation.download_client_id == row.selected_download_client_id,
            )
        )
        if observation is not None:
            progress = float(observation.progress) if observation.progress is not None else None
            download_state = observation.state
            download_speed = int(observation.download_speed) if observation.download_speed is not None else None
            download_eta_seconds = int(observation.eta_seconds) if observation.eta_seconds is not None else None

    effective_profile = (
        await load_effective_profile(db, media_type=profile_media_type, entity_id=profile_entity_id)
        if profile_entity_id is not None
        else None
    )

    snapshot = dict(row.comparison_snapshot or {})
    candidate_snapshot = snapshot.get("candidate") or {}
    current_formats, candidate_formats = await comparison_formats(db, snapshot)
    current_ids = [UUID(value) for value in row.current_release_ids or []]
    source = await source_of_releases(
        db,
        movie_release_ids=current_ids if row.media_type == MediaType.MOVIES else (),
        show_release_ids=current_ids if row.media_type != MediaType.MOVIES else (),
    )
    candidate_edition = (
        candidate_snapshot.get("edition")
        if isinstance(candidate_snapshot, dict) and "edition" in candidate_snapshot
        else parse_release(row.title).edition
    )
    edition_matches = row.media_type != MediaType.MOVIES or same_edition(current_edition, candidate_edition)
    execution_eligible = bool(row.execution_eligible and edition_matches)
    execution_block_reason = row.execution_block_reason
    if not edition_matches:
        execution_block_reason = (
            "Edition mismatch: "
            f"{edition_label(current_edition)} cannot be replaced by {edition_label(candidate_edition)}. "
            "Search for upgrades again."
        )
    return UpgradeCandidateResponse(
        id=row.id,
        media_type=row.media_type,
        movie_id=row.movie_id,
        season_id=row.season_id,
        media_title=media_title,
        media_year=media_year,
        tmdb_id=tmdb_id,
        season_number=season_number,
        current_paths=current_paths,
        root_name=root_name,
        poster_ref=poster_ref,
        release_folder=release_folder,
        current_release_ids=[UUID(value) for value in row.current_release_ids],
        current_release_name=row.current_release_name,
        current_quality=row.current_quality,
        current_size=await _current_size(db, row),
        current_indexer=source.indexer_name,
        current_indexer_color=_indexer_color(source.indexer_id),
        current_tracker=source.tracker,
        current_edition=current_edition,
        movie_has_multiple_editions=movie_has_multiple_editions,
        current_custom_format_score=row.current_custom_format_score,
        quality_profile_id=(
            None if effective_profile is None or effective_profile.profile_inherited else effective_profile.profile_id
        ),
        quality_profile_name=effective_profile.profile_name if effective_profile else None,
        quality_profile_inherited=effective_profile.profile_inherited if effective_profile else True,
        indexer_id=row.indexer_id,
        indexer_name=row.indexer_name,
        indexer_color=_indexer_color(row.indexer_id),
        title=row.title,
        quality=row.quality,
        candidate_edition=candidate_edition,
        result_count=max(1, len(row.search_results or [])),
        custom_format_score=row.custom_format_score,
        score_delta=row.score_delta,
        size=row.size,
        seeders=row.seeders,
        leechers=row.leechers,
        published_at=row.published_at,
        upgrade_types=list(row.upgrade_types or []),
        added_custom_formats=[dict(value) for value in row.added_custom_formats or []],
        current_releases=[
            UpgradeCurrentRelease(
                release_id=str(value.get("release_id") or ""),
                release_name=str(value.get("release_name") or ""),
                quality=value.get("quality"),
                edition=value.get("edition"),
                custom_format_score=int(value.get("custom_format_score") or 0),
            )
            for value in ((snapshot.get("current") or {}).get("releases") or [])
            if isinstance(value, dict)
        ],
        current_episode_count=current_episode_count,
        season_episode_count=season_episode_count,
        current_matched_formats=[UpgradeMatchedFormat(**item) for item in current_formats],
        candidate_matched_formats=[UpgradeMatchedFormat(**item) for item in candidate_formats],
        execution_eligible=execution_eligible,
        execution_block_reason=execution_block_reason,
        comparison_snapshot=dict(row.comparison_snapshot or {}),
        execution_state=row.execution_state,
        selected_download_client_id=row.selected_download_client_id,
        selected_download_client_name=row.selected_download_client_name,
        selected_category=row.selected_category,
        torrent_id=row.torrent_id,
        download_progress=progress,
        download_state=download_state,
        download_speed=download_speed,
        download_eta_seconds=download_eta_seconds,
        execution_error=row.execution_error,
        submitted_at=row.submitted_at,
        validated_at=row.validated_at,
        old_torrents_removed_at=row.old_torrents_removed_at,
        cleanup_completed_at=row.cleanup_completed_at,
        last_searched_at=row.last_searched_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/upgrade-candidates", response_model=list[UpgradeCandidateResponse])
async def list_upgrade_candidates(
    media_type: MediaType | None = Query(default=None),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UpgradeCandidateResponse]:
    statement = select(UpgradeCandidate).where(~UpgradeCandidate.target_key.startswith("replace:"))
    if media_type is not None:
        statement = statement.where(UpgradeCandidate.media_type == media_type)
    rows = (
        await db.scalars(
            statement.order_by(
                UpgradeCandidate.execution_eligible.desc(),
                UpgradeCandidate.updated_at.desc(),
                UpgradeCandidate.title,
            )
        )
    ).all()
    replacing = await _titles_in_replace_media(db)
    return [await _response(db, row) for row in rows if not _left_to_replace_media(row, replacing)]


async def _titles_in_replace_media(db: AsyncSession) -> set[str]:
    """Movies and seasons Replace Media is handling (lost source, not ignored).

    A title whose source is lost is fixed there: its search accepts anything
    equal or better, so a better release arrives as the replacement, and two
    downloads for one title would race to retire the same old release.
    """

    from app.services.replacements import replacement_queue

    return {
        key
        for item in await replacement_queue(db)
        if not item["dismissed"]
        for key in (item.get("movie_id"), item.get("season_id"))
        if key
    }


def _left_to_replace_media(row: UpgradeCandidate, replacing: set[str]) -> bool:
    """Whether Upgrade Media leaves this row to Replace Media.

    An upgrade already under way stays, so its progress is never hidden.
    """

    if row.execution_state:
        return False
    return str(row.movie_id or "") in replacing or str(row.season_id or "") in replacing


def _search_result_rows(row: UpgradeCandidate) -> list[dict[str, Any]]:
    return [dict(value) for value in (row.search_results or []) if isinstance(value, dict)]


def _search_result_response(row: UpgradeCandidate, value: dict[str, Any], *, position: int) -> UpgradeSearchResultResponse:
    indexer_id = UUID(str(value["indexer_id"])) if value.get("indexer_id") else None
    snapshot = value.get("snapshot") or {}
    candidate = snapshot.get("candidate") if isinstance(snapshot, dict) else {}
    formats = candidate.get("formats") if isinstance(candidate, dict) else []
    matched_format_names = [
        str(item["custom_format_name"])
        for item in (formats or [])
        if isinstance(item, dict) and item.get("matched") and item.get("custom_format_name")
    ]
    selected = (
        str(value.get("guid") or "") == row.guid
        and indexer_id == row.indexer_id
        and str(value.get("title") or "") == row.title
    )
    return UpgradeSearchResultResponse(
        id=UUID(str(value.get("id") or row.id)),
        indexer_id=indexer_id,
        indexer_name=str(value.get("indexer_name") or "Unknown indexer"),
        indexer_color=_indexer_color(indexer_id),
        title=str(value.get("title") or "Untitled result"),
        quality=value.get("quality"),
        edition=value.get("edition"),
        release_group=value.get("release_group"),
        custom_format_score=value.get("custom_format_score"),
        size=value.get("size"),
        seeders=value.get("seeders"),
        leechers=value.get("leechers"),
        published_at=value.get("published_at"),
        eligible=bool(value.get("eligible")),
        rejection_reasons=[str(reason) for reason in value.get("rejection_reasons") or []],
        upgrade_types=[str(reason) for reason in value.get("upgrade_types") or []],
        matched_format_names=matched_format_names,
        parser=dict(value.get("parser") or {}),
        recommended=position == 0 and bool(value.get("eligible")),
        selected=selected,
    )


@router.get("/upgrade-candidates/{candidate_id}/results", response_model=UpgradeSearchResultsResponse)
async def upgrade_search_results(
    candidate_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UpgradeSearchResultsResponse:
    row = await db.get(UpgradeCandidate, candidate_id)
    if row is None:
        raise AppError("NOT_FOUND", "Upgrade candidate was not found.", status_code=404)
    values = _search_result_rows(row)
    return UpgradeSearchResultsResponse(
        candidate_id=row.id,
        searched_at=row.last_searched_at,
        results=[_search_result_response(row, value, position=index) for index, value in enumerate(values)],
    )


@router.post("/upgrade-candidates/{candidate_id}/results/select", response_model=UpgradeCandidateResponse)
async def select_upgrade_search_result(
    candidate_id: UUID,
    payload: UpgradeSearchResultSelectRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> UpgradeCandidateResponse:
    row = await db.get(UpgradeCandidate, candidate_id)
    if row is None:
        raise AppError("NOT_FOUND", "Upgrade candidate was not found.", status_code=404)
    if row.execution_state is not None:
        raise AppError("UPGRADE_ALREADY_STARTED", "The selected result cannot change after an upgrade starts.", status_code=409)
    value = next((item for item in _search_result_rows(row) if str(item.get("id")) == str(payload.result_id)), None)
    if value is None:
        raise AppError("UPGRADE_RESULT_NOT_FOUND", "That cached search result is no longer available.", status_code=404)
    if not value.get("eligible"):
        reasons = " ".join(str(reason) for reason in value.get("rejection_reasons") or [])
        raise AppError("UPGRADE_RESULT_REJECTED", reasons or "That result does not qualify as an upgrade.", status_code=409)
    snapshot = dict(value.get("snapshot") or {})
    current = snapshot.get("current") or {}
    row.indexer_id = UUID(str(value["indexer_id"])) if value.get("indexer_id") else None
    row.indexer_name = str(value.get("indexer_name") or row.indexer_name)
    row.guid = str(value.get("guid") or value.get("title") or row.guid)
    row.download_url = value.get("download_url")
    row.title = str(value.get("title") or row.title)
    row.quality = value.get("quality")
    row.custom_format_score = int(value.get("custom_format_score") or 0)
    row.score_delta = row.custom_format_score - int(current.get("custom_format_score") or row.current_custom_format_score)
    row.size = value.get("size")
    row.seeders = value.get("seeders")
    row.leechers = value.get("leechers")
    published = value.get("published_at")
    row.published_at = datetime.fromisoformat(published) if isinstance(published, str) else published
    row.upgrade_types = [str(reason) for reason in value.get("upgrade_types") or []]
    row.added_custom_formats = [dict(item) for item in value.get("added_custom_formats") or []]
    row.comparison_snapshot = snapshot
    # Choosing a different release changes only the recommendation. Target-level
    # guards (for example a profile that is awaiting re-evaluation) must survive.
    await create_event(
        db,
        "upgrade.result_selected",
        entity_type="upgrade_candidate",
        entity_id=row.id,
        message=f"Selected alternate upgrade result {row.title}.",
        details={"candidate_id": str(row.id), "result_id": str(payload.result_id)},
    )
    await db.commit()
    return await _response(db, row)


@router.get("/upgrade-candidates/count", response_model=UpgradeCandidateCount)
async def count_upgrade_candidates(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UpgradeCandidateCount:
    """Candidates still awaiting a decision.

    One already submitted is being acted on, and one that cannot be submitted is
    not a decision to make, so neither belongs in a badge that should reach zero
    once the queue has been worked through.
    """

    pending = (
        await db.scalars(
            select(UpgradeCandidate)
            .where(
                ~UpgradeCandidate.target_key.startswith("replace:"),
                UpgradeCandidate.execution_state.is_(None),
                UpgradeCandidate.execution_eligible.is_(True),
            )
        )
    ).all()
    replacing = await _titles_in_replace_media(db) if pending else set()
    count = sum(1 for row in pending if not _left_to_replace_media(row, replacing))
    read_only = list(
        await db.scalars(
            select(StorageRoot.name)
            .where(StorageRoot.access_mode == AccessMode.READ_ONLY)
            .order_by(StorageRoot.name)
        )
    )
    return UpgradeCandidateCount(count=count, read_only_roots=read_only)


@router.post("/upgrade-candidates/search", response_model=UpgradeSearchResponse, status_code=status.HTTP_202_ACCEPTED)
async def search_upgrade_candidates(
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    movie_id: UUID | None = Query(default=None),
    show_id: UUID | None = Query(default=None),
    client_factory=Depends(indexers_api.get_torznab_client_factory),
) -> UpgradeSearchResponse:
    if movie_id is not None and show_id is not None:
        raise AppError("INVALID_UPGRADE_TARGET", "Choose either a Movie or a Show to check for upgrades, not both.", status_code=422)
    if movie_id is not None or show_id is not None:
        if movie_id is not None and await db.get(Movie, movie_id) is None:
            raise AppError("MOVIE_NOT_FOUND", "Movie was not found.", status_code=404)
        if show_id is not None and await db.get(Show, show_id) is None:
            raise AppError("SHOW_NOT_FOUND", "Show was not found.", status_code=404)
        target = {"movie_id": str(movie_id)} if movie_id is not None else {"show_id": str(show_id)}
        job = await create_job(
            db,
            "search_media_upgrades",
            cancellable=False,
            summary={"trigger": "manual", "target": target},
        )
        await db.commit()
        launch_runtime_job(
            job.id,
            lambda: run_upgrade_search_job(
                job.id,
                client_factory=client_factory,
                movie_id=movie_id,
                show_id=show_id,
            ),
        )
        return UpgradeSearchResponse(job_id=job.id)
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "search_media_upgrades"))
    if schedule is None:
        await ensure_default_schedules(db)
        await db.commit()
        schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "search_media_upgrades"))
    if schedule is None:
        raise AppError("UPGRADE_SEARCH_UNAVAILABLE", "Upgrade search task is unavailable.", status_code=503)
    job = await trigger_schedule(db, schedule, trigger="manual")
    return UpgradeSearchResponse(job_id=job.id)


async def _profile_targets(
    db: AsyncSession,
    candidate_ids: list[UUID],
) -> tuple[list[UpgradeCandidate], dict[tuple[MediaType, UUID], Movie | Show]]:
    rows = list((await db.scalars(select(UpgradeCandidate).where(UpgradeCandidate.id.in_(candidate_ids)))).all())
    if len(rows) != len(set(candidate_ids)):
        raise AppError("UPGRADE_CANDIDATE_NOT_FOUND", "One or more upgrade candidates were not found.", status_code=404)
    if any(row.execution_state is not None for row in rows):
        raise AppError(
            "UPGRADE_ALREADY_STARTED",
            "A Quality Profile cannot be changed after an upgrade has started.",
            status_code=409,
        )

    targets: dict[tuple[MediaType, UUID], Movie | Show] = {}
    for row in rows:
        if row.media_type == MediaType.MOVIES:
            target = await db.get(Movie, row.movie_id) if row.movie_id else None
        else:
            season = await db.get(Season, row.season_id) if row.season_id else None
            target = await db.get(Show, season.show_id) if season is not None else None
        if target is None:
            raise AppError("UPGRADE_MEDIA_NOT_FOUND", "The media for an upgrade candidate was not found.", status_code=404)
        targets[(row.media_type, target.id)] = target
    return rows, targets


async def _queue_upgrade_search(db: AsyncSession, *, trigger: str):
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "search_media_upgrades"))
    if schedule is None:
        await ensure_default_schedules(db)
        await db.commit()
        schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "search_media_upgrades"))
    if schedule is None:
        raise AppError("UPGRADE_SEARCH_UNAVAILABLE", "Upgrade search task is unavailable.", status_code=503)
    return await trigger_schedule(db, schedule, trigger=trigger)


async def _set_upgrade_quality_profile(
    db: AsyncSession,
    *,
    candidate_ids: list[UUID],
    quality_profile_id: UUID | None,
) -> UpgradeQualityProfileUpdateResponse:
    profile = await db.get(QualityProfile, quality_profile_id) if quality_profile_id else None
    if quality_profile_id is not None and profile is None:
        raise AppError("INVALID_QUALITY_PROFILE", "Quality Profile does not exist.", status_code=422)

    rows, targets = await _profile_targets(db, candidate_ids)
    changed = 0
    for (media_type, entity_id), _target in targets.items():
        assignment = await get_assignment(db, media_type=media_type, entity_id=entity_id, for_update=True)
        if assignment is None:
            if quality_profile_id is None:
                continue
            assignment = MediaProfileOverride(
                media_type=media_type,
                movie_id=entity_id if media_type == MediaType.MOVIES else None,
                show_id=entity_id if media_type == MediaType.SHOWS else None,
                quality_profile_id=quality_profile_id,
                override_definition={},
                revision=1,
            )
            db.add(assignment)
        elif assignment.quality_profile_id != quality_profile_id:
            assignment.quality_profile_id = quality_profile_id
            assignment.revision += 1
        else:
            continue
        await db.flush()
        if media_type == MediaType.MOVIES:
            await refresh_movie_release_scores(db, entity_id)
        else:
            await refresh_show_release_scores(db, entity_id)
        await create_event(
            db,
            "quality_profile.assignment_updated",
            entity_type="movie" if media_type == MediaType.MOVIES else "show",
            entity_id=entity_id,
            message="Quality Profile assignment was changed from Upgrade Media.",
            details={
                "quality_profile_id": str(quality_profile_id) if quality_profile_id else None,
                "source": "upgrade_media",
                "overrides_preserved": True,
            },
        )
        changed += 1

    affected_keys = set(targets)
    all_candidates = (await db.scalars(select(UpgradeCandidate).where(UpgradeCandidate.execution_state.is_(None)))).all()
    affected_ids: list[UUID] = []
    season_show_ids: dict[UUID, UUID] = {}
    for candidate in all_candidates:
        if candidate.media_type == MediaType.MOVIES and candidate.movie_id is not None:
            key = (MediaType.MOVIES, candidate.movie_id)
        elif candidate.season_id is not None:
            show_id = season_show_ids.get(candidate.season_id)
            if show_id is None:
                season = await db.get(Season, candidate.season_id)
                show_id = season.show_id if season else None
                if show_id is not None:
                    season_show_ids[candidate.season_id] = show_id
            key = (MediaType.SHOWS, show_id) if show_id is not None else None
        else:
            key = None
        if key not in affected_keys:
            continue
        candidate.execution_eligible = False
        candidate.execution_block_reason = "Quality Profile changed; waiting for upgrade candidates to be re-evaluated."
        affected_ids.append(candidate.id)

    await db.commit()
    job = await _queue_upgrade_search(db, trigger="quality_profile_changed")
    return UpgradeQualityProfileUpdateResponse(
        job_id=job.id,
        candidate_ids=affected_ids or [row.id for row in rows],
        quality_profile_id=quality_profile_id,
        quality_profile_name=profile.name if profile else None,
        updated_entities=changed,
    )


@router.patch(
    "/upgrade-candidates/{candidate_id}/quality-profile",
    response_model=UpgradeQualityProfileUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def set_upgrade_quality_profile(
    candidate_id: UUID,
    payload: UpgradeQualityProfileUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> UpgradeQualityProfileUpdateResponse:
    return await _set_upgrade_quality_profile(
        db,
        candidate_ids=[candidate_id],
        quality_profile_id=payload.quality_profile_id,
    )


@router.post(
    "/upgrade-candidates/quality-profile",
    response_model=UpgradeQualityProfileUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def set_upgrade_quality_profiles(
    payload: UpgradeQualityProfileBulkUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> UpgradeQualityProfileUpdateResponse:
    return await _set_upgrade_quality_profile(
        db,
        candidate_ids=payload.candidate_ids,
        quality_profile_id=payload.quality_profile_id,
    )


@router.post("/upgrade-candidates/{candidate_id}/upgrade", response_model=UpgradeCandidateResponse)
async def execute_upgrade(
    candidate_id: UUID,
    payload: UpgradeExecuteRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    torznab_factory=Depends(indexers_api.get_torznab_client_factory),
    qbittorrent_client_factory=Depends(downloads_api.get_qbittorrent_client_factory),
) -> UpgradeCandidateResponse:
    row = await submit_upgrade_candidate(
        db,
        candidate_id,
        download_client_id=payload.download_client_id,
        category=payload.category,
        delete_old_media=payload.delete_old_media,
        torznab_client_factory=torznab_factory,
        qbittorrent_client_factory=qbittorrent_client_factory,
    )
    await db.commit()
    return await _response(db, row)


@router.post("/upgrade-candidates/{candidate_id}/retry-handoff", response_model=UpgradeCandidateResponse)
async def retry_handoff(
    candidate_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    qbittorrent_client_factory=Depends(downloads_api.get_qbittorrent_client_factory),
) -> UpgradeCandidateResponse:
    row = await retry_upgrade_handoff(db, candidate_id, qbittorrent_client_factory=qbittorrent_client_factory)
    await db.commit()
    if await run_automatic_cleanups(db):
        await db.refresh(row)
    return await _response(db, row)


@router.get("/upgrade-candidates/{candidate_id}/cleanup-preview", response_model=UpgradeCleanupPreviewResponse)
async def preview_cleanup(
    candidate_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UpgradeCleanupPreviewResponse:
    return UpgradeCleanupPreviewResponse.model_validate(await cleanup_preview(db, candidate_id))


@router.post("/upgrade-candidates/{candidate_id}/cleanup", response_model=UpgradeCleanupResult)
async def execute_cleanup(
    candidate_id: UUID,
    payload: UpgradeCleanupCommitRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> UpgradeCleanupResult:
    result = await commit_cleanup(db, candidate_id, payload.confirmation_token)
    await db.commit()
    return UpgradeCleanupResult.model_validate(result)
