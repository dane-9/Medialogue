from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import JobStatus, Schedule
from app.schemas.tasks import ScheduledTaskResponse, ScheduledTaskRunResponse, ScheduledTaskUpdateRequest
from app.services.scheduler import (
    ACTIVE_STATES,
    DEFAULT_SCHEDULES,
    ensure_default_schedules,
    schedule_definition,
    schedule_interval,
    schedule_name,
    trigger_schedule,
    utcnow,
)


router = APIRouter(tags=["scheduled tasks"])


async def _response(db: AsyncSession, schedule: Schedule) -> ScheduledTaskResponse:
    from app.models.domain import Job

    jobs = (
        await db.scalars(
            select(Job)
            .where(Job.schedule_id == schedule.id, Job.status.in_(ACTIVE_STATES))
            .order_by(Job.created_at.desc())
        )
    ).all()
    active = jobs[0] if jobs else None
    definition = schedule_definition(schedule)
    return ScheduledTaskResponse(
        id=schedule.id,
        name=schedule_name(schedule),
        job_type=schedule.job_type,
        description=str(definition.get("description") or "Background application task."),
        interval_seconds=schedule_interval(schedule),
        interval_options=list(definition.get("interval_options") or []),
        configurable=bool(definition.get("configurable", False)),
        enabled=schedule.enabled,
        last_started_at=schedule.last_started_at,
        last_finished_at=schedule.last_finished_at,
        next_run_at=schedule.next_run_at,
        last_status=schedule.last_status,
        last_duration_ms=schedule.last_duration_ms,
        active_job_id=active.id if active else None,
        active_job_status=active.status if active else None,
    )


@router.get("/tasks", response_model=list[ScheduledTaskResponse])
async def list_tasks(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[ScheduledTaskResponse]:
    await ensure_default_schedules(db)
    await db.commit()
    schedules = (await db.scalars(select(Schedule))).all()
    configured_order = {job_type: index for index, job_type in enumerate(DEFAULT_SCHEDULES)}
    schedules.sort(key=lambda item: (configured_order.get(item.job_type, len(configured_order)), item.job_type))
    return [await _response(db, schedule) for schedule in schedules]


@router.patch("/tasks/{task_id}", response_model=ScheduledTaskResponse)
async def update_task(
    task_id: UUID,
    payload: ScheduledTaskUpdateRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> ScheduledTaskResponse:
    schedule = await db.get(Schedule, task_id)
    if schedule is None:
        raise AppError("TASK_NOT_FOUND", "Scheduled task was not found.", status_code=404)
    definition = schedule_definition(schedule)
    if not definition.get("configurable", False):
        raise AppError("TASK_INTERVAL_FIXED", "This task has a fixed interval.", status_code=409)
    interval = payload.interval_seconds
    options = set(definition.get("interval_options") or [])
    if interval is not None and interval not in options:
        raise AppError("TASK_INTERVAL_INVALID", "Select one of the supported task intervals.", status_code=422)
    schedule.enabled = interval is not None
    if interval is None:
        schedule.next_run_at = None
    else:
        schedule.expression = str(interval)
        schedule.next_run_at = utcnow() + timedelta(seconds=interval)
    await db.commit()
    await db.refresh(schedule)
    return await _response(db, schedule)


@router.post("/tasks/{task_id}/run", response_model=ScheduledTaskRunResponse)
async def run_task(
    task_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> ScheduledTaskRunResponse:
    schedule = await db.get(Schedule, task_id)
    if schedule is None:
        raise AppError("TASK_NOT_FOUND", "Scheduled task was not found.", status_code=404)
    job = await trigger_schedule(db, schedule, trigger="manual")
    return ScheduledTaskRunResponse(job_id=job.id)
