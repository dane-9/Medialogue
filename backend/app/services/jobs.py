from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Job, JobStatus
from app.services.events import publish_live_event


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _live_payload(job: Job) -> dict[str, Any]:
    """Small durable-state pointer for SSE consumers.

    Job.summary can contain large frozen search/parser definitions, so the live
    event intentionally carries only the fields needed by global UI. Clients
    can GET /jobs/{id} for the complete persisted record after reconnecting.
    """

    return {
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status.value,
        "progress": dict(job.progress or {}),
        "error": dict(job.error) if job.error else None,
        "cancellable": bool(job.cancellable),
        "revision": int(job.revision),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def publish_job_status(job: Job) -> None:
    publish_live_event(
        "job.status",
        entity_type="job",
        entity_id=job.id,
        data=_live_payload(job),
    )


async def create_job(
    db: AsyncSession,
    job_type: str,
    *,
    cancellable: bool = True,
    summary: dict[str, Any] | None = None,
) -> Job:
    job = Job(job_type=job_type, cancellable=cancellable, summary=summary or {}, status=JobStatus.QUEUED)
    db.add(job)
    await db.flush()
    publish_job_status(job)
    return job


async def update_job(
    db: AsyncSession,
    job: Job,
    *,
    status: JobStatus | None = None,
    progress: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> Job:
    changed = False
    if status is not None and job.status != status:
        job.status = status
        changed = True
        if status == JobStatus.RUNNING and job.started_at is None:
            job.started_at = utcnow()
        if status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED}:
            job.finished_at = utcnow()
    if progress is not None and dict(job.progress or {}) != progress:
        job.progress = progress
        changed = True
    if summary is not None and dict(job.summary or {}) != summary:
        job.summary = summary
        changed = True
    if error is not None and job.error != error:
        job.error = error
        changed = True
    if changed:
        job.revision += 1
        await db.flush()
        publish_job_status(job)
    return job


async def cancel_job(db: AsyncSession, job: Job) -> Job:
    if not job.cancellable or job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
        return job
    return await update_job(db, job, status=JobStatus.CANCELLED)
