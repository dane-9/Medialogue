from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.domain import Job, StorageRoot
from app.schemas.reconciliation import ReconciliationRunRequest, ReconciliationRunResponse
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job
from app.services.library_scan import active_storage_root_scan_job, run_storage_root_scan, storage_root_scan_running


router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.post("/run", response_model=ReconciliationRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_reconciliation(
    payload: ReconciliationRunRequest = ReconciliationRunRequest(),
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> ReconciliationRunResponse:
    query = select(StorageRoot).where(StorageRoot.enabled.is_(True))
    if payload.root_id is not None:
        query = query.where(StorageRoot.id == payload.root_id)
    candidate_roots = (await db.scalars(query.order_by(StorageRoot.name))).all()
    if payload.root_id is not None and not candidate_roots:
        raise AppError("NOT_FOUND", "Media folder was not found.", status_code=404)

    uninitialized_root_ids = [root.id for root in candidate_roots if root.last_scan_at is None]
    roots = [root for root in candidate_roots if root.last_scan_at is not None]
    job_ids: list[UUID] = []
    skipped_root_ids: list[UUID] = []
    active_job_ids: list[UUID] = []
    pending_launches: list[tuple[UUID, UUID]] = []
    for root in roots:
        existing = await active_storage_root_scan_job(db, root.id)
        if existing is not None or storage_root_scan_running(root.id):
            skipped_root_ids.append(root.id)
            if existing is not None:
                active_job_ids.append(existing.id)
            continue
        job = await create_job(db, "reconciliation", summary={"storage_root_id": str(root.id), "path": root.resolved_root_path})
        job_ids.append(job.id)
        pending_launches.append((job.id, root.id))
    await db.commit()
    for job_id in job_ids:
        committed_job = await db.get(Job, job_id)
        if committed_job is not None:
            publish_job_status(committed_job)
    for job_id, root_id in pending_launches:
        launch_runtime_job(job_id, lambda job_id=job_id, root_id=root_id: run_storage_root_scan(job_id, root_id))
    return ReconciliationRunResponse(
        job_ids=job_ids,
        skipped_root_ids=skipped_root_ids,
        active_job_ids=active_job_ids,
        uninitialized_root_ids=uninitialized_root_ids,
    )
