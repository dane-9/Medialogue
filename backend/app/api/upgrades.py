from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import downloads as downloads_api
from app.api import indexers as indexers_api
from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import (
    MediaDirectory,
    MediaFile,
    MediaType,
    Movie,
    MovieRelease,
    Schedule,
    Season,
    Show,
    TorrentClientObservation,
    UpgradeCandidate,
)
from app.schemas.upgrades import (
    UpgradeCandidateResponse,
    UpgradeCleanupCommitRequest,
    UpgradeCleanupPreviewResponse,
    UpgradeCleanupResult,
    UpgradeExecuteRequest,
    UpgradeSearchResponse,
)
from app.services.scheduler import ensure_default_schedules, trigger_schedule
from app.services.upgrade_execution import (
    cleanup_preview,
    commit_cleanup,
    retry_upgrade_handoff,
    submit_upgrade_candidate,
)

router = APIRouter(tags=["upgrade candidates"])


async def _response(db: AsyncSession, row: UpgradeCandidate) -> UpgradeCandidateResponse:
    media_title = "Unknown media"
    media_year: int | None = None
    season_number: int | None = None
    current_paths: list[str] = []
    if row.media_type == MediaType.MOVIES:
        movie = await db.get(Movie, row.movie_id) if row.movie_id else None
        if movie is not None:
            media_title = movie.title
            media_year = movie.year
        release_ids = [UUID(value) for value in row.current_release_ids]
        if release_ids:
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
    else:
        season = await db.get(Season, row.season_id) if row.season_id else None
        show = await db.get(Show, season.show_id) if season is not None else None
        if show is not None:
            media_title = show.title
            media_year = show.year
        if season is not None:
            season_number = season.season_number
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

    progress = None
    download_state = None
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

    return UpgradeCandidateResponse(
        id=row.id,
        media_type=row.media_type,
        movie_id=row.movie_id,
        season_id=row.season_id,
        media_title=media_title,
        media_year=media_year,
        season_number=season_number,
        current_paths=current_paths,
        current_release_ids=[UUID(value) for value in row.current_release_ids],
        current_release_name=row.current_release_name,
        current_quality=row.current_quality,
        current_custom_format_score=row.current_custom_format_score,
        indexer_id=row.indexer_id,
        indexer_name=row.indexer_name,
        title=row.title,
        quality=row.quality,
        custom_format_score=row.custom_format_score,
        score_delta=row.score_delta,
        size=row.size,
        seeders=row.seeders,
        leechers=row.leechers,
        published_at=row.published_at,
        upgrade_types=list(row.upgrade_types or []),
        added_custom_formats=[dict(value) for value in row.added_custom_formats or []],
        execution_eligible=row.execution_eligible,
        execution_block_reason=row.execution_block_reason,
        comparison_snapshot=dict(row.comparison_snapshot or {}),
        execution_state=row.execution_state,
        selected_download_client_id=row.selected_download_client_id,
        selected_download_client_name=row.selected_download_client_name,
        selected_category=row.selected_category,
        torrent_id=row.torrent_id,
        download_progress=progress,
        download_state=download_state,
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
    statement = select(UpgradeCandidate)
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
    return [await _response(db, row) for row in rows]


@router.post("/upgrade-candidates/search", response_model=UpgradeSearchResponse, status_code=status.HTTP_202_ACCEPTED)
async def search_upgrade_candidates(
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> UpgradeSearchResponse:
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "search_media_upgrades"))
    if schedule is None:
        await ensure_default_schedules(db)
        await db.commit()
        schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "search_media_upgrades"))
    if schedule is None:
        raise AppError("UPGRADE_SEARCH_UNAVAILABLE", "Upgrade search task is unavailable.", status_code=503)
    job = await trigger_schedule(db, schedule, trigger="manual")
    return UpgradeSearchResponse(job_id=job.id)


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
