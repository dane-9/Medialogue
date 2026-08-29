from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import JobStatus, Schedule
from app.schemas.tasks import ScheduledTaskResponse, ScheduledTaskRunResponse
from app.services.scheduler import ACTIVE_STATES, ensure_default_schedules, schedule_interval, schedule_name, trigger_schedule


router = APIRouter(tags=["scheduled tasks"])


async def _response(db: AsyncSession, schedule: Schedule) -> ScheduledTaskResponse:
    from app.models.domain import Job

    jobs = (
        await db.scalars(
            select(Job)
            .where(Job.job_type == schedule.job_type, Job.status.in_(ACTIVE_STATES))
            .order_by(Job.created_at.desc())
        )
    ).all()
    active = next((job for job in jobs if str((job.summary or {}).get("schedule_id") or "") == str(schedule.id)), None)
    return ScheduledTaskResponse(
        id=schedule.id,
        name=schedule_name(schedule),
        job_type=schedule.job_type,
        interval_seconds=schedule_interval(schedule),
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
    schedules = (await db.scalars(select(Schedule).order_by(Schedule.job_type))).all()
    return [await _response(db, schedule) for schedule in schedules]


@router.post("/tasks/{task_id}/run", response_model=ScheduledTaskRunResponse)
async def run_task(
    task_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> ScheduledTaskRunResponse:
    schedule = await db.get(Schedule, task_id)
    if schedule is None:
        raise AppError("TASK_NOT_FOUND", "Scheduled task was not found.", status_code=404)
    if not schedule.enabled:
        raise AppError("TASK_DISABLED", "Scheduled task is disabled.", status_code=409)
    job = await trigger_schedule(db, schedule, trigger="manual")
    return ScheduledTaskRunResponse(job_id=job.id)
