from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Job, JobStatus, Schedule
from app.schemas.jobs import JobAcceptedResponse
from app.services.backup_crypto import decode_key, new_recovery_key
from app.services.remote_backups import (DriveBackups, access_token, begin_oauth, finish_oauth,
    oauth_ready, read_backup_settings, update_backup_settings)
from app.services.scheduler import ensure_default_schedules, trigger_schedule

router = APIRouter(prefix="/backups", tags=["backups"])


class BackupSettingsRequest(BaseModel):
    recovery_key: str | None = Field(default=None, max_length=100)
    keep_daily: int = Field(default=7, ge=1, le=365)
    keep_weekly: int = Field(default=4, ge=0, le=104)
    interval_seconds: int = Field(default=86400, ge=3600, le=2592000)
    enabled: bool = False


@router.get("/status")
async def backup_status(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    saved = read_backup_settings()
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    active = await db.scalar(select(Job).where(Job.job_type == "remote_backup",
        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).limit(1))
    return {"oauth_configured": oauth_ready(), "connected": bool(saved.get("refresh_token")),
        "key_configured": bool(saved.get("recovery_key")), "keep_daily": saved["keep_daily"],
        "keep_weekly": saved["keep_weekly"], "last_success_at": saved.get("last_success_at"),
        "last_error": saved.get("last_error"), "enabled": bool(schedule and schedule.enabled),
        "torrent_count": len(saved.get("coverage") or {}),
        "interval_seconds": int(schedule.expression) if schedule else 86400,
        "next_run_at": schedule.next_run_at.isoformat() if schedule and schedule.next_run_at else None,
        "active_job_id": str(active.id) if active else None}


@router.post("/recovery-key")
async def generate_recovery_key(response: Response, _: object = Depends(require_csrf)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"recovery_key": new_recovery_key()}


@router.put("/settings")
async def save_backup_settings(payload: BackupSettingsRequest, _: object = Depends(require_csrf),
                               db: AsyncSession = Depends(get_db)) -> dict:
    saved = read_backup_settings()
    if payload.recovery_key:
        try:
            decode_key(payload.recovery_key)
        except ValueError as exc:
            raise AppError("INVALID_RECOVERY_KEY", str(exc), status_code=422) from exc
        if saved.get("recovery_key") and saved["recovery_key"] != payload.recovery_key:
            raise AppError("KEY_ALREADY_CONFIGURED", "This destination already has a recovery key. Keep using it so retained backups remain recoverable.", status_code=409)
    if payload.enabled and not (saved.get("refresh_token") and (saved.get("recovery_key") or payload.recovery_key)):
        raise AppError("BACKUP_SETUP_INCOMPLETE", "Connect Google Drive and save a recovery key before enabling scheduled backups.", status_code=409)
    changes = {"keep_daily": payload.keep_daily, "keep_weekly": payload.keep_weekly}
    if payload.recovery_key:
        changes["recovery_key"] = payload.recovery_key
    update_backup_settings(changes)
    await ensure_default_schedules(db)
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    from datetime import datetime, timedelta, timezone
    schedule.enabled = payload.enabled
    schedule.expression = str(payload.interval_seconds)
    schedule.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=payload.interval_seconds) if payload.enabled else None
    await db.commit()
    return {"saved": True}


@router.post("/google/connect")
async def connect_google(session=Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    if await db.scalar(select(Job.id).where(Job.job_type == "remote_backup", Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).limit(1)):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup before changing its Google account.", status_code=409)
    return {"authorization_url": begin_oauth(str(session.admin.id))}


@router.post("/google/disconnect")
async def disconnect_google(_: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    if await db.scalar(select(Job.id).where(Job.job_type == "remote_backup", Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).limit(1)):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup before disconnecting Google Drive.", status_code=409)
    update_backup_settings({"refresh_token": None, "oauth_state": None, "oauth_verifier": None})
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    if schedule:
        schedule.enabled = False
        schedule.next_run_at = None
    await db.commit()
    return {"disconnected": True, "remote_snapshots_retained": True}


@router.get("/google/callback")
async def google_callback(code: str = Query(default="", max_length=4096), state: str = Query(max_length=256),
                          admin: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if await db.scalar(select(Job.id).where(Job.job_type == "remote_backup", Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).limit(1)):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup, then reconnect Google Drive.", status_code=409)
    await finish_oauth(code, state, str(admin.id))
    return RedirectResponse("/settings?tab=Backups", status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/run", response_model=JobAcceptedResponse, status_code=202)
async def start_backup(_: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)):
    saved = read_backup_settings()
    if not saved.get("refresh_token") or not saved.get("recovery_key"):
        raise AppError("BACKUP_SETUP_INCOMPLETE", "Connect Google Drive and save a recovery key first.", status_code=409)
    await ensure_default_schedules(db)
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    job = await trigger_schedule(db, schedule, trigger="manual")
    return JobAcceptedResponse(job_id=job.id)


@router.get("/google/snapshots")
async def list_snapshots(_: AdminUser = Depends(require_admin)) -> dict:
    drive = DriveBackups(await access_token())
    try:
        rows = await drive.list()
        return {"items": [{"id": row["id"], "name": row["name"], "size": row.get("size"),
                           "created_at": row.get("createdTime"),
                           "verified": row.get("appProperties", {}).get("verified") == "true",
                           "download_url": f"https://drive.google.com/file/d/{row['id']}/view"} for row in rows]}
    finally:
        await drive.close()
