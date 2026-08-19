from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.integrations.filesystem import FilesystemObserver
from app.models.domain import Job, JobStatus, MediaType, StorageRoot
from app.services.events import create_event, publish_live_event
from app.services.jobs import update_job
from app.services.reconciliation import (
    mark_absent_known_directories,
    mark_root_available,
    mark_root_unavailable,
    reconcile_movie_directory,
)
from app.services.shows import mark_absent_show_directories, reconcile_show_directory


_root_locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)


def storage_root_scan_running(root_id: UUID) -> bool:
    """Return whether this process is already reconciling the root."""

    return _root_locks[root_id].locked()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run_storage_root_scan(job_id: UUID, root_id: UUID) -> None:
    """Background task entry point using an independent DB session."""
    async with _root_locks[root_id]:
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            root = await db.get(StorageRoot, root_id)
            if job is None or root is None:
                return
            if job.status in {JobStatus.CANCELLED, JobStatus.INTERRUPTED, JobStatus.COMPLETED, JobStatus.FAILED}:
                return
            try:
                await execute_storage_root_scan(db, root, job)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                job = await db.get(Job, job_id)
                root = await db.get(StorageRoot, root_id)
                if job is not None and job.status != JobStatus.CANCELLED:
                    await update_job(
                        db,
                        job,
                        status=JobStatus.FAILED,
                        error={"code": "SCAN_FAILED", "message": str(exc)},
                    )
                    if root is not None:
                        await create_event(
                            db,
                            "scan.failed",
                            entity_type="storage_root",
                            entity_id=root.id,
                            message=f"Scan of {root.name} failed.",
                            details={"job_id": str(job.id), "error": str(exc)},
                        )
                    await db.commit()


async def execute_storage_root_scan(
    db: AsyncSession,
    root: StorageRoot,
    job: Job,
    *,
    observer: FilesystemObserver | None = None,
) -> dict[str, int]:
    observer = observer or FilesystemObserver()
    if job.status in {JobStatus.CANCELLED, JobStatus.INTERRUPTED, JobStatus.COMPLETED, JobStatus.FAILED}:
        return {"matched": 0, "review": 0, "duplicates": 0, "conflicts": 0}
    await update_job(db, job, status=JobStatus.RUNNING, progress={"current": 0, "total": 0, "percent": 0})
    await db.flush()
    publish_live_event(
        "scan.progress",
        entity_type="storage_root",
        entity_id=root.id,
        data={"job_id": str(job.id), "current": 0, "total": 0, "percent": 0, "summary": {}},
    )

    root_path = Path(root.resolved_root_path)
    root.last_health_checked_at = utcnow()
    if not root_path.is_dir():
        affected = await mark_root_unavailable(db, root)
        await update_job(
            db,
            job,
            status=JobStatus.FAILED,
            error={
                "code": "ROOT_UNREACHABLE",
                "message": "The configured root is unavailable.",
                "affected_count": affected,
            },
        )
        return {"matched": 0, "review": 0, "duplicates": 0, "conflicts": 0, "affected": affected}

    observations = await asyncio.to_thread(observer.scan_root, root.resolved_root_path)
    await mark_root_available(db, root)
    seen_paths = {item.path for item in observations}
    if root.media_type == MediaType.SHOWS:
        await mark_absent_show_directories(db, root, seen_paths)
    else:
        await mark_absent_known_directories(db, root, seen_paths)

    summary = {"matched": 0, "review": 0, "duplicates": 0, "conflicts": 0}
    total = len(observations)
    for index, observation in enumerate(observations, start=1):
        await db.refresh(job)
        if job.status == JobStatus.CANCELLED:
            return summary
        result = (
            await reconcile_show_directory(db, root, observation)
            if root.media_type == MediaType.SHOWS
            else await reconcile_movie_directory(db, root, observation)
        )
        summary[result] += 1
        progress = {"current": index, "total": total, "percent": round(index * 100 / total, 1) if total else 100}
        await update_job(
            db,
            job,
            progress=progress,
            summary={**summary, "storage_root_id": str(root.id)},
        )
        await db.flush()
        publish_live_event(
            "scan.progress",
            entity_type="storage_root",
            entity_id=root.id,
            data={"job_id": str(job.id), **progress, "summary": dict(summary)},
        )

    root.last_scan_at = utcnow()
    await update_job(
        db,
        job,
        status=JobStatus.COMPLETED,
        progress={"current": total, "total": total, "percent": 100},
        summary={**summary, "storage_root_id": str(root.id)},
    )
    await create_event(
        db,
        "scan.completed",
        entity_type="storage_root",
        entity_id=root.id,
        message=f"Scan of {root.name} completed.",
        details=summary,
    )
    return summary
