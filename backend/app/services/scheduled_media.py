from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.domain import (
    Episode,
    Job,
    JobStatus,
    MediaDirectory,
    MediaFile,
    MediaType,
    ReleaseState,
    Severity,
    StorageRoot,
)
from app.services.events import create_event
from app.services.jobs import JobFailure, checkpoint_job, create_job, publish_job_status, run_job, update_job
from app.services.library_scan import active_storage_root_scan_job, run_storage_root_scan
from app.services.reconciliation import directories_within, mark_absent_known_directories, mark_root_available, mark_root_unavailable
from app.services.shows import mark_absent_show_directories, refresh_episode_presence


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _existing_paths(paths: list[tuple[str, bool]]) -> set[str]:
    existing: set[str] = set()
    for value, directory in paths:
        path = Path(value)
        exists = path.is_dir() if directory else path.exists()
        if exists:
            existing.add(value)
    return existing


async def _verify_root(db: AsyncSession, root: StorageRoot, *, within: str | None = None) -> dict[str, int | str]:
    """Check known media under ``root`` (or only ``within`` one folder of it)."""

    if not await asyncio.to_thread(Path(root.resolved_root_path).is_dir):
        if within is not None:
            # The watcher checks one folder; an unreachable root is the health
            # check's to report, not a reason to mark this folder missing.
            return {"root": root.name, "unavailable": 1, "checked": 0, "missing": 0, "restored": 0, "affected": 0}
        affected = await mark_root_unavailable(db, root)
        return {"root": root.name, "unavailable": 1, "checked": 0, "missing": 0, "restored": 0, "affected": affected}

    await mark_root_available(db, root)
    directories = (
        await db.scalars(
            select(MediaDirectory)
            .options(
                selectinload(MediaDirectory.files).selectinload(MediaFile.episode_maps),
                selectinload(MediaDirectory.movie_release),
            )
            .where(MediaDirectory.storage_root_id == root.id, *directories_within(within))
        )
    ).unique().all()
    directory_paths = await asyncio.to_thread(
        _existing_paths,
        [(item.resolved_path, True) for item in directories],
    )
    directory_result = (
        await mark_absent_show_directories(db, root, directory_paths, within=within)
        if root.media_type == MediaType.SHOWS
        else await mark_absent_known_directories(db, root, directory_paths, within=within)
    )

    file_candidates = [
        (str(Path(directory.resolved_path) / media_file.relative_path), False)
        for directory in directories
        if directory.resolved_path in directory_paths
        for media_file in directory.files
    ]
    existing_files = await asyncio.to_thread(_existing_paths, file_candidates)
    now = utcnow()
    threshold = max(1, root.missing_grace_checks or 2)
    checked = missing = restored = 0
    touched_episode_ids: set[UUID] = set()

    for directory in directories:
        directory_present = directory.resolved_path in directory_paths
        for media_file in directory.files:
            path = str(Path(directory.resolved_path) / media_file.relative_path)
            if not directory_present:
                if not directory.exists:
                    media_file.exists = False
                continue
            checked += 1
            media_file.last_exists_check_at = now
            if path in existing_files:
                if not media_file.exists:
                    restored += 1
                    touched_episode_ids.update(item.episode_id for item in media_file.episode_maps)
                media_file.exists = True
                media_file.last_seen_at = now
                media_file.missing_since = None
                media_file.missing_check_count = 0
                continue
            if not media_file.exists:
                continue
            if media_file.missing_since is None:
                media_file.missing_since = now
                media_file.missing_check_count = 1
                continue
            media_file.missing_check_count = (media_file.missing_check_count or 0) + 1
            if media_file.missing_check_count < threshold:
                continue
            media_file.exists = False
            missing += 1
            touched_episode_ids.update(item.episode_id for item in media_file.episode_maps)
            await create_event(
                db,
                "media_file.missing",
                entity_type="media_file",
                entity_id=media_file.id,
                message=f"A known media file is missing: {media_file.filename}",
                severity=Severity.WARNING,
                details={"path": path, "root_id": str(root.id), "grace_checks": threshold},
            )

        release = directory.movie_release
        if release is not None and directory.files:
            any_present = any(item.exists for item in directory.files)
            if not any_present and release.release_state in {ReleaseState.CURRENT, ReleaseState.DUPLICATE}:
                release.release_state = ReleaseState.MISSING
            elif any_present and release.release_state == ReleaseState.MISSING:
                release.release_state = ReleaseState.CURRENT

    for episode_id in touched_episode_ids:
        episode = await db.get(Episode, episode_id)
        if episode is not None:
            await refresh_episode_presence(db, episode)

    return {
        "root": root.name,
        "unavailable": 0,
        "checked": checked,
        "missing": missing + int(directory_result["missing"]),
        "restored": restored + int(directory_result["restored"]),
        "affected": 0,
    }


