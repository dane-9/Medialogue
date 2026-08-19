from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.api.operations import active_operations_enabled
from app.core.errors import AppError
from app.db.session import get_db
from app.integrations.filesystem import FilesystemObserver
from app.models.auth import AdminUser
from app.models.domain import (
    AssociationType,
    IdentityState,
    MediaDirectory,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    Problem,
    ProblemStatus,
    ReleaseState,
    StorageRoot,
    Torrent,
)
from app.schemas.reconciliation import (
    ManualAttachRequest,
    ReconciliationActionResponse,
    ReconciliationRootStatus,
    ReconciliationRunRequest,
    ReconciliationRunResponse,
    ReconciliationStatusResponse,
)
from app.services.jobs import create_job
from app.services.library_scan import run_storage_root_scan, storage_root_scan_running
from app.services.reconciliation import reconcile_movie_directory


router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get("/status", response_model=ReconciliationStatusResponse)
async def reconciliation_status(
    _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> ReconciliationStatusResponse:
    roots = (await db.scalars(select(StorageRoot).order_by(StorageRoot.name))).all()
    root_status: list[ReconciliationRootStatus] = []
    for root in roots:
        affected = int(
            await db.scalar(
                select(func.count()).select_from(MediaDirectory).where(
                    MediaDirectory.storage_root_id == root.id,
                    MediaDirectory.exists.is_(True),
                )
            )
            or 0
        )
        root_status.append(
            ReconciliationRootStatus(
                id=root.id,
                name=root.name,
                path=root.resolved_root_path,
                health=root.last_health,
                affected_count=affected,
            )
        )
    incoming_count = int(
        await db.scalar(
            select(func.count()).select_from(MovieReleaseTorrent).where(
                MovieReleaseTorrent.association_type == AssociationType.INCOMING
            )
        )
        or 0
    )
    missing_count = int(
        await db.scalar(
            select(func.count()).select_from(MovieRelease).where(MovieRelease.release_state == ReleaseState.MISSING)
        )
        or 0
    )
    problem_count = int(
        await db.scalar(select(func.count()).select_from(Problem).where(Problem.status == ProblemStatus.OPEN)) or 0
    )
    return ReconciliationStatusResponse(
        generated_at=datetime.now(timezone.utc),
        roots=root_status,
        incoming_count=incoming_count,
        missing_release_count=missing_count,
        open_problem_count=problem_count,
    )


@router.post("/run", response_model=ReconciliationRunResponse, status_code=status.HTTP_202_ACCEPTED)
@router.post("/refresh", response_model=ReconciliationRunResponse, status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
async def run_reconciliation(
    background_tasks: BackgroundTasks,
    payload: ReconciliationRunRequest = ReconciliationRunRequest(),
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> ReconciliationRunResponse:
    if not active_operations_enabled():
        raise AppError("ACTIVE_OPERATIONS_LOCKED", "Enable Active Operations before reconciling.", status_code=423)
    query = select(StorageRoot).where(StorageRoot.enabled.is_(True))
    if payload.root_id is not None:
        query = query.where(StorageRoot.id == payload.root_id)
    roots = (await db.scalars(query.order_by(StorageRoot.name))).all()
    if payload.root_id is not None and not roots:
        raise AppError("NOT_FOUND", "Storage root was not found.", status_code=404)
    job_ids: list[UUID] = []
    skipped_root_ids: list[UUID] = []
    for root in roots:
        if storage_root_scan_running(root.id):
            skipped_root_ids.append(root.id)
            continue
        job = await create_job(db, "reconciliation", summary={"storage_root_id": str(root.id), "path": root.resolved_root_path})
        job_ids.append(job.id)
        background_tasks.add_task(run_storage_root_scan, job.id, root.id)
    await db.commit()
    return ReconciliationRunResponse(job_ids=job_ids, skipped_root_ids=skipped_root_ids)


@router.post("/movies/{movie_id}/manual-attach", response_model=ReconciliationActionResponse)
async def manual_attach(
    movie_id: UUID,
    payload: ManualAttachRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> ReconciliationActionResponse:
    if not active_operations_enabled():
        raise AppError("ACTIVE_OPERATIONS_LOCKED", "Enable Active Operations before attaching media.", status_code=423)
    movie = await db.get(Movie, movie_id)
    root = await db.get(StorageRoot, payload.root_id)
    if movie is None or root is None:
        raise AppError("NOT_FOUND", "Movie or storage root was not found.", status_code=404)
    if payload.expected_revision is not None and payload.expected_revision != movie.revision:
        raise AppError("REVISION_CONFLICT", "Movie changed; refresh before applying the manual match.", status_code=409)
    path = Path(payload.path).resolve(strict=False)
    try:
        path.relative_to(Path(root.resolved_root_path).resolve(strict=False))
    except ValueError as exc:
        raise AppError("OUTSIDE_MANAGED_ROOT", "The path is outside the selected storage root.", status_code=422) from exc
    if not path.is_dir():
        raise AppError("TORRENT_PATH_NOT_FOUND", "The selected media directory is not accessible.", status_code=422)
    try:
        observed = FilesystemObserver().inspect_directory(path, Path(root.resolved_root_path))
    except (OSError, PermissionError) as exc:
        raise AppError("TORRENT_PATH_NOT_FOUND", "The selected media directory could not be inspected.", status_code=422) from exc
    movie.manual_identity_override = True
    movie.identity_state = IdentityState.MANUAL
    movie.revision += 1
    result = await reconcile_movie_directory(db, root, observed, movie_hint=movie)
    await db.commit()
    return ReconciliationActionResponse(
        movie_id=movie.id,
        status="manual_attached" if result in {"matched", "duplicates", "conflicts"} else "review",
        details={"result": result, "path": str(path), "revision": movie.revision},
    )
