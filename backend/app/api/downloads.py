from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.core.integration_config import DownloadClientConfig, get_integration_config_store
from app.db.session import get_db
from app.integrations.qbittorrent import QBittorrentClient
from app.models.auth import AdminUser
from app.models.domain import (
    AssociationType,
    DownloadClient,
    MediaType,
    MovieRelease,
    MovieReleaseTorrent,
    Problem,
    ProblemStatus,
    Torrent,
    TorrentClientObservation,
)
from app.schemas.common import Collection, DeleteResponse
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.downloads import (
    DownloadClientCreate,
    DownloadClientResponse,
    DownloadClientSavedTestRequest,
    DownloadClientTestResponse,
    DownloadClientTestRequest,
    DownloadClientUpdate,
    DownloadResponse,
    DownloadScope,
)
from app.services.integration_state import ConfiguredDownloadClient, get_configured_download_client, list_configured_download_clients
from app.services.qbittorrent import (
    run_all_download_client_polls,
    run_download_client_poll,
    test_download_client_connection,
)
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job


router = APIRouter(tags=["downloads"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_qbittorrent_client_factory():
    return QBittorrentClient


get_qbit_client_factory = get_qbittorrent_client_factory


def _client_response(client: ConfiguredDownloadClient) -> DownloadClientResponse:
    return DownloadClientResponse(
        id=client.id,
        name=client.name,
        url=client.url,
        username=client.username,
        password_configured=bool(client.password),
        scope=DownloadScope(client.scope.value),
        category=client.category,
        tags=list(client.tags or []),
        enabled=client.enabled,
        health=client.health,
        last_health_checked_at=client.last_health_checked_at,
        last_success_at=client.last_success_at,
        latency_ms=client.latency_ms,
        last_error=client.last_error,
        revision=client.revision,
        poll_interval_seconds=client.poll_interval_seconds,
        recent_priority=client.recent_priority,
        older_priority=client.older_priority,
        sequential_order=client.sequential_order,
        first_last_first=client.first_last_first,
        content_layout=client.content_layout,
        completed_download_handling=client.completed_download_handling,
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


async def _download_response(
    db: AsyncSession,
    observation: TorrentClientObservation,
    torrent: Torrent,
    client: ConfiguredDownloadClient,
) -> DownloadResponse:
    association_row = (
        await db.execute(
            select(MovieReleaseTorrent, MovieRelease)
            .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
            .options(
                selectinload(MovieRelease.quality_definition),
                selectinload(MovieRelease.directories),
            )
            .where(MovieReleaseTorrent.torrent_id == torrent.id)
            .order_by(MovieReleaseTorrent.created_at.desc())
            .limit(1)
        )
    ).first()
    association, release = association_row if association_row else (None, None)
    media_state = None
    reconciliation_state = None
    reconciliation_detail = None
    if release is not None:
        media_state = "present" if any(item.exists for item in release.directories) else "missing"
        reconciliation_state = association.association_type.value
        problem = await db.scalar(
            select(Problem).where(
                Problem.entity_type == "movie_release",
                Problem.entity_id == release.id,
                Problem.status == ProblemStatus.OPEN,
            ).order_by(Problem.created_at.desc())
        )
        if problem is not None:
            reconciliation_state = "conflict"
            reconciliation_detail = problem.message
    return DownloadResponse(
        id=observation.id,
        torrent_id=torrent.id,
        client_id=client.id,
        client_name=client.name,
        scope=DownloadScope(client.scope.value),
        info_hash=torrent.info_hash,
        name=torrent.name,
        total_size=torrent.total_size,
        reported_save_path=observation.reported_save_path,
        resolved_save_path=observation.resolved_save_path,
        state=observation.state,
        progress=float(observation.progress) if observation.progress is not None else None,
        category=observation.category,
        tags=list(observation.tags or []),
        is_present=observation.is_present,
        first_seen_at=observation.first_seen_at,
        last_seen_at=observation.last_seen_at,
        removed_at=observation.removed_at,
        completed_at=torrent.completed_at,
        movie_id=release.movie_id if release else None,
        quality=release.quality_definition.name if release and release.quality_definition else None,
        edition=release.effective_edition if release else None,
        media_state=media_state,
        reconciliation_state=reconciliation_state,
        reconciliation_detail=reconciliation_detail,
        incoming=bool(association and association.association_type == AssociationType.INCOMING),
        incoming_kind=(
            str(release.parse_snapshot.get("incoming_kind") or "release")
            if release and association and association.association_type == AssociationType.INCOMING
            else None
        ),
    )


@router.get("/download-clients", response_model=Collection[DownloadClientResponse])
async def list_download_clients(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[DownloadClientResponse]:
    rows = sorted(await list_configured_download_clients(db), key=lambda item: item.name.casefold())
    total = len(rows)
    start = (page - 1) * page_size
    rows = rows[start : start + page_size]
    return Collection(
        items=[_client_response(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


@router.post("/download-clients/test", response_model=DownloadClientTestResponse)
async def test_unsaved_download_client(
    payload: DownloadClientTestRequest,
    _: object = Depends(require_csrf),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> DownloadClientTestResponse:
    started = perf_counter()
    adapter = client_factory(str(payload.url).rstrip("/"), payload.username, payload.password)
    try:
        result = await adapter.health()
        return DownloadClientTestResponse(
            status="healthy",
            version=result.get("version"),
            latency_ms=round((perf_counter() - started) * 1000),
        )
    except Exception as exc:
        return DownloadClientTestResponse(status="unavailable", message=str(exc))
    finally:
        await adapter.close()


@router.post("/download-clients", response_model=DownloadClientResponse, status_code=status.HTTP_201_CREATED)
async def create_download_client(
    payload: DownloadClientCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DownloadClientResponse:
    config = get_integration_config_store().save_download_client(
        DownloadClientConfig(
            id=uuid4(),
            name=payload.name,
            url=str(payload.url).rstrip("/"),
            username=payload.username,
            password=payload.password,
            scope=payload.scope.value,
            category=payload.category,
            tags=list(payload.tags),
            enabled=payload.enabled,
            poll_interval_seconds=payload.poll_interval_seconds,
            recent_priority=payload.recent_priority,
            older_priority=payload.older_priority,
            sequential_order=payload.sequential_order,
            first_last_first=payload.first_last_first,
            content_layout=payload.content_layout,
            completed_download_handling=payload.completed_download_handling,
        )
    )
    db.add(DownloadClient(id=config.id))
    await db.commit()
    client = await get_configured_download_client(db, config.id)
    assert client is not None
    return _client_response(client)


@router.get("/download-clients/{client_id}", response_model=DownloadClientResponse)
async def get_download_client(
    client_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DownloadClientResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    return _client_response(client)


@router.put("/download-clients/{client_id}", response_model=DownloadClientResponse)
@router.patch("/download-clients/{client_id}", response_model=DownloadClientResponse, include_in_schema=False)
async def update_download_client(
    client_id: UUID,
    payload: DownloadClientUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DownloadClientResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    current = client.config
    values = payload.model_dump(exclude_unset=True)
    values.pop("expected_revision", None)
    password = values.pop("password", None)
    updated = DownloadClientConfig(
        id=current.id,
        name=values.get("name", current.name),
        url=(str(values["url"]).rstrip("/") if values.get("url") is not None else current.url),
        username=values.get("username", current.username),
        password=(password or current.password),
        scope=(values["scope"].value if values.get("scope") is not None else current.scope),
        category=values.get("category", current.category),
        tags=(list(values["tags"]) if values.get("tags") is not None else list(current.tags)),
        enabled=(values["enabled"] if values.get("enabled") is not None else current.enabled),
        revision=current.revision,
        poll_interval_seconds=(values["poll_interval_seconds"] if values.get("poll_interval_seconds") is not None else current.poll_interval_seconds),
        recent_priority=values.get("recent_priority", current.recent_priority),
        older_priority=values.get("older_priority", current.older_priority),
        sequential_order=values.get("sequential_order", current.sequential_order),
        first_last_first=values.get("first_last_first", current.first_last_first),
        content_layout=values.get("content_layout", current.content_layout),
        completed_download_handling=values.get("completed_download_handling", current.completed_download_handling),
    )
    try:
        get_integration_config_store().save_download_client(updated, expected_revision=payload.expected_revision)
    except ValueError as exc:
        if str(exc) == "revision_conflict":
            raise AppError("REVISION_CONFLICT", "Download client changed; refresh and try again.", status_code=409) from exc
        raise
    client.health = "unknown"
    client.last_polled_at = None
    client.last_health_checked_at = None
    client.last_success_at = None
    client.latency_ms = None
    client.last_error = None
    await db.commit()
    refreshed = await get_configured_download_client(db, client_id)
    assert refreshed is not None
    return _client_response(refreshed)


@router.delete("/download-clients/{client_id}", response_model=DeleteResponse)
async def delete_download_client(
    client_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    # Cascade removes only observation rows; durable Torrent history remains.
    get_integration_config_store().delete_download_client(client_id)
    await db.delete(client.state)
    await db.commit()
    return DeleteResponse(id=client_id)


@router.post("/download-clients/{client_id}/test", response_model=DownloadClientTestResponse)
async def test_download_client(
    client_id: UUID,
    payload: DownloadClientSavedTestRequest | None = None,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> DownloadClientTestResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    client.last_health_checked_at = utcnow()
    test_url = str(payload.url).rstrip("/") if payload and payload.url is not None else client.url
    test_username = payload.username if payload and payload.username is not None else (client.username or "")
    test_password = payload.password if payload and payload.password else (client.password or "")
    testing_saved_configuration = (
        test_url == client.url
        and test_username == (client.username or "")
        and test_password == (client.password or "")
    )
    try:
        started = perf_counter()
        adapter = client_factory(test_url, test_username, test_password)
        try:
            health = await adapter.health()
            result = {
                "status": "healthy",
                "version": health.get("version"),
                "latency_ms": round((perf_counter() - started) * 1000),
            }
        finally:
            await adapter.close()
    except Exception as exc:
        # Only persist health against the stored configuration. A test using
        # unsaved overrides is diagnostic and must not make the saved client
        # appear broken.
        if testing_saved_configuration:
            client.health = "unavailable"
            client.last_error = str(exc)
            client.latency_ms = None
        await db.commit()
        return DownloadClientTestResponse(status="unavailable", message=str(exc))
    if testing_saved_configuration:
        client.health = "healthy"
        client.last_success_at = utcnow()
        client.last_error = None
        client.latency_ms = int(result.get("latency_ms") or 0)
    await db.commit()
    return DownloadClientTestResponse(**result)


@router.get("/download-clients/{client_id}/health", response_model=DownloadClientTestResponse)
async def download_client_health(
    client_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DownloadClientTestResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    return DownloadClientTestResponse(status=client.health or "unknown")


@router.post("/download-clients/{client_id}/health/refresh", response_model=DownloadClientTestResponse)
async def refresh_download_client_health(
    client_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> DownloadClientTestResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    client.last_health_checked_at = utcnow()
    try:
        result = await test_download_client_connection(client, client_factory=client_factory)
    except Exception as exc:
        client.health = "unavailable"
        client.last_error = str(exc)
        client.latency_ms = None
        await db.commit()
        return DownloadClientTestResponse(status="unavailable", message=str(exc))
    client.health = "healthy"
    client.last_success_at = utcnow()
    client.last_error = None
    client.latency_ms = int(result.get("latency_ms") or 0)
    await db.commit()
    return DownloadClientTestResponse(**result)


@router.post("/download-clients/{client_id}/poll", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def poll_client(
    client_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> JobAcceptedResponse:
    client = await get_configured_download_client(db, client_id)
    if client is None:
        raise AppError("NOT_FOUND", "Download client was not found.", status_code=404)
    if not client.enabled:
        raise AppError("DOWNLOAD_CLIENT_DISABLED", "Download client is disabled.", status_code=409)
    job = await create_job(
        db,
        "qbittorrent_poll",
        summary={"client_id": str(client.id), "client_name": client.name, "message": f"Polling qBittorrent client {client.name}…"},
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_download_client_poll(job.id, client.id, client_factory=client_factory))
    return JobAcceptedResponse(job_id=job.id)


@router.post("/downloads/poll", response_model=JobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def poll_all_clients(
    _: object = Depends(require_csrf),
    admin: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_qbittorrent_client_factory),
) -> JobAcceptedResponse:
    del admin
    clients = [client for client in await list_configured_download_clients(db) if client.enabled]
    job = await create_job(
        db,
        "qbittorrent_poll_all",
        summary={"client_count": len(clients), "message": f"Polling {len(clients)} qBittorrent client{'s' if len(clients) != 1 else ''}…"},
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_all_download_client_polls(job.id, client_factory=client_factory))
    return JobAcceptedResponse(job_id=job.id)


@router.get("/downloads", response_model=Collection[DownloadResponse])
async def list_downloads(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    client_id: UUID | None = None,
    scope: DownloadScope | None = None,
    state: str | None = None,
    include_removed: bool = False,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[DownloadResponse]:
    conditions = []
    if client_id is not None:
        conditions.append(TorrentClientObservation.download_client_id == client_id)
    if not include_removed:
        conditions.append(TorrentClientObservation.is_present.is_(True))
    if state:
        conditions.append(TorrentClientObservation.state == state)
    if scope:
        eligible_ids = [item.id for item in get_integration_config_store().list_download_clients() if item.scope == scope.value]
        if not eligible_ids:
            return Collection(items=[], page=page, page_size=page_size, total=0, pages=0)
        conditions.append(TorrentClientObservation.download_client_id.in_(eligible_ids))
    query = (
        select(TorrentClientObservation, Torrent)
        .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
        .where(*conditions)
        .order_by(TorrentClientObservation.last_seen_at.desc())
    )
    count_query = (
        select(func.count())
        .select_from(TorrentClientObservation)
        .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
        .where(*conditions)
    )
    total = await db.scalar(count_query) or 0
    rows = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).all()
    return Collection(
        items=[
            await _download_response(db, observation, torrent, configured)
            for observation, torrent in rows
            if (configured := await get_configured_download_client(db, observation.download_client_id)) is not None
        ],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )
