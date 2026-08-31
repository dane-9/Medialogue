from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.integrations.qbittorrent import QBittorrentClient
from app.models.auth import AdminUser
from app.models.domain import Job, JobStatus
from app.services.events import create_event
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.recovery import (
    RecoveryCapabilitiesResponse,
    RecoveryExportAcceptedResponse,
    RecoveryReimportPlanResponse,
    RecoveryReimportRequest,
)
from app.services.jobs import create_job
from app.services.recovery import (
    build_recovery_reimport_plan,
    cleanup_expired_recovery_exports,
    confirm_recovery_reimport_plan,
    recovery_bundle_path,
    recovery_capabilities,
    recovery_export_root,
    run_recovery_export,
    run_recovery_reimport,
)
from app.services.jobs import publish_job_status
from app.services.runtime_jobs import launch_runtime_job

router = APIRouter(prefix="/recovery", tags=["recovery"])


def get_recovery_export_runner():
    return run_recovery_export


def get_recovery_qbit_factory():
    return QBittorrentClient


@router.get("/capabilities", response_model=RecoveryCapabilitiesResponse)
async def capabilities(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> RecoveryCapabilitiesResponse:
    return RecoveryCapabilitiesResponse(**(await recovery_capabilities(db)))


@router.post(
    "/export",
    response_model=RecoveryExportAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_recovery_export(
    background_tasks: BackgroundTasks,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    runner=Depends(get_recovery_export_runner),
) -> RecoveryExportAcceptedResponse:
    capability = await recovery_capabilities(db)
    if not capability["supported"]:
        raise AppError(
            "RECOVERY_EXPORT_UNAVAILABLE",
            "Recovery export is not currently available.",
            status_code=409,
            details={"reasons": capability.get("reasons") or []},
        )
    settings = get_settings()
    cleanup_expired_recovery_exports(settings)
    existing = await db.scalar(
        select(Job)
        .where(Job.job_type == "recovery_export", Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        raise AppError(
            "RECOVERY_EXPORT_ALREADY_RUNNING",
            "A Recovery Bundle export is already running.",
            status_code=409,
            details={"job_id": str(existing.id)},
        )
    job = await create_job(
        db,
        "recovery_export",
        cancellable=False,
        summary={
            "message": "Preparing Recovery Bundle.",
            "sensitive": True,
            "retention_hours": settings.recovery_export_retention_hours,
        },
    )
    await create_event(
        db,
        "recovery.export_started",
        entity_type="job",
        entity_id=job.id,
        message="Recovery Bundle export started.",
        details={"sensitive": True},
    )
    await db.commit()
    background_tasks.add_task(runner, job.id)
    return RecoveryExportAcceptedResponse(job_id=job.id)


def _validated_bundle_path(job: Job) -> Path:
    settings = get_settings()
    root = recovery_export_root(settings).resolve(strict=False)
    raw = str((job.summary or {}).get("bundle_path") or "")
    candidate = Path(raw).resolve(strict=False) if raw else recovery_bundle_path(job.id, settings).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AppError("RECOVERY_BUNDLE_PATH_INVALID", "Stored Recovery Bundle path is invalid.", status_code=409) from exc
    return candidate


@router.get("/exports/{job_id}/download")
async def download_recovery_export(
    job_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    job = await db.get(Job, job_id)
    if job is None or job.job_type != "recovery_export":
        raise AppError("NOT_FOUND", "Recovery export job was not found.", status_code=404)
    if job.status != JobStatus.COMPLETED:
        raise AppError("RECOVERY_EXPORT_NOT_READY", "Recovery Bundle is not ready for download.", status_code=409)
    bundle = _validated_bundle_path(job)
    if not bundle.is_file():
        raise AppError(
            "RECOVERY_EXPORT_EXPIRED",
            "The temporary Recovery Bundle is no longer available. Create a new export.",
            status_code=410,
        )
    filename = str((job.summary or {}).get("bundle_filename") or bundle.name)
    return FileResponse(
        bundle,
        media_type="application/zip",
        filename=filename,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/reimport/plan", response_model=RecoveryReimportPlanResponse)
async def recovery_reimport_plan(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_recovery_qbit_factory),
) -> RecoveryReimportPlanResponse:
    plan = await build_recovery_reimport_plan(db, client_factory=client_factory)
    return RecoveryReimportPlanResponse(**plan)


@router.post(
    "/reimport",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_recovery_reimport(
    payload: RecoveryReimportRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_recovery_qbit_factory),
) -> JobAcceptedResponse:
    existing = await db.scalar(
        select(Job)
        .where(Job.job_type == "recovery_reimport", Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        raise AppError(
            "RECOVERY_REIMPORT_ALREADY_RUNNING",
            "A bulk recovery re-import is already running.",
            status_code=409,
            details={"job_id": str(existing.id)},
        )

    _, selected = await confirm_recovery_reimport_plan(
        db,
        payload.confirmation_token,
        payload.torrent_ids,
        client_factory=client_factory,
    )
    job = await create_job(
        db,
        "recovery_reimport",
        cancellable=False,
        summary={
            "message": f"Preparing to submit {len(selected)} archived torrent(s) to qBittorrent.",
            "selected": len(selected),
            "torrent_ids": [item["torrent_id"] for item in selected],
        },
    )
    await create_event(
        db,
        "recovery.reimport_started",
        entity_type="job",
        entity_id=job.id,
        message=f"Bulk recovery re-import started for {len(selected)} torrent(s).",
        details={"selected": len(selected)},
    )
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(
        job.id,
        lambda: run_recovery_reimport(job.id, selected, client_factory=client_factory),
    )
    return JobAcceptedResponse(job_id=job.id)
