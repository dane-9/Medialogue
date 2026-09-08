"""Encrypted Google Drive snapshots with bounded retries and verified retention."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import AppError
from app.db import session as db_session
from app.models.domain import Job, JobStatus
from app.services.backup_crypto import encrypt_backup
from app.services.jobs import create_job, update_job
from app.services.recovery import recovery_bundle_path, run_recovery_export
from app.services.torrent_archive import _atomic_write, recovery_revision

DRIVE = "https://www.googleapis.com/drive/v3/files"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FIELDS = "id,name,size,createdTime,sha256Checksum,appProperties"
STORE_AAD = b"medialogue-backup-settings-v1"
backup_lock = asyncio.Lock()


def read_backup_settings() -> dict:
    settings = get_settings()
    path = Path(settings.config_dir) / "backup-settings.enc"
    if not path.exists():
        return {"keep_daily": 7, "keep_weekly": 4}
    payload = path.read_bytes()
    key = hashlib.sha256(settings.secret_key.encode()).digest()
    return json.loads(AESGCM(key).decrypt(payload[:12], payload[12:], STORE_AAD))


def update_backup_settings(changes: dict) -> dict:
    state = read_backup_settings()
    state.update(changes)
    state.setdefault("instance_id", str(uuid4()))
    nonce = os.urandom(12)
    settings = get_settings()
    key = hashlib.sha256(settings.secret_key.encode()).digest()
    _atomic_write(Path(settings.config_dir) / "backup-settings.enc",
                  nonce + AESGCM(key).encrypt(nonce, json.dumps(state).encode(), STORE_AAD))
    return state


def oauth_ready() -> bool:
    s = get_settings()
    return bool(s.google_drive_client_id and s.google_drive_client_secret and s.google_drive_redirect_uri)


def begin_oauth(admin_id: str) -> str:
    if not oauth_ready():
        raise AppError("DRIVE_SETUP_REQUIRED", "Configure Google OAuth client ID, secret and redirect URI first.", status_code=409)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    update_backup_settings({"oauth_state": state, "oauth_admin": admin_id,
                            "oauth_expires": time.time() + 600, "oauth_verifier": verifier})
    s = get_settings()
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": s.google_drive_client_id, "redirect_uri": s.google_drive_redirect_uri,
        "response_type": "code", "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
    })


async def finish_oauth(code: str, state: str, admin_id: str) -> None:
    saved = read_backup_settings()
    if (not secrets.compare_digest(state.encode(), (saved.get("oauth_state") or "").encode())
            or saved.get("oauth_admin") != admin_id or saved.get("oauth_expires", 0) < time.time()):
        raise AppError("DRIVE_OAUTH_STATE_INVALID", "Google connection expired. Start connecting again.", status_code=400)
    update_backup_settings({"oauth_state": None, "oauth_expires": 0})
    s = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(TOKEN, data={"code": code, "grant_type": "authorization_code",
            "client_id": s.google_drive_client_id, "client_secret": s.google_drive_client_secret,
            "redirect_uri": s.google_drive_redirect_uri, "code_verifier": saved["oauth_verifier"]})
    if response.status_code != 200:
        raise AppError("DRIVE_AUTH_FAILED", "Google authorization failed. Connect again.", status_code=400)
    payload = response.json()
    if not payload.get("refresh_token") or SCOPE not in payload.get("scope", "").split():
        raise AppError("DRIVE_AUTH_FAILED", "Google did not grant offline backup access. Connect again and allow Drive access.", status_code=400)
    # A new account must not inherit a folder ID or retention ownership from another account.
    update_backup_settings({"refresh_token": payload["refresh_token"], "folder_id": None,
                            "instance_id": str(uuid4()), "oauth_verifier": None,
                            "coverage": {}, "last_success_at": None, "last_file_id": None})


async def access_token() -> str:
    saved = read_backup_settings()
    if not saved.get("refresh_token"):
        raise AppError("DRIVE_NOT_CONNECTED", "Connect Google Drive first.", status_code=409)
    s = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(TOKEN, data={"grant_type": "refresh_token",
            "refresh_token": saved["refresh_token"], "client_id": s.google_drive_client_id,
            "client_secret": s.google_drive_client_secret})
    if response.status_code != 200:
        raise AppError("DRIVE_RECONNECT_REQUIRED", "Google Drive authorization failed. Reconnect your account.", status_code=409)
    return response.json()["access_token"]


class DriveBackups:
    def __init__(self, token: str):
        self.client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)

    async def close(self):
        await self.client.aclose()

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(5):
            try:
                response = await self.client.request(method, url, **kwargs)
                if response.status_code == 401 and attempt == 0:
                    self.client.headers["Authorization"] = f"Bearer {await access_token()}"
                    continue
                if response.status_code not in {429, 500, 502, 503, 504}:
                    if response.status_code >= 400:
                        raise AppError("DRIVE_REQUEST_FAILED", f"Google Drive returned HTTP {response.status_code}. Check account access and storage space.", status_code=502)
                    return response
            except httpx.TransportError:
                if attempt == 4:
                    raise AppError("DRIVE_UNREACHABLE", "Google Drive could not be reached. The previous backup is retained.", status_code=502) from None
            await asyncio.sleep(min(2 ** attempt, 8))
        raise AppError("DRIVE_BUSY", "Google Drive is temporarily unavailable. Retry the backup.", status_code=502)

    async def list(self) -> list[dict]:
        rows = []
        page = None
        while True:
            params = {"q": "trashed = false and appProperties has { key='medialogue' and value='backup-v1' }",
                      "fields": f"nextPageToken,files({FIELDS})", "pageSize": "100"}
            if page:
                params["pageToken"] = page
            payload = (await self.request("GET", DRIVE, params=params)).json()
            rows.extend(payload.get("files", []))
            page = payload.get("nextPageToken")
            if not page:
                return sorted(rows, key=lambda row: row.get("createdTime", ""), reverse=True)

    async def folder(self) -> str:
        state = read_backup_settings()
        if state.get("folder_id"):
            return state["folder_id"]
        response = await self.request("POST", DRIVE, json={"name": "Medialogue Backups",
            "mimeType": "application/vnd.google-apps.folder"}, params={"fields": "id"})
        folder_id = response.json()["id"]
        update_backup_settings({"folder_id": folder_id})
        return folder_id

    async def upload(self, path: Path, snapshot_id: str) -> dict:
        state = read_backup_settings()
        response = await self.request("POST", "https://www.googleapis.com/upload/drive/v3/files",
            params={"uploadType": "resumable", "fields": FIELDS},
            headers={"X-Upload-Content-Type": "application/octet-stream", "X-Upload-Content-Length": str(path.stat().st_size)},
            json={"name": path.name, "parents": [await self.folder()], "appProperties": {
                "medialogue": "backup-v1", "instance": state["instance_id"], "snapshot": snapshot_id}})
        session = response.headers.get("Location", "")
        parsed = urlsplit(session)
        if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com":
            raise ValueError("Google returned an invalid upload endpoint")
        total = path.stat().st_size
        offset = 0
        failures = 0
        with path.open("rb") as reader:
            while offset < total:
                reader.seek(offset)
                block = reader.read(4 * 1024 * 1024)
                try:
                    response = await self.client.put(session, content=block, headers={
                        "Content-Range": f"bytes {offset}-{offset + len(block) - 1}/{total}"})
                except httpx.TransportError:
                    response = None
                if response is not None and response.status_code == 401:
                    self.client.headers["Authorization"] = f"Bearer {await access_token()}"
                    response = None
                if response is None or response.status_code in {429, 500, 502, 503, 504}:
                    failures += 1
                    if failures > 5:
                        raise ValueError("Upload interrupted repeatedly; retry the backup. Earlier backups are retained.")
                    await asyncio.sleep(min(2 ** failures, 8))
                    response = await self.request("PUT", session, content=b"", headers={"Content-Range": f"bytes */{total}"})
                if response.status_code in {200, 201}:
                    return response.json()
                if response.status_code != 308:
                    raise ValueError(f"Google upload failed (HTTP {response.status_code})")
                acknowledged = response.headers.get("Range", "")
                next_offset = int(acknowledged.rsplit("-", 1)[1]) + 1 if acknowledged else 0
                if next_offset <= offset:
                    failures += 1
                    if failures > 5:
                        raise ValueError("Google upload made no progress")
                offset = next_offset
        raise ValueError("Google did not confirm upload completion")


def retained_ids(rows: list[dict], daily: int, weekly: int) -> set[str]:
    keep: set[str] = set()
    days: set[str] = set()
    weeks: set[str] = set()
    for row in sorted(rows, key=lambda item: item["createdTime"], reverse=True):
        stamp = datetime.fromisoformat(row["createdTime"].replace("Z", "+00:00"))
        day = stamp.date().isoformat()
        week = str(stamp.isocalendar()[:2])
        if day not in days and len(days) < daily:
            keep.add(row["id"])
            days.add(day)
        if week not in weeks and len(weeks) < weekly:
            keep.add(row["id"])
            weeks.add(week)
    return keep


async def run_remote_backup(job_id: UUID) -> None:
    async with backup_lock:
        encrypted = None
        bundle = None
        drive = None
        try:
            state = read_backup_settings()
            if not state.get("recovery_key"):
                raise ValueError("Save a recovery key before creating a remote backup")
            token = await access_token()
            async with db_session.async_session_factory() as db:
                job = await db.get(Job, job_id)
                await update_job(db, job, status=JobStatus.RUNNING, progress={"percent": 5, "stage": "snapshot"})
                export = await create_job(db, "recovery_export", cancellable=False, summary={"remote_backup": str(job_id)})
                await db.commit()
                export_id = export.id
            await run_recovery_export(export_id)
            bundle = recovery_bundle_path(export_id)
            if not bundle.is_file():
                raise ValueError("The recovery snapshot failed; inspect the Recovery Export task")
            coverage = {}
            unreadable_manifests = 0
            with zipfile.ZipFile(bundle) as snapshot:
                for name in snapshot.namelist():
                    if name.replace("\\", "/").startswith("manifests/") and name.endswith(".json"):
                        try:
                            manifest = json.loads(snapshot.read(name))
                            coverage[str(manifest["torrent_info_hash"]).lower()] = recovery_revision(manifest)
                        except (ValueError, KeyError, TypeError, AttributeError):
                            unreadable_manifests += 1
            encrypted = bundle.with_name(f"medialogue-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{job_id}.mlbackup")
            await asyncio.to_thread(encrypt_backup, bundle, encrypted, state["recovery_key"])
            bundle.unlink()
            digest = await asyncio.to_thread(file_digest, encrypted)
            drive = DriveBackups(token)
            uploaded = await drive.upload(encrypted, str(job_id))
            verified = (await drive.request("GET", f"{DRIVE}/{uploaded['id']}", params={"fields": FIELDS})).json()
            if verified.get("sha256Checksum") != digest or int(verified.get("size", -1)) != encrypted.stat().st_size:
                raise ValueError("Remote upload verification failed; earlier backups are retained")
            # Only verified files can become retention candidates.
            properties = {**verified.get("appProperties", {}), "verified": "true"}
            await drive.request("PATCH", f"{DRIVE}/{verified['id']}", json={"appProperties": properties})
            now = datetime.now(timezone.utc).isoformat()
            update_backup_settings({"last_success_at": now, "last_file_id": verified["id"], "last_error": None,
                                    "coverage": coverage})
            cleanup_warning = None
            try:
                rows = [row for row in await drive.list() if row.get("appProperties", {}).get("instance") == state["instance_id"]
                        and row.get("appProperties", {}).get("verified") == "true"]
                keep = retained_ids(rows, int(state["keep_daily"]), int(state["keep_weekly"])) | {verified["id"]}
                for row in rows:
                    if row["id"] not in keep:
                        await drive.request("PATCH", f"{DRIVE}/{row['id']}", json={"trashed": True})
            except Exception:
                cleanup_warning = "Backup verified, but older backups could not be pruned. They have been retained."
            async with db_session.async_session_factory() as db:
                job = await db.get(Job, job_id)
                await update_job(db, job, status=JobStatus.COMPLETED, progress={"percent": 100, "stage": "verified"},
                    summary={"message": "Encrypted Google Drive backup verified.", "file_id": verified["id"],
                             "sha256": digest, "size": encrypted.stat().st_size, "warning": cleanup_warning,
                             "torrent_count": len(coverage), "unreadable_manifests": unreadable_manifests})
                await db.commit()
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            update_backup_settings({"last_error": message})
            async with db_session.async_session_factory() as db:
                job = await db.get(Job, job_id)
                if job:
                    await update_job(db, job, status=JobStatus.FAILED, error={"code": "REMOTE_BACKUP_FAILED", "message": message})
                    await db.commit()
        finally:
            if drive:
                await drive.close()
            if encrypted:
                encrypted.unlink(missing_ok=True)
            if bundle:
                bundle.unlink(missing_ok=True)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while block := reader.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
