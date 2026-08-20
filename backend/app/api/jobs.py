import asyncio
import json
from datetime import datetime
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Event, Job, JobStatus
from app.schemas.common import Collection
from app.schemas.jobs import EventResponse, JobResponse
from app.services.events import subscribe, unsubscribe
from app.services.jobs import cancel_job, publish_job_status
from app.services.runtime_jobs import cancel_runtime_job

router = APIRouter(tags=["jobs and events"])


@router.get("/jobs", response_model=Collection[JobResponse])
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    status_filter: str | None = Query(None, alias="status"),
    job_type: str | None = None,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[JobResponse]:
    query = select(Job)
    count_query = select(func.count()).select_from(Job)
    if status_filter:
        try:
            parsed_status = JobStatus(status_filter)
        except ValueError as exc:
            raise AppError("INVALID_JOB_STATUS", f"Unknown job status: {status_filter}", status_code=422) from exc
        query = query.where(Job.status == parsed_status)
        count_query = count_query.where(Job.status == parsed_status)
    if job_type:
        query = query.where(Job.job_type == job_type)
        count_query = count_query.where(Job.job_type == job_type)
    total = await db.scalar(count_query) or 0
    rows = (
        await db.scalars(
            query.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return Collection(
        items=[JobResponse.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID, _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> JobResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise AppError("NOT_FOUND", "Job was not found.", status_code=404)
    return JobResponse.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel(job_id: UUID, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> JobResponse:
    job = await db.get(Job, job_id)
    if job is None:
        raise AppError("NOT_FOUND", "Job was not found.", status_code=404)
    previous = job.status
    await cancel_job(db, job)
    await db.commit()
    publish_job_status(job)
    if previous in {JobStatus.QUEUED, JobStatus.RUNNING} and job.status == JobStatus.CANCELLED:
        cancel_runtime_job(job.id)
    return JobResponse.model_validate(job)


@router.get("/events", response_model=Collection[EventResponse])
async def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[EventResponse]:
    query = select(Event)
    count_query = select(func.count()).select_from(Event)
    filters = []
    if entity_type:
        filters.append(Event.entity_type == entity_type)
    if entity_id:
        filters.append(Event.entity_id == entity_id)
    if event_type:
        filters.append(Event.event_type == event_type)
    if severity:
        filters.append(Event.severity == severity)
    if date_from:
        filters.append(Event.created_at >= date_from)
    if date_to:
        filters.append(Event.created_at <= date_to)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    total = await db.scalar(count_query) or 0
    rows = (
        await db.scalars(
            query.order_by(Event.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return Collection(
        items=[EventResponse.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


async def _event_stream(request: Request) -> AsyncIterator[str]:
    queue = subscribe()
    try:
        while not await request.is_disconnected():
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15)
                yield f"event: {payload['event']}\ndata: {json.dumps(payload)}\n\n"
            except TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        unsubscribe(queue)


@router.get("/events/stream")
async def event_stream(request: Request, _: AdminUser = Depends(require_admin)) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