async def run_known_media_verification(job_id: UUID) -> None:
    async def worker(db: AsyncSession, job: Job) -> None:
        roots = (
            await db.scalars(
                select(StorageRoot)
                .where(StorageRoot.enabled.is_(True), StorageRoot.last_scan_at.is_not(None))
                .order_by(StorageRoot.sort_order, StorageRoot.name)
            )
        ).all()
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"stage": "verifying", "percent": 0, "detail": "Checking known media paths…"},
        )
        totals = {"roots": len(roots), "checked": 0, "missing": 0, "restored": 0, "unavailable": 0}
        results: list[dict[str, int | str]] = []
        for index, root in enumerate(roots, start=1):
            result = await _verify_root(db, root)
            results.append(result)
            for key in ("checked", "missing", "restored", "unavailable"):
                totals[key] += int(result[key])
            await checkpoint_job(
                db,
                job,
                progress={
                    "stage": "verifying",
                    "percent": round(index * 100 / len(roots), 1) if roots else 100,
                    "detail": f"Checked {root.name}.",
                },
                summary={**dict(job.summary or {}), **totals, "results": results},
            )
        detail = f"Checked {totals['checked']} known file(s) across {totals['roots']} initialized media folder(s)."
        await update_job(
            db,
            job,
            status=JobStatus.COMPLETED,
            progress={"stage": "completed", "percent": 100, "detail": detail},
            summary={**dict(job.summary or {}), **totals, "results": results},
        )

    await run_job(
        job_id,
        worker,
        failure_code="KNOWN_MEDIA_VERIFICATION_FAILED",
        failure_message="Known media verification failed.",
        failure_progress={"stage": "failed", "percent": 100},
    )


async def run_full_media_folder_scan(job_id: UUID) -> None:
    async def worker(db: AsyncSession, job: Job) -> None:
        roots = (
            await db.scalars(
                select(StorageRoot)
                .where(StorageRoot.enabled.is_(True), StorageRoot.last_scan_at.is_not(None))
                .order_by(StorageRoot.sort_order, StorageRoot.name)
            )
        ).all()
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"stage": "scanning", "percent": 0, "detail": "Preparing initialized media folders…"},
        )
        completed = failed = skipped = 0
        child_job_ids: list[str] = []
        for index, root in enumerate(roots, start=1):
            active = await active_storage_root_scan_job(db, root.id)
            if active is not None:
                skipped += 1
            else:
                child = await create_job(
                    db,
                    "storage_root_scan",
                    cancellable=False,
                    summary={"storage_root_id": str(root.id), "path": root.resolved_root_path, "trigger": "scheduled"},
                )
                await db.commit()
                publish_job_status(child)
                child_job_ids.append(str(child.id))
                await run_storage_root_scan(child.id, root.id)
                await db.refresh(child)
                if child.status == JobStatus.COMPLETED:
                    completed += 1
                else:
                    failed += 1
            await checkpoint_job(
                db,
                job,
                progress={
                    "stage": "scanning",
                    "percent": round(index * 100 / len(roots), 1) if roots else 100,
                    "detail": f"Finished {root.name}.",
                },
                summary={
                    **dict(job.summary or {}),
                    "roots": len(roots),
                    "completed": completed,
                    "failed": failed,
                    "skipped": skipped,
                    "child_job_ids": child_job_ids,
                },
            )
        detail = f"Scanned {completed} of {len(roots)} initialized media folder(s)."
        if failed:
            raise JobFailure(
                "FULL_MEDIA_FOLDER_SCAN_PARTIAL_FAILURE",
                f"{failed} media folder scan(s) failed.",
                details={"roots": len(roots), "completed": completed, "failed": failed, "skipped": skipped},
                progress={"stage": "failed", "percent": 100, "detail": detail},
            )
        await update_job(
            db,
            job,
            status=JobStatus.COMPLETED,
            progress={"stage": "completed", "percent": 100, "detail": detail},
            summary={
                **dict(job.summary or {}),
                "roots": len(roots),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "child_job_ids": child_job_ids,
            },
        )

    await run_job(
        job_id,
        worker,
        failure_code="FULL_MEDIA_FOLDER_SCAN_FAILED",
        failure_message="Full media folder scan failed.",
        failure_progress={"stage": "failed", "percent": 100},
    )
