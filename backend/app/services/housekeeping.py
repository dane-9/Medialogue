from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Job, JobStatus
from app.services.jobs import checkpoint_job, run_job, update_job
from app.services.recovery import cleanup_expired_recovery_exports
from app.services.search import cleanup_expired_search_results


SHORT_RETENTION_JOB_TYPES = ("integration_health_sweep", "verify_known_media_files", "full_media_folder_scan")
HOUSEKEEPING_JOB_TYPE = "scheduled_housekeeping"
SUCCESS_RETENTION_DAYS = 7
FAILURE_RETENTION_DAYS = 30
HOUSEKEEPING_RETENTION_DAYS = 30


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def prune_scheduled_job_history(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Prune high-volume scheduler history without touching user workflow evidence."""

    current = now or utcnow()
    successful_cutoff = current - timedelta(days=SUCCESS_RETENTION_DAYS)
    failure_cutoff = current - timedelta(days=FAILURE_RETENTION_DAYS)
    housekeeping_cutoff = current - timedelta(days=HOUSEKEEPING_RETENTION_DAYS)
    deleted = 0

    successful = await db.execute(
        delete(Job).where(
            Job.job_type.in_(SHORT_RETENTION_JOB_TYPES),
            Job.status.in_((JobStatus.COMPLETED, JobStatus.CANCELLED)),
            Job.finished_at < successful_cutoff,
        )
    )
    deleted += successful.rowcount or 0

    failed = await db.execute(
        delete(Job).where(
            Job.job_type.in_(SHORT_RETENTION_JOB_TYPES),
            Job.status.in_((JobStatus.FAILED, JobStatus.INTERRUPTED)),
            Job.finished_at < failure_cutoff,
        )
    )
    deleted += failed.rowcount or 0

    housekeeping = await db.execute(
        delete(Job).where(
            Job.job_type == HOUSEKEEPING_JOB_TYPE,
            Job.status.in_((JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED)),
            Job.finished_at < housekeeping_cutoff,
        )
    )
    deleted += housekeeping.rowcount or 0
    return deleted


async def run_scheduled_housekeeping(job_id) -> None:
    async def worker(db: AsyncSession, job: Job) -> None:
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"stage": "pruning", "percent": 25, "detail": "Pruning expired scheduled-task history…"},
        )
        deleted = await prune_scheduled_job_history(db)
        expired_search_results = await cleanup_expired_search_results(db)
        expired_recovery_exports = await asyncio.to_thread(cleanup_expired_recovery_exports)
        await update_job(
            db,
            job,
            status=JobStatus.COMPLETED,
            progress={
                "stage": "completed",
                "percent": 100,
                "detail": f"Removed {deleted} execution record(s) and {expired_search_results} expired search result(s).",
            },
            summary={
                **dict(job.summary or {}),
                "deleted_jobs": deleted,
                "deleted_search_results": expired_search_results,
                "deleted_recovery_exports": expired_recovery_exports,
            },
        )

    await run_job(
        job_id,
        worker,
        failure_code="HOUSEKEEPING_FAILED",
        failure_message="Scheduled housekeeping failed.",
        failure_progress={"stage": "failed", "percent": 100},
    )
