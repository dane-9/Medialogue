from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.integrations.qbittorrent import QBittorrentClient
from app.models.auth import AdminUser
from app.models.domain import Job, JobStatus
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.recovery import RecoveryReimportPlanResponse, RecoveryReimportRequest
from app.services.events import create_event
from app.services.jobs import create_job, publish_job_status
from app.services.recovery import build_recovery_reimport_plan, confirm_recovery_reimport_plan, run_recovery_reimport
from app.services.runtime_jobs import launch_runtime_job

router = APIRouter(prefix="/recovery", tags=["recovery"])


def get_recovery_qbit_factory():
    return QBittorrentClient


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
