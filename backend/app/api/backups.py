from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
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
from app.services.backup_coverage import archive_coverage
from app.services.jobs import create_job, publish_job_status
from app.services.remote_backups import (DATABASE, DEFAULT_DATABASE_INTERVAL, DriveBackups, access_token, begin_oauth,
    finish_oauth, oauth_ready, read_backup_settings, run_drive_restore, update_backup_settings)
from app.services.runtime_jobs import launch_runtime_job
from app.services.scheduler import ensure_default_schedules, trigger_schedule

router = APIRouter(prefix="/backups", tags=["backups"])

DRIVE_JOBS = ("remote_backup", "drive_restore")


class BackupSettingsRequest(BaseModel):
    enabled: bool = False
    keep_database: int = Field(default=7, ge=1, le=90)
    database_interval_seconds: int = Field(default=DEFAULT_DATABASE_INTERVAL, ge=3600, le=2592000)


async def _active(db: AsyncSession, job_type: str) -> Job | None:
    return await db.scalar(select(Job).where(Job.job_type == job_type,
        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).order_by(Job.created_at.desc()).limit(1))


async def _drive_busy(db: AsyncSession) -> bool:
    return bool(await db.scalar(select(Job.id).where(Job.job_type.in_(DRIVE_JOBS),
        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).limit(1)))


@router.get("/status")
async def backup_status(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    saved = read_backup_settings()
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    backup = await _active(db, "remote_backup")
    restore = await _active(db, "drive_restore")
    mirror = saved.get("mirror") or {}
    return {
        "oauth_configured": oauth_ready(),
        "connected": bool(saved.get("refresh_token")),
        "enabled": bool(schedule and schedule.enabled),
        "sync_interval_seconds": int(schedule.expression) if schedule else 600,
        "next_run_at": schedule.next_run_at.isoformat() if schedule and schedule.next_run_at else None,
        "last_sync_at": saved.get("last_sync_at"),
        "last_database_at": saved.get("last_database_at"),
        "last_error": saved.get("last_error"),
        "keep_database": int(saved.get("keep_database") or 7),
        "database_interval_seconds": int(saved.get("database_interval_seconds") or DEFAULT_DATABASE_INTERVAL),
        "torrents_on_drive": sum(1 for key in mirror if key.startswith("torrents/")),
        "folder_url": f"https://drive.google.com/drive/folders/{saved['folders']['root']}" if (saved.get("folders") or {}).get("root") else None,
        "active_job_id": str(backup.id) if backup else None,
        "active_restore_job_id": str(restore.id) if restore else None,
    }


@router.put("/settings")
async def save_backup_settings(payload: BackupSettingsRequest, _: object = Depends(require_csrf),
                               db: AsyncSession = Depends(get_db)) -> dict:
    saved = read_backup_settings()
    if payload.enabled and not saved.get("refresh_token"):
        raise AppError("BACKUP_SETUP_INCOMPLETE", "Connect Google Drive before turning on backups.", status_code=409)
    update_backup_settings({"keep_database": payload.keep_database,
                            "database_interval_seconds": payload.database_interval_seconds})
    await ensure_default_schedules(db)
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    schedule.enabled = payload.enabled
    # Soon after turning on, so the first copy does not wait a whole interval.
    schedule.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=30) if payload.enabled else None
    await db.commit()
    return {"saved": True}


@router.post("/google/connect")
async def connect_google(session=Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    if await _drive_busy(db):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup or restore before changing its Google account.", status_code=409)
    return {"authorization_url": begin_oauth(str(session.admin.id))}


@router.post("/google/disconnect")
async def disconnect_google(_: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    if await _drive_busy(db):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup or restore before disconnecting Google Drive.", status_code=409)
    update_backup_settings({"refresh_token": None, "oauth_state": None, "oauth_verifier": None})
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    if schedule:
        schedule.enabled = False
        schedule.next_run_at = None
    await db.commit()
    return {"disconnected": True, "drive_files_kept": True}


@router.get("/google/callback")
async def google_callback(code: str = Query(default="", max_length=4096), state: str = Query(max_length=256),
                          admin: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if await _drive_busy(db):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup or restore, then reconnect Google Drive.", status_code=409)
    await finish_oauth(code, state, str(admin.id))
    return RedirectResponse("/backups", status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/run", response_model=JobAcceptedResponse, status_code=202)
async def start_backup(_: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)):
    """Back up now: changed torrents, and the database whatever its schedule."""

    if not read_backup_settings().get("refresh_token"):
        raise AppError("BACKUP_SETUP_INCOMPLETE", "Connect Google Drive first.", status_code=409)
    if await _active(db, "drive_restore"):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the restore to finish first.", status_code=409)
    await ensure_default_schedules(db)
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    job = await trigger_schedule(db, schedule, trigger="manual")
    return JobAcceptedResponse(job_id=job.id)


@router.get("/coverage")
async def coverage(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    return await archive_coverage(db)


@router.post("/restore", response_model=JobAcceptedResponse, status_code=202)
async def start_restore(_: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)):
    """Fetch every torrent and manifest from Drive into the local archive.

    Nothing is sent to qBittorrent here; the re-import plan does that once
    confirmed.
    """

    if not read_backup_settings().get("refresh_token"):
        raise AppError("BACKUP_SETUP_INCOMPLETE", "Connect Google Drive first.", status_code=409)
    if await _drive_busy(db):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup or restore to finish.", status_code=409)
    job = await create_job(db, "drive_restore", cancellable=False,
                           summary={"message": "Fetching torrents from Google Drive."})
    await db.commit()
    publish_job_status(job)
    launch_runtime_job(job.id, lambda: run_drive_restore(job.id))
    return JobAcceptedResponse(job_id=job.id)


@router.get("/database-copies")
async def database_copies(_: AdminUser = Depends(require_admin)) -> dict:
    """The database copies in Drive, newest first, to download by hand if ever needed."""

    folder = (read_backup_settings().get("folders") or {}).get(DATABASE)
    if not folder:
        return {"items": []}
    drive = DriveBackups(await access_token())
    try:
        rows = sorted(await drive.children(folder), key=lambda row: row.get("createdTime", ""), reverse=True)
        return {"items": [{"id": row["id"], "name": row["name"], "size": int(row["size"]) if row.get("size") else None,
                           "created_at": row.get("createdTime"),
                           "url": f"https://drive.google.com/file/d/{row['id']}/view"} for row in rows]}
    finally:
        await drive.close()
