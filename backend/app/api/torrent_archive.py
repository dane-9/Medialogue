from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.integrations.qbittorrent import QBittorrentClient
from app.models.auth import AdminUser
from app.models.domain import Torrent, TorrentArchiveState, TorrentClientObservation
from app.schemas.common import Collection
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.torrent_archive import (
    TorrentArchiveDetail,
    TorrentArchiveSummary,
    TorrentRestoreRequest,
)
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job
from app.services.torrent_archive import (
    prepare_torrent_restore,
    read_manifest,
    run_torrent_archive_restore,
    run_torrent_archive_retry,
    select_archive_client,
)


router = APIRouter(tags=["torrent-archive"])


def get_torrent_archive_qbit_factory():
    return QBittorrentClient


def _manifest_primary(manifest: dict) -> dict:
    associations = manifest.get("media_associations")
    if isinstance(associations, list) and associations and isinstance(associations[0], dict):
        return associations[0]
    return {}


async def _summary(db: AsyncSession, torrent: Torrent) -> TorrentArchiveSummary:
    manifest = read_manifest(torrent)
    primary = _manifest_primary(manifest)
    observations = manifest.get("download_client_observations")
    original = observations[0] if isinstance(observations, list) and observations and isinstance(observations[0], dict) else {}
    qbit_present = bool(
        await db.scalar(
            select(func.count())
            .select_from(TorrentClientObservation)
            .where(TorrentClientObservation.torrent_id == torrent.id, TorrentClientObservation.is_present.is_(True))
        )
    )
    return TorrentArchiveSummary(
        id=torrent.id,
        info_hash=torrent.info_hash,
        torrent_name=torrent.name,
        total_size=torrent.total_size,
        archive_state=torrent.archive_state.value,
        archive_path=torrent.archive_path,
        manifest_path=torrent.manifest_path,
        manifest_schema_version=torrent.manifest_schema_version,
        first_seen_at=torrent.first_seen_at,
        last_seen_at=torrent.last_seen_at,
        completed_at=torrent.completed_at,
        media_type=primary.get("media_type") or manifest.get("media_type"),
        media_title=primary.get("title") or manifest.get("media_title"),
        tmdb_id=primary.get("tmdb_id") or manifest.get("tmdb_id"),
        tvdb_id=primary.get("tvdb_id") or manifest.get("tvdb_id"),
        release_name=primary.get("release_name") or manifest.get("release_name"),
        quality=primary.get("quality") or manifest.get("quality"),
        edition=primary.get("edition") if "edition" in primary else manifest.get("edition"),
        release_group=primary.get("release_group") or manifest.get("release_group"),
        original_download_client=original.get("download_client_name") or manifest.get("download_client_name"),
        previous_reported_path=manifest.get("previous_reported_path"),
        previous_resolved_path=manifest.get("previous_resolved_path"),
        qbit_present=qbit_present,
    )


@router.get("/torrent-archive", response_model=Collection[TorrentArchiveSummary])
async def list_torrent_archive(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    query: str | None = Query(None),
    archive_state: str | None = Query(None),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[TorrentArchiveSummary]:
    clauses = []
    if query:
        needle = f"%{query.strip()}%"
        clauses.append(or_(Torrent.name.ilike(needle), Torrent.info_hash.ilike(needle)))
    if archive_state:
        try:
            clauses.append(Torrent.archive_state == TorrentArchiveState(archive_state))
        except ValueError as exc:
            raise AppError("INVALID_ARCHIVE_STATE", "Unknown torrent archive state.", status_code=422) from exc

    count_query = select(func.count()).select_from(Torrent)
    rows_query = select(Torrent)
    if clauses:
        count_query = count_query.where(*clauses)
        rows_query = rows_query.where(*clauses)
    total = int(await db.scalar(count_query) or 0)
    torrents = (
        await db.scalars(
            rows_query.order_by(Torrent.first_seen_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return Collection(
        items=[await _summary(db, torrent) for torrent in torrents],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


@router.get("/torrent-archive/{torrent_id}", response_model=TorrentArchiveDetail)
async def get_torrent_archive_item(
    torrent_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TorrentArchiveDetail:
    torrent = await db.get(Torrent, torrent_id)
    if torrent is None:
        raise AppError("NOT_FOUND", "Torrent archive record was not found.", status_code=404)
    summary = await _summary(db, torrent)
    manifest = read_manifest(torrent)
    observations = manifest.get("download_client_observations")
    associations = manifest.get("media_associations")
    return TorrentArchiveDetail(
        **summary.model_dump(),
        manifest=manifest,
        observations=observations if isinstance(observations, list) else [],
        associations=associations if isinstance(associations, list) else [],
    )


@router.post(
    "/torrent-archive/{torrent_id}/retry",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_torrent_archive(
    torrent_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_torrent_archive_qbit_factory),
) -> JobAcceptedResponse:
    torrent = await db.get(Torrent, torrent_id)
    if torrent is None:
        raise AppError("NOT_FOUND", "Torrent archive record was not found.", status_code=404)
    client = await select_archive_client(db, torrent)
    if client is None:
        raise AppError(
            "TORRENT_NOT_AVAILABLE_IN_QBITTORRENT",
            "No enabled qBittorrent client currently has this torrent, so its .torrent file cannot be re-exported.",
            status_code=409,
        )
    job = await create_job(
        db,
        "torrent_archive_retry",
        summary={
            "torrent_id": str(torrent.id),
            "client_name": client.name,
            "message": f"Retrying recovery archive for {torrent.name}…",
        },
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(
        job.id,
        lambda: run_torrent_archive_retry(job.id, torrent.id, client_factory=client_factory),
    )
    return JobAcceptedResponse(job_id=job.id)


@router.post(
    "/torrent-archive/{torrent_id}/restore",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restore_archived_torrent(
    torrent_id: UUID,
    payload: TorrentRestoreRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_torrent_archive_qbit_factory),
) -> JobAcceptedResponse:
    prepared = await prepare_torrent_restore(db, torrent_id, payload.download_client_id, payload.save_path)
    torrent = prepared["torrent"]
    client = prepared["client"]
    job = await create_job(
        db,
        "torrent_restore",
        summary={
            "torrent_id": str(torrent.id),
            "client_name": client.name,
            "save_path": payload.save_path,
            "resolved_save_path": prepared["resolved_save_path"],
            "message": f"Submitting {torrent.name} to qBittorrent…",
        },
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(
        job.id,
        lambda: run_torrent_archive_restore(
            job.id,
            torrent_id,
            payload.download_client_id,
            payload.save_path,
            category=payload.category,
            tags=payload.tags,
            client_factory=client_factory,
        ),
    )
    return JobAcceptedResponse(job_id=job.id)
