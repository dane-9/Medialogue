from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Job, JobStatus, Schedule
from app.schemas.jobs import JobAcceptedResponse
from app.services.backup_coverage import archive_coverage
from app.services.backup_import import import_backup_zip
from app.services.remote_backups import (DATABASE, DEFAULT_DATABASE_INTERVAL, DriveBackups, access_token,
    begin_device_sign_in, bytes_on_drive, check_device_sign_in, clear_client_credentials, client_credentials, copies_to_keep,
    read_backup_settings, read_copy_index, retention_settings, save_client_credentials, update_backup_settings)
from app.services.torrent_archive import rebuild_catalogue_from_manifests
from app.services.scheduler import ensure_default_schedules, trigger_schedule

router = APIRouter(prefix="/backups", tags=["backups"])



class BackupSettingsRequest(BaseModel):
    enabled: bool = False
    keep_recent: int = Field(default=0, ge=0, le=90)
    keep_daily: int = Field(default=7, ge=0, le=90)
    keep_weekly: int = Field(default=4, ge=0, le=52)
    keep_monthly: int = Field(default=6, ge=0, le=60)
    database_interval_seconds: int = Field(default=DEFAULT_DATABASE_INTERVAL, ge=3600, le=2592000)


class GoogleClientRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=512)
    client_secret: str = Field(min_length=1, max_length=512)


async def _active(db: AsyncSession, job_type: str) -> Job | None:
    return await db.scalar(select(Job).where(Job.job_type == job_type,
        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])).order_by(Job.created_at.desc()).limit(1))


async def _drive_busy(db: AsyncSession) -> bool:
    return await _active(db, "remote_backup") is not None


@router.get("/status")
async def backup_status(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> dict:
    saved = read_backup_settings()
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    backup = await _active(db, "remote_backup")
    mirror = saved.get("mirror") or {}
    client_id, _, client_source = client_credentials(saved)
    return {
        "oauth_configured": bool(client_source),
        "client_id": client_id or None,
        "client_source": client_source or None,
        "connected": bool(saved.get("refresh_token")),
        "enabled": bool(schedule and schedule.enabled),
        "sync_interval_seconds": int(schedule.expression) if schedule else 600,
        "next_run_at": schedule.next_run_at.isoformat() if schedule and schedule.next_run_at else None,
        "last_sync_at": saved.get("last_sync_at"),
        "last_database_at": saved.get("last_database_at"),
        "last_error": saved.get("last_error"),
        **retention_settings(saved),
        "database_interval_seconds": int(saved.get("database_interval_seconds") or DEFAULT_DATABASE_INTERVAL),
        "torrents_on_drive": sum(1 for key in mirror if key.startswith("torrents/")),
        "bytes_on_drive": bytes_on_drive(saved),
        "folder_url": f"https://drive.google.com/drive/folders/{saved['folders']['root']}" if (saved.get("folders") or {}).get("root") else None,
        "active_job_id": str(backup.id) if backup else None,
    }


@router.put("/settings")
async def save_backup_settings(payload: BackupSettingsRequest, _: object = Depends(require_csrf),
                               db: AsyncSession = Depends(get_db)) -> dict:
    saved = read_backup_settings()
    if payload.enabled and not saved.get("refresh_token"):
        raise AppError("BACKUP_SETUP_INCOMPLETE", "Connect Google Drive before turning on backups.", status_code=409)
    update_backup_settings({"keep_recent": payload.keep_recent, "keep_daily": payload.keep_daily,
                            "keep_weekly": payload.keep_weekly, "keep_monthly": payload.keep_monthly,
                            "database_interval_seconds": payload.database_interval_seconds})
    await ensure_default_schedules(db)
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    schedule.enabled = payload.enabled
    # Soon after turning on, so the first copy does not wait a whole interval.
    schedule.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=30) if payload.enabled else None
    await db.commit()
    return {"saved": True}


@router.put("/google/client")
async def save_google_client(payload: GoogleClientRequest, _: object = Depends(require_csrf),
                             db: AsyncSession = Depends(get_db)) -> dict:
    """The OAuth client (type "TVs and Limited Input devices") Medialogue signs in with."""

    if await _drive_busy(db):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup before changing the Google client.", status_code=409)
    save_client_credentials(payload.client_id, payload.client_secret)
    await _pause_without_account(db)
    return {"saved": True}


