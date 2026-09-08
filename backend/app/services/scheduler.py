from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.models.domain import Job, JobStatus, Schedule
from app.services.health_sweep import run_integration_health_sweep
from app.services.housekeeping import run_scheduled_housekeeping
from app.services.plex_sync import run_plex_library_sync
from app.services.scheduled_media import run_full_media_folder_scan, run_known_media_verification
from app.services.upgrade_candidates import run_upgrade_search_job
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job


logger = logging.getLogger(__name__)
ACTIVE_STATES = (JobStatus.QUEUED, JobStatus.RUNNING)
DEFAULT_SCHEDULES = {
    "search_media_replacements": {"name": "Search for Media Replacements", "description": "Finds equivalent or better releases for confirmed lost tracker sources. Never downloads automatically.", "interval_seconds": 86400, "interval_options": [21600, 43200, 86400, 604800], "configurable": True, "enabled": False},
    "remote_backup": {"name": "Back Up to Google Drive", "description": "Creates and verifies an encrypted recovery snapshot in Google Drive.", "interval_seconds": 86400, "interval_options": [3600, 21600, 43200, 86400, 604800], "configurable": True, "enabled": False},
    "integration_health_sweep": {
        "name": "Check Integration Health",
        "description": "Checks configured integrations and media-folder availability.",
        "interval_seconds": 60,
        "interval_options": [60, 300, 900, 3600, 21600],
        "configurable": True,
        "enabled": True,
    },
    "verify_known_media_files": {
        "name": "Verify Known Media Files",
        "description": "Checks only media paths already known to Medialogue; it does not discover new folders.",
        "interval_seconds": 21600,
        "interval_options": [21600, 43200, 86400, 604800],
        "configurable": True,
        "enabled": True,
    },
    "full_media_folder_scan": {
        "name": "Full Media Folder Scan",
        "description": "Recursively scans initialized media folders and reconciles newly discovered or changed media.",
        "interval_seconds": 21600,
        "interval_options": [3600, 7200, 14400, 21600, 86400, 259200, 604800, 1209600, 2592000],
        "configurable": True,
        "enabled": True,
    },
    "plex_library_sync": {
        "name": "Sync Plex Library",
        "description": "Reads Plex's existing library and refreshes verification for all local media; it does not trigger a Plex scan.",
        "interval_seconds": 86400,
        "interval_options": [21600, 43200, 86400, 259200, 604800],
        "configurable": True,
        "enabled": False,
    },
    "search_media_upgrades": {
        "name": "Search for Media Upgrades",
        "description": "Searches monitored media for better releases and records only the best review candidate. It never downloads automatically.",
        "interval_seconds": 86400,
        "interval_options": [21600, 43200, 86400, 259200, 604800],
        "configurable": True,
        "enabled": True,
    },
    "scheduled_housekeeping": {
        "name": "Housekeeping",
        "description": "Removes expired background-task history and temporary application data.",
        "interval_seconds": 86400,
        "interval_options": [],
        "configurable": False,
        "enabled": True,
    },
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def schedule_interval(schedule: Schedule) -> int:
    try:
        return max(5, int(schedule.expression))
    except (TypeError, ValueError):
        return 60


def schedule_name(schedule: Schedule) -> str:
    configured = DEFAULT_SCHEDULES.get(schedule.job_type)
    return str((schedule.settings or {}).get("name") or (configured or {}).get("name") or schedule.job_type.replace("_", " ").title())


def schedule_definition(schedule: Schedule) -> dict:
    return DEFAULT_SCHEDULES.get(schedule.job_type, {})


async def ensure_default_schedules(db: AsyncSession) -> None:
    now = utcnow()
    existing = {item.job_type: item for item in (await db.scalars(select(Schedule))).all()}
    for job_type, definition in DEFAULT_SCHEDULES.items():
        schedule = existing.get(job_type)
        if schedule is None:
            enabled = bool(definition["enabled"])
            db.add(Schedule(
                job_type=job_type,
                expression=str(definition["interval_seconds"]),
                enabled=enabled,
                settings={"name": definition["name"]},
                next_run_at=now + timedelta(seconds=int(definition["interval_seconds"])) if enabled else None,
            ))
        else:
            schedule.settings = {**dict(schedule.settings or {}), "name": definition["name"]}
            if schedule.enabled and schedule.next_run_at is None:
                schedule.next_run_at = now + timedelta(seconds=int(definition["interval_seconds"]))
    await db.flush()


async def _active_job(db: AsyncSession, schedule_id: UUID) -> Job | None:
    return await db.scalar(
        select(Job)
        .where(Job.schedule_id == schedule_id, Job.status.in_(ACTIVE_STATES))
        .order_by(Job.created_at.desc())
    )


async def _finish_schedule(schedule_id: UUID, job_id: UUID) -> None:
    async with db_session.async_session_factory() as db:
        schedule = await db.get(Schedule, schedule_id)
        job = await db.get(Job, job_id)
        if schedule is None or job is None:
            return
        schedule.last_started_at = job.started_at or schedule.last_started_at
        schedule.last_finished_at = job.finished_at or utcnow()
        schedule.last_status = job.status.value
        started = _aware(schedule.last_started_at)
        finished = _aware(schedule.last_finished_at)
        schedule.last_duration_ms = max(0, round((finished - started).total_seconds() * 1000)) if started and finished else None
        schedule.next_run_at = utcnow() + timedelta(seconds=schedule_interval(schedule)) if schedule.enabled else None
        await db.commit()


async def _run_schedule(schedule_id: UUID, job_id: UUID) -> None:
    try:
        job_type = await _schedule_job_type(schedule_id)
        if job_type == "search_media_replacements":
            from app.services.replacements import run_replacement_search
            await run_replacement_search(job_id)
        elif job_type == "remote_backup":
            from app.services.remote_backups import run_remote_backup
            await run_remote_backup(job_id)
        elif job_type == "integration_health_sweep":
            await run_integration_health_sweep(job_id)
        elif job_type == "scheduled_housekeeping":
            await run_scheduled_housekeeping(job_id)
        elif job_type == "verify_known_media_files":
            await run_known_media_verification(job_id)
        elif job_type == "full_media_folder_scan":
            await run_full_media_folder_scan(job_id)
        elif job_type == "plex_library_sync":
            await run_plex_library_sync(job_id)
        elif job_type == "search_media_upgrades":
            await run_upgrade_search_job(job_id)
    finally:
        await _finish_schedule(schedule_id, job_id)


async def _schedule_job_type(schedule_id: UUID) -> str | None:
    async with db_session.async_session_factory() as db:
        schedule = await db.get(Schedule, schedule_id)
        return schedule.job_type if schedule else None


async def trigger_schedule(db: AsyncSession, schedule: Schedule, *, trigger: str) -> Job:
    schedule_id = schedule.id
    active = await _active_job(db, schedule_id)
    if active is not None:
        return active
    now = utcnow()
    try:
        job = await create_job(
            db,
            schedule.job_type,
            schedule_id=schedule_id,
            cancellable=False,
            summary={"schedule_id": str(schedule_id), "schedule_name": schedule_name(schedule), "trigger": trigger},
        )
        schedule.last_started_at = now
        schedule.last_status = JobStatus.QUEUED.value
        schedule.next_run_at = now + timedelta(seconds=schedule_interval(schedule)) if schedule.enabled else None
        await db.commit()
    except IntegrityError:
        # The partial unique index is the final arbiter when a manual request,
        # configuration-triggered request and scheduler tick arrive together.
        await db.rollback()
        active = await _active_job(db, schedule_id)
        if active is not None:
            return active
        raise
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: _run_schedule(schedule_id, job.id))
    return job



async def request_health_sweep(db: AsyncSession) -> None:
    """Make the health task due after configuration changes.

    The scheduler owns job creation, so configuration requests stay fast and
    several saves made together collapse into one non-overlapping sweep.
    """

    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "integration_health_sweep"))
    if schedule is None:
        await ensure_default_schedules(db)
        schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "integration_health_sweep"))
    if schedule is not None and schedule.enabled:
        schedule.next_run_at = utcnow()


async def run_scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            async with db_session.async_session_factory() as db:
                await ensure_default_schedules(db)
                await db.commit()
                now = utcnow()
                schedules = (await db.scalars(select(Schedule).where(Schedule.enabled.is_(True)))).all()
                for schedule in schedules:
                    next_run = _aware(schedule.next_run_at)
                    if next_run is None or next_run <= now:
                        await trigger_schedule(db, schedule, trigger="scheduled")
        except Exception:
            logger.warning("Scheduled task cycle failed", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            continue
