import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.models.domain import Job, JobStatus
from app.services.events import publish_live_event


TERMINAL_JOB_STATES = frozenset(
    {
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
    }
)


class JobFailure(Exception):
    """A worker failure with the durable error payload shown to administrators."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Any = None,
        progress: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.progress = progress


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


async def checkpoint_job(
    db: AsyncSession,
    job: Job,
    *,
    status: JobStatus | None = None,
    progress: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> Job:
    """Persist and publish a durable job checkpoint.

    Long-running workers intentionally commit at visible checkpoints so a
    cancellation request can be observed by the worker and the UI can show
    progress immediately. Keeping that small lifecycle operation here avoids
    repeating the update/commit/publish sequence in every worker.
    """

    await update_job(
        db,
        job,
        status=status,
        progress=progress,
        summary=summary,
        error=error,
    )
    await db.commit()
    publish_job_status(job)
    return job


async def run_job(
    job_id: UUID,
    worker: Callable[[AsyncSession, Job], Awaitable[None]],
    *,
    failure_code: str,
    failure_message: str,
    failure_progress: dict[str, Any] | None = None,
) -> None:
    """Run a durable Job worker with the shared session/error lifecycle.

    Workers remain responsible for domain-specific progress and summaries.
    This helper owns the invariant parts: loading the durable row, ignoring
    terminal work, rolling back failed transactions, and publishing the final
    state.
    """

    async with db_session.async_session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None or job.status in TERMINAL_JOB_STATES:
            return

        try:
            await worker(db, job)
        except asyncio.CancelledError:
            raise
        except JobFailure as exc:
            await db.rollback()
            job = await db.get(Job, job_id)
            if job is None or job.status in {JobStatus.CANCELLED, JobStatus.INTERRUPTED}:
                return
            await update_job(
                db,
                job,
                status=JobStatus.FAILED,
                progress=exc.progress or failure_progress,
                error={
                    "code": exc.code,
                    "message": exc.message,
                    **({"details": exc.details} if exc.details is not None else {}),
                },
            )
        except Exception as exc:
            await db.rollback()
            job = await db.get(Job, job_id)
            if job is None or job.status in {JobStatus.CANCELLED, JobStatus.INTERRUPTED}:
                return
            await update_job(
                db,
                job,
                status=JobStatus.FAILED,
                progress=failure_progress,
                error={"code": failure_code, "message": str(exc) or failure_message},
            )

        await db.commit()
        publish_job_status(job)


async def cancel_job(db: AsyncSession, job: Job) -> Job:
    if not job.cancellable or job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
        return job
    return await update_job(db, job, status=JobStatus.CANCELLED)
