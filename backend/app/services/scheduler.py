from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.models.domain import Job, JobStatus, Schedule
from app.services.health_sweep import run_integration_health_sweep
from app.services.jobs import create_job, publish_job_status
from app.services.runtime_jobs import launch_runtime_job


logger = logging.getLogger(__name__)
ACTIVE_STATES = (JobStatus.QUEUED, JobStatus.RUNNING)
DEFAULT_SCHEDULES = {
    "integration_health_sweep": {"name": "Check Integration Health", "interval_seconds": 60},
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


async def ensure_default_schedules(db: AsyncSession) -> None:
    now = utcnow()
    existing = {item.job_type: item for item in (await db.scalars(select(Schedule))).all()}
    for job_type, definition in DEFAULT_SCHEDULES.items():
        schedule = existing.get(job_type)
        if schedule is None:
            db.add(Schedule(
                job_type=job_type,
                expression=str(definition["interval_seconds"]),
                enabled=True,
                settings={"name": definition["name"]},
                next_run_at=now + timedelta(seconds=int(definition["interval_seconds"])),
            ))
        else:
            schedule.expression = str(definition["interval_seconds"])
            schedule.settings = {**dict(schedule.settings or {}), "name": definition["name"]}
            if schedule.next_run_at is None:
                schedule.next_run_at = now + timedelta(seconds=int(definition["interval_seconds"]))
    await db.flush()


async def _active_job(db: AsyncSession, schedule: Schedule) -> Job | None:
    rows = (
        await db.scalars(
            select(Job)
            .where(Job.job_type == schedule.job_type, Job.status.in_(ACTIVE_STATES))
            .order_by(Job.created_at.desc())
        )
    ).all()
    return next((job for job in rows if str((job.summary or {}).get("schedule_id") or "") == str(schedule.id)), None)


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
        schedule.next_run_at = utcnow() + timedelta(seconds=schedule_interval(schedule))
        await db.commit()


async def _run_schedule(schedule_id: UUID, job_id: UUID) -> None:
    try:
        if (await _schedule_job_type(schedule_id)) == "integration_health_sweep":
            await run_integration_health_sweep(job_id)
    finally:
        await _finish_schedule(schedule_id, job_id)


async def _schedule_job_type(schedule_id: UUID) -> str | None:
    async with db_session.async_session_factory() as db:
        schedule = await db.get(Schedule, schedule_id)
        return schedule.job_type if schedule else None


async def trigger_schedule(db: AsyncSession, schedule: Schedule, *, trigger: str) -> Job:
    active = await _active_job(db, schedule)
    if active is not None:
        return active
    now = utcnow()
    job = await create_job(
        db,
        schedule.job_type,
        cancellable=False,
        summary={"schedule_id": str(schedule.id), "schedule_name": schedule_name(schedule), "trigger": trigger},
    )
    schedule.last_started_at = now
    schedule.last_status = JobStatus.QUEUED.value
    schedule.next_run_at = now + timedelta(seconds=schedule_interval(schedule))
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: _run_schedule(schedule.id, job.id))
    return job


async def trigger_health_sweep(*, trigger: str = "event") -> Job | None:
    async with db_session.async_session_factory() as db:
        schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "integration_health_sweep"))
        if schedule is None:
            await ensure_default_schedules(db)
            await db.commit()
            schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "integration_health_sweep"))
        if schedule is None or not schedule.enabled:
            return None
        return await trigger_schedule(db, schedule, trigger=trigger)


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