@router.delete("/google/client")
async def remove_google_client(_: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    if await _drive_busy(db):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup before removing the Google client.", status_code=409)
    clear_client_credentials()
    await _pause_without_account(db)
    return {"removed": True}


async def _pause_without_account(db: AsyncSession) -> None:
    """Scheduled backups stop once no Google account is connected."""

    if read_backup_settings().get("refresh_token"):
        return
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    if schedule:
        schedule.enabled = False
        schedule.next_run_at = None
    await db.commit()


@router.post("/google/connect")
async def connect_google(session=Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    """Start a device sign-in: the code to enter at the returned Google address."""

    if await _drive_busy(db):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup before changing its Google account.", status_code=409)
    return await begin_device_sign_in(str(session.admin.id))


@router.post("/google/connect/check")
async def check_google_connection(session=Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    """Polled while the code is shown; 'connected' once it was approved."""

    if await _drive_busy(db):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup, then reconnect Google Drive.", status_code=409)
    return {"status": await check_device_sign_in(str(session.admin.id))}


@router.post("/google/disconnect")
async def disconnect_google(_: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    if await _drive_busy(db):
        raise AppError("BACKUP_IN_PROGRESS", "Wait for the current backup before disconnecting Google Drive.", status_code=409)
    update_backup_settings({"refresh_token": None, "device_code": None})
    await _pause_without_account(db)
    return {"disconnected": True, "drive_files_kept": True}


@router.post("/run", response_model=JobAcceptedResponse, status_code=202)
async def start_backup(_: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)):
    """Back up now: changed torrents, and the database whatever its schedule."""

    if not read_backup_settings().get("refresh_token"):
        raise AppError("BACKUP_SETUP_INCOMPLETE", "Connect Google Drive first.", status_code=409)
    await ensure_default_schedules(db)
    schedule = await db.scalar(select(Schedule).where(Schedule.job_type == "remote_backup"))
    job = await trigger_schedule(db, schedule, trigger="manual")
    return JobAcceptedResponse(job_id=job.id)


@router.get("/coverage")
async def coverage(
    status: str | None = Query(None, description="Only list gaps with this status; the counts always cover every release."),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await archive_coverage(db, gap_status=status)


#: Big enough for the whole backup folder, database copies included.
UPLOAD_LIMIT = 16 * 1024 * 1024 * 1024


@router.post("/restore/upload")
async def upload_backup(request: Request, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> dict:
    """Take the backup folder's ZIP, downloaded from Google Drive, as the request body.

    Its torrents and manifests go into the archive, which then lists them.
    Nothing is sent to qBittorrent here; the re-import plan does that once
    confirmed. Uploading the same ZIP again changes nothing.
    """

    # Staged under /config, which is persistent and sized for backups, rather
    # than the container's /tmp, which may be small or in memory.
    staging = Path(get_settings().config_dir) / "uploads"
    staging.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix="backup-", suffix=".zip", delete=False, dir=staging)
    path = Path(handle.name)
    try:
        size = 0
        with handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > UPLOAD_LIMIT:
                    raise AppError("BACKUP_UPLOAD_TOO_LARGE", "That file is too large to be a Medialogue backup.", status_code=413)
                handle.write(chunk)
        if not size:
            raise AppError("BACKUP_UPLOAD_EMPTY", "Choose the backup ZIP to upload.", status_code=422)
        try:
            imported = await asyncio.to_thread(import_backup_zip, path)
        except ValueError as exc:
            raise AppError("BACKUP_UPLOAD_INVALID", str(exc), status_code=422) from exc
    finally:
        path.unlink(missing_ok=True)
    catalogue = await rebuild_catalogue_from_manifests(db)
    await db.commit()
    return {**imported, "listed": catalogue["imported"] + catalogue["repaired"],
            "errors": imported["errors"] + [f"{item['manifest']}: {item['error']}" for item in catalogue["errors"]][:20]}


@router.get("/database-copies")
async def database_copies(_: AdminUser = Depends(require_admin)) -> dict:
    """The database copies in Drive, newest first, to download by hand if ever needed."""

    folder = (read_backup_settings().get("folders") or {}).get(DATABASE)
    if not folder:
        return {"items": []}
    drive = DriveBackups(await access_token())
    try:
        rows = sorted(await drive.children(folder), key=lambda row: row.get("createdTime", ""), reverse=True)
        saved = read_backup_settings()
        kept = copies_to_keep([row for row in rows if (row.get("appProperties") or {}).get("kind") == "database"],
                              **retention_settings(saved))
        index = read_copy_index()
        return {"items": [{"id": row["id"], "name": row["name"], "size": int(row["size"]) if row.get("size") else None,
                           "created_at": row.get("createdTime"),
                           # Why it is still there; empty means the next copy trashes it.
                           "kept_by": kept.get(row["id"], []),
                           "torrents": len((index.get(row["id"]) or {}).get("torrents") or []) or None,
                           "url": f"https://drive.google.com/file/d/{row['id']}/view"} for row in rows]}
    finally:
        await drive.close()
