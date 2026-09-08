from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Job, JobStatus, Torrent, UpgradeCandidate
from app.schemas.jobs import JobAcceptedResponse
from app.services.jobs import create_job
from app.services.runtime_jobs import launch_runtime_job
from app.services.replacements import replacement_queue, run_replacement_search
from app.api import downloads as downloads_api

router = APIRouter(prefix="/replacements", tags=["replacements"])


class ReplacementSearchRequest(BaseModel):
    target_key: str | None = Field(default=None, max_length=150)


class ReplacementReviewRequest(BaseModel):
    torrent_ids: list[UUID] = Field(min_length=1, max_length=250)
    action: Literal["ignore", "snooze", "restore"]


@router.get("")
async def queue(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    return {"items": await replacement_queue(db)}


@router.post("/search", response_model=JobAcceptedResponse, status_code=202)
async def search(payload: ReplacementSearchRequest, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)):
    active = await db.scalar(select(Job).where(Job.job_type == "search_media_replacements",
        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).limit(1))
    if active:
        return JobAcceptedResponse(job_id=active.id)
    job = await create_job(db, "search_media_replacements", cancellable=False)
    await db.commit()
    launch_runtime_job(job.id, lambda: run_replacement_search(job.id, target_key=payload.target_key))
    return JobAcceptedResponse(job_id=job.id)


@router.post("/review")
async def review(payload: ReplacementReviewRequest, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    for torrent_id in payload.torrent_ids:
        torrent = await db.get(Torrent, torrent_id)
        if not torrent:
            raise AppError("NOT_FOUND", "Torrent was not found", status_code=404)
        torrent.metadata_json = {**torrent.metadata_json, "replacement_review": {
            "ignored": payload.action == "ignore",
            "snoozed_until": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat() if payload.action == "snooze" else None}}
    await db.commit()
    return {"updated": len(payload.torrent_ids)}


@router.post("/{candidate_id}/retire-torrents")
async def retire(candidate_id: UUID, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db),
                 client_factory=Depends(downloads_api.get_qbittorrent_client_factory)) -> dict:
    from app.services.upgrade_execution import advance_upgrade_handoff
    row = await db.get(UpgradeCandidate, candidate_id)
    if not row or not row.target_key.startswith("replace:") or row.execution_state != "cleanup_required" or not row.torrent_id:
        raise AppError("REPLACEMENT_NOT_READY", "The replacement must be validated before retiring old torrents.", status_code=409)
    torrent = await db.get(Torrent, row.torrent_id)
    row.execution_state = "validating"
    await db.flush()
    await advance_upgrade_handoff(db, torrent, qbittorrent_client_factory=client_factory, allow_torrent_removal=True)
    await db.commit()
    return {"state": row.execution_state, "error": row.execution_error}


@router.post("/{candidate_id}/finish")
async def finish(candidate_id: UUID, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    from app.services.torrent_archive import torrent_archive_complete
    from app.services.upgrade_execution import _replacement_cleanup_guard
    row = await db.get(UpgradeCandidate, candidate_id)
    if not row or not row.target_key.startswith("replace:") or row.execution_state != "cleanup_required":
        raise AppError("REPLACEMENT_NOT_READY", "The replacement must be validated before finishing.", status_code=409)
    torrent = await db.get(Torrent, row.torrent_id) if row.torrent_id else None
    blocked, _ = await _replacement_cleanup_guard(db, row)
    if not torrent or not torrent_archive_complete(torrent) or blocked:
        raise AppError("REPLACEMENT_NOT_READY", "The replacement files or archive could not be verified. " + " ".join(blocked), status_code=409)
    row.execution_state = "completed"
    row.execution_error = None
    await db.commit()
    return {"state": "completed", "old_media_retained": True}
