from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.integrations.qbittorrent import QBittorrentClient
from app.models.auth import AdminUser
from app.models.domain import (
    DownloadClient,
    IntegrationType,
    MediaType,
    RemotePathMapping,
    StorageRoot,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
)
from app.schemas.common import Collection
from app.schemas.torrent_archive import (
    TorrentArchiveDetail,
    TorrentArchiveRetryResponse,
    TorrentArchiveSummary,
    TorrentRestoreRequest,
    TorrentRestoreResponse,
)
from app.services.events import create_event
from app.services.qbittorrent import resolve_remote_path
from app.services.torrent_archive import (
    ensure_torrent_archived,
    read_manifest,
    select_archive_client,
)


router = APIRouter(tags=["torrent-archive"])


def get_torrent_archive_qbit_factory():
    return QBittorrentClient


def _inside(path: str, root: str) -> bool:
    try:
        Path(path).resolve(strict=False).relative_to(Path(root).resolve(strict=False))
        return True
    except ValueError:
        return False


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


@router.post("/torrent-archive/{torrent_id}/retry", response_model=TorrentArchiveRetryResponse)
async def retry_torrent_archive(
    torrent_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_torrent_archive_qbit_factory),
) -> TorrentArchiveRetryResponse:
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
    success = await ensure_torrent_archived(db, torrent, client, client_factory=client_factory)
    await db.commit()
    return TorrentArchiveRetryResponse(
        torrent_id=torrent.id,
        archive_state=torrent.archive_state.value,
        archive_path=torrent.archive_path,
        manifest_path=torrent.manifest_path,
        message=None if success else str((torrent.metadata_json or {}).get("archive_error") or "Archive failed."),
    )


@router.post(
    "/torrent-archive/{torrent_id}/restore",
    response_model=TorrentRestoreResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restore_archived_torrent(
    torrent_id: UUID,
    payload: TorrentRestoreRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_torrent_archive_qbit_factory),
) -> TorrentRestoreResponse:
    torrent = await db.get(Torrent, torrent_id)
    if torrent is None:
        raise AppError("NOT_FOUND", "Torrent archive record was not found.", status_code=404)
    if torrent.archive_state != TorrentArchiveState.ARCHIVED or not torrent.archive_path:
        raise AppError("TORRENT_NOT_ARCHIVED", "This torrent does not have a complete recovery archive.", status_code=409)
    archive_path = Path(torrent.archive_path)
    if not archive_path.is_file():
        raise AppError("TORRENT_ARCHIVE_FILE_MISSING", "The archived .torrent file is missing from the archive mount.", status_code=409)

    client = await db.get(DownloadClient, payload.download_client_id)
    if client is None or not client.enabled:
        raise AppError("DOWNLOAD_CLIENT_NOT_FOUND", "The selected qBittorrent client is unavailable or disabled.", status_code=404)

    manifest = read_manifest(torrent)
    raw_media_type = str(manifest.get("media_type") or "").lower()
    if raw_media_type in {"movie", "movies"}:
        media_type = MediaType.MOVIES
    elif raw_media_type in {"show", "shows", "tv"}:
        media_type = MediaType.SHOWS
    else:
        media_type = client.scope
    if client.scope != media_type:
        raise AppError(
            "DOWNLOAD_CLIENT_SCOPE_MISMATCH",
            f"This archive belongs to {media_type.value}, but the selected client is scoped to {client.scope.value}.",
            status_code=409,
        )

    mappings = (
        await db.scalars(
            select(RemotePathMapping).where(
                RemotePathMapping.enabled.is_(True),
                RemotePathMapping.integration_type == IntegrationType.QBITTORRENT,
                (RemotePathMapping.integration_id == client.id) | RemotePathMapping.integration_id.is_(None),
            )
        )
    ).all()
    resolved_save_path, _ = resolve_remote_path(payload.save_path, mappings)
    roots = (
        await db.scalars(
            select(StorageRoot).where(StorageRoot.enabled.is_(True), StorageRoot.media_type == client.scope)
        )
    ).all()
    if not resolved_save_path or not any(_inside(resolved_save_path, root.resolved_root_path) for root in roots):
        raise AppError(
            "RESTORE_PATH_OUTSIDE_CONFIGURED_ROOTS",
            "The restore destination must resolve inside an enabled storage root for this qBittorrent client scope.",
            status_code=422,
        )

    adapter = client_factory(client.url, client.username or "", client.password or "")
    try:
        await adapter.add_torrent(
            archive_path.read_bytes(),
            filename=f"{torrent.info_hash}.torrent",
            save_path=payload.save_path,
            category=payload.category if payload.category is not None else client.category,
            tags=tuple(payload.tags if payload.tags is not None else (client.tags or [])),
        )
    except Exception as exc:
        raise AppError("QBITTORRENT_RESTORE_FAILED", f"qBittorrent rejected the archived torrent: {exc}", status_code=503) from exc
    finally:
        await adapter.close()

    await create_event(
        db,
        "torrent.restored",
        entity_type="torrent",
        entity_id=torrent.id,
        message=f"Archived torrent submitted to qBittorrent client {client.name}.",
        details={
            "download_client_id": str(client.id),
            "download_client_name": client.name,
            "reported_save_path": payload.save_path,
            "resolved_save_path": resolved_save_path,
            "info_hash": torrent.info_hash,
        },
    )
    await db.commit()
    return TorrentRestoreResponse(
        torrent_id=torrent.id,
        download_client_id=client.id,
        client_name=client.name,
        info_hash=torrent.info_hash,
        save_path=payload.save_path,
        resolved_save_path=resolved_save_path,
    )
