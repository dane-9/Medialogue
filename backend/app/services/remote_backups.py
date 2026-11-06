"""Google Drive copy of the torrent archive, for rebuilding after losing the server.

Drive holds plain files in a "Medialogue Backups" folder:

* ``torrents/<info hash>.torrent`` and ``manifests/<info hash>.json``, mirrored
  from the local archive within minutes of changing. Each manifest says where
  its torrent downloaded to, so the files alone are enough to re-add
  everything to qBittorrent at its original location.
* ``database/medialogue-database-<time>.zip``: the database and configuration,
  on their own slower schedule, keeping the newest few.

Nothing is encrypted. The torrent files carry tracker passkeys, so anyone with
access to the folder can use those tracker accounts.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import application_secret_path, get_settings
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.db import session as db_session
from app.models.domain import Job, JobStatus
from app.services.jobs import update_job
from app.services.torrent_archive import _atomic_write, _safe_hash, archive_paths, recovery_revision

DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
FIELDS = "id,name,size,createdTime,sha256Checksum,appProperties,trashed"
STORE_AAD = b"medialogue-backup-settings-v1"
ROOT_FOLDER = "Medialogue Backups"
MIRRORED = ("torrents", "manifests")
DATABASE = "database"
#: Marks the files Medialogue owns in Drive.
MARK = {"medialogue": "mirror-v1"}
DEFAULT_KEEP_DATABASE = 7
DEFAULT_DATABASE_INTERVAL = 86400
#: Uploads in flight at once while mirroring.
PARALLEL_UPLOADS = 4
#: Larger files go up in resumable chunks instead of one request.
SIMPLE_UPLOAD_LIMIT = 5 * 1024 * 1024
backup_lock = asyncio.Lock()


# --- Settings ---------------------------------------------------------------


def _settings_path() -> Path:
    return Path(get_settings().config_dir) / "backup-settings.enc"


def read_backup_settings() -> dict:
    """Connection and mirror state, stored encrypted with the app secret (it holds the Drive token)."""

    defaults = {"keep_database": DEFAULT_KEEP_DATABASE, "database_interval_seconds": DEFAULT_DATABASE_INTERVAL}
    path = _settings_path()
    if not path.exists():
        return defaults
    payload = path.read_bytes()
    key = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return {**defaults, **json.loads(AESGCM(key).decrypt(payload[:12], payload[12:], STORE_AAD))}


def update_backup_settings(changes: dict) -> dict:
    state = read_backup_settings()
    state.update(changes)
    state.setdefault("instance_id", str(uuid4()))
    nonce = os.urandom(12)
    key = hashlib.sha256(get_settings().secret_key.encode()).digest()
    _atomic_write(_settings_path(), nonce + AESGCM(key).encrypt(nonce, json.dumps(state).encode(), STORE_AAD))
    return state


# --- Google account ---------------------------------------------------------


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
    # A different account starts from an empty mirror; the next run rebuilds
    # what this account already holds from Drive itself.
    update_backup_settings({"refresh_token": payload["refresh_token"], "folders": {}, "mirror": {},
                            "oauth_verifier": None, "last_sync_at": None, "last_database_at": None})


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


# --- Drive API --------------------------------------------------------------


class DriveMissing(Exception):
    """A file or folder Medialogue expected is gone from Drive."""


class DriveBackups:
    def __init__(self, token: str):
        self.client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)

    async def close(self):
        await self.client.aclose()

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """One Drive call, retried on rate limits, server errors and dropped connections."""

        for attempt in range(5):
            try:
                response = await self.client.request(method, url, **kwargs)
                if response.status_code == 401 and attempt == 0:
                    self.client.headers["Authorization"] = f"Bearer {await access_token()}"
                    continue
                if response.status_code == 404:
                    raise DriveMissing(url)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    if response.status_code >= 400:
                        raise AppError("DRIVE_REQUEST_FAILED", f"Google Drive returned HTTP {response.status_code}. Check account access and storage space.", status_code=502)
                    return response
            except httpx.TransportError:
                if attempt == 4:
                    raise AppError("DRIVE_UNREACHABLE", "Google Drive could not be reached. Nothing already backed up was affected.", status_code=502) from None
            await asyncio.sleep(min(2 ** attempt, 8))
        raise AppError("DRIVE_BUSY", "Google Drive is temporarily unavailable. The next run tries again.", status_code=502)

    async def children(self, folder_id: str) -> list[dict]:
        rows: list[dict] = []
        page = None
        while True:
            params = {"q": f"'{folder_id}' in parents and trashed = false",
                      "fields": f"nextPageToken,files({FIELDS})", "pageSize": "1000"}
            if page:
                params["pageToken"] = page
            payload = (await self.request("GET", DRIVE, params=params)).json()
            rows.extend(payload.get("files", []))
            page = payload.get("nextPageToken")
            if not page:
                return rows

    async def folder(self, name: str, parent: str | None) -> str:
        """The id of a folder by name, created when missing."""

        query = f"name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        query += f" and '{parent}' in parents" if parent else " and 'root' in parents"
        found = (await self.request("GET", DRIVE, params={"q": query, "fields": "files(id)", "pageSize": "10"})).json()
        if found.get("files"):
            return found["files"][0]["id"]
        body: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME, "appProperties": MARK}
        if parent:
            body["parents"] = [parent]
        return (await self.request("POST", DRIVE, json=body, params={"fields": "id"})).json()["id"]

    async def exists(self, file_id: str) -> bool:
        try:
            row = (await self.request("GET", f"{DRIVE}/{file_id}", params={"fields": "id,trashed"})).json()
        except DriveMissing:
            return False
        return not row.get("trashed")

    async def put_small(self, data: bytes, *, name: str, parent: str, properties: dict, file_id: str | None = None) -> dict:
        """Create a file, or replace a file's content, in one request."""

        boundary = secrets.token_hex(16)
        metadata: dict[str, Any] = {"name": name, "appProperties": {**MARK, **properties}}
        if file_id is None:
            metadata["parents"] = [parent]
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--".encode()
        url = f"{UPLOAD}/{file_id}" if file_id else UPLOAD
        response = await self.request("PATCH" if file_id else "POST", url,
            params={"uploadType": "multipart", "fields": FIELDS}, content=body,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        return response.json()

    async def put_large(self, path: Path, *, name: str, parent: str, properties: dict) -> dict:
        """Create a file in resumable chunks, picking up where a dropped chunk left off."""

        response = await self.request("POST", UPLOAD,
            params={"uploadType": "resumable", "fields": FIELDS},
            headers={"X-Upload-Content-Type": "application/octet-stream", "X-Upload-Content-Length": str(path.stat().st_size)},
            json={"name": name, "parents": [parent], "appProperties": {**MARK, **properties}})
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
                        raise ValueError("Upload interrupted repeatedly; the next run tries again.")
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

    async def download(self, file_id: str) -> bytes:
        return (await self.request("GET", f"{DRIVE}/{file_id}", params={"alt": "media"})).content

    async def trash(self, file_id: str) -> None:
        await self.request("PATCH", f"{DRIVE}/{file_id}", json={"trashed": True})


def _checked(row: dict, digest: str, size: int) -> dict:
    """Drive's own checksum and size must match what was sent."""

    if row.get("sha256Checksum") and row["sha256Checksum"] != digest:
        raise ValueError(f"Drive stored a different checksum for {row.get('name')}")
    if row.get("size") is not None and int(row["size"]) != size:
        raise ValueError(f"Drive stored a different size for {row.get('name')}")
    return row


async def drive_folders(drive: DriveBackups, state: dict) -> tuple[dict[str, str], bool]:
    """The backup folder and its subfolders, and whether any had to be made.

    A folder deleted in Drive is recreated, and then everything is uploaded
    again rather than trusting a mirror that no longer exists.
    """

    folders = dict(state.get("folders") or {})
    if folders.get("root") and await drive.exists(folders["root"]):
        missing = [name for name in (*MIRRORED, DATABASE) if not folders.get(name)]
        if not missing:
            return folders, False
    else:
        folders = {"root": await drive.folder(ROOT_FOLDER, None)}
    for name in (*MIRRORED, DATABASE):
        folders[name] = await drive.folder(name, folders["root"])
    return folders, True


# --- Mirroring --------------------------------------------------------------


def _local_files(kind: str) -> list[Path]:
    root = Path(get_settings().torrent_archive_dir).resolve() / kind
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and not path.name.startswith(".") and not path.name.endswith(".tmp")
    )


def _fingerprint(kind: str, data: bytes) -> str:
    """What decides whether a file must go up again.

    A manifest is rewritten whenever live telemetry is refreshed; only a change
    to what a restore uses (paths, files, settings) is worth uploading.
    """

    if kind == "manifests":
        try:
            return "r:" + recovery_revision(json.loads(data))
        except (ValueError, TypeError, AttributeError):
            pass
    return "s:" + hashlib.sha256(data).hexdigest()


async def _rebuild_mirror(drive: DriveBackups, folders: dict[str, str]) -> dict[str, dict]:
    """What Drive already holds, so a lost local state does not re-upload everything."""

    mirror: dict[str, dict] = {}
    for kind in MIRRORED:
        for row in await drive.children(folders[kind]):
            fingerprint = (row.get("appProperties") or {}).get("fingerprint")
            if fingerprint:
                mirror[f"{kind}/{row['name']}"] = {"id": row["id"], "fingerprint": fingerprint}
    return mirror


async def mirror_archive(drive: DriveBackups, state: dict, folders: dict[str, str], *, rebuilt: bool) -> dict[str, Any]:
    """Upload every torrent and manifest that is new or changed since the last run."""

    mirror: dict[str, dict] = {} if rebuilt else dict(state.get("mirror") or {})
    if not mirror:
        mirror = await _rebuild_mirror(drive, folders)
    work: list[tuple[str, Path, os.stat_result]] = []
    unchanged = 0
    for kind in MIRRORED:
        for path in _local_files(kind):
            stat = path.stat()
            entry = mirror.get(f"{kind}/{path.name}")
            if entry and entry.get("size") == stat.st_size and entry.get("mtime") == stat.st_mtime_ns:
                unchanged += 1
                continue
            work.append((kind, path, stat))

    counts = {"uploaded": 0, "updated": 0, "unchanged": unchanged, "failed": 0}
    errors: list[str] = []
    gate = asyncio.Semaphore(PARALLEL_UPLOADS)

    async def push(kind: str, path: Path, stat: os.stat_result) -> None:
        key = f"{kind}/{path.name}"
        async with gate:
            try:
                data = await asyncio.to_thread(path.read_bytes)
                fingerprint = _fingerprint(kind, data)
                entry = mirror.get(key)
                if entry and entry.get("fingerprint") == fingerprint:
                    mirror[key] = {**entry, "size": stat.st_size, "mtime": stat.st_mtime_ns}
                    counts["unchanged"] += 1
                    return
                digest = hashlib.sha256(data).hexdigest()
                properties = {"fingerprint": fingerprint}
                row = None
                if entry:
                    try:
                        row = await drive.put_small(data, name=path.name, parent=folders[kind], properties=properties, file_id=entry["id"])
                        counts["updated"] += 1
                    except DriveMissing:
                        row = None
                if row is None:
                    row = await drive.put_small(data, name=path.name, parent=folders[kind], properties=properties)
                    counts["uploaded"] += 1
                _checked(row, digest, len(data))
                mirror[key] = {"id": row["id"], "fingerprint": fingerprint, "size": stat.st_size, "mtime": stat.st_mtime_ns}
            except Exception as exc:
                counts["failed"] += 1
                message = exc.message if isinstance(exc, AppError) else str(exc)
                if len(errors) < 5:
                    errors.append(f"{path.name}: {message}")

    # Saved after every batch, so a run cut short keeps what it already sent.
    for start in range(0, len(work), 50):
        await asyncio.gather(*(push(*item) for item in work[start:start + 50]))
        update_backup_settings({"mirror": mirror})
    update_backup_settings({"mirror": mirror})
    return {**counts, "errors": errors, "torrents": sum(1 for key in mirror if key.startswith("torrents/"))}


# --- Database ---------------------------------------------------------------


def _database_due(state: dict) -> bool:
    last = state.get("last_database_at")
    if not last:
        return True
    interval = int(state.get("database_interval_seconds") or DEFAULT_DATABASE_INTERVAL)
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(seconds=interval)


async def backup_database(drive: DriveBackups, state: dict, folders: dict[str, str]) -> dict[str, Any]:
    """Upload the database and configuration as one ZIP, keeping the newest few."""

    from app.services.recovery import run_sqlite_backup

    settings = get_settings()
    work = Path(tempfile.mkdtemp(prefix="medialogue-database-"))
    try:
        await run_sqlite_backup(work / "database" / "medialogue.db", settings)
        store = get_integration_config_store()
        sources = [store.config_path, store.secrets_path,
                   Path(settings.config_dir) / "setup-state.json", Path(settings.config_dir) / "custom-format-layout.json",
                   application_secret_path(settings)]
        for source in sources:
            if source.is_file():
                (work / "config").mkdir(exist_ok=True)
                shutil.copy2(source, work / "config" / source.name)
        name = f"medialogue-database-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.zip"
        bundle = work.parent / f"{work.name}.zip"

        def pack() -> None:
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(work.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(work).as_posix())

        await asyncio.to_thread(pack)
        data_size = bundle.stat().st_size
        digest = await asyncio.to_thread(file_digest, bundle)
        if data_size <= SIMPLE_UPLOAD_LIMIT:
            row = await drive.put_small(bundle.read_bytes(), name=name, parent=folders[DATABASE], properties={"kind": "database"})
        else:
            row = await drive.put_large(bundle, name=name, parent=folders[DATABASE], properties={"kind": "database"})
        if not row.get("sha256Checksum"):
            row = (await drive.request("GET", f"{DRIVE}/{row['id']}", params={"fields": FIELDS})).json()
        _checked(row, digest, data_size)
        bundle.unlink(missing_ok=True)

        # Older copies go only after this one is confirmed.
        keep = max(1, int(state.get("keep_database") or DEFAULT_KEEP_DATABASE))
        copies = sorted(
            (row for row in await drive.children(folders[DATABASE]) if (row.get("appProperties") or {}).get("kind") == "database"),
            key=lambda item: item.get("createdTime", ""), reverse=True,
        )
        for old in copies[keep:]:
            await drive.trash(old["id"])
        return {"file": name, "size": data_size}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        (work.parent / f"{work.name}.zip").unlink(missing_ok=True)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while block := reader.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


# --- Jobs -------------------------------------------------------------------


async def _finish(job_id: UUID, **values: Any) -> None:
    async with db_session.async_session_factory() as db:
        job = await db.get(Job, job_id)
        if job is not None:
            await update_job(db, job, **values)
            await db.commit()


async def run_remote_backup(job_id: UUID) -> None:
    """Mirror the archive to Drive, and the database when it is due (or asked for)."""

    async with backup_lock:
        drive = None
        try:
            async with db_session.async_session_factory() as db:
                job = await db.get(Job, job_id)
                if job is None:
                    return
                manual = (job.summary or {}).get("trigger") == "manual"
                await update_job(db, job, status=JobStatus.RUNNING, progress={"percent": 5, "stage": "connecting"})
                await db.commit()
            state = read_backup_settings()
            drive = DriveBackups(await access_token())
            folders, rebuilt = await drive_folders(drive, state)
            update_backup_settings({"folders": folders})
            await _finish(job_id, progress={"percent": 15, "stage": "torrents"})
            mirrored = await mirror_archive(drive, read_backup_settings(), folders, rebuilt=rebuilt)
            now = datetime.now(timezone.utc).isoformat()
            changes: dict[str, Any] = {"last_sync_at": now}
            database = None
            if manual or _database_due(read_backup_settings()):
                await _finish(job_id, progress={"percent": 70, "stage": "database"})
                database = await backup_database(drive, read_backup_settings(), folders)
                changes["last_database_at"] = now
            failed = mirrored["failed"]
            changes["last_error"] = (f"{failed} file(s) could not be uploaded: " + "; ".join(mirrored["errors"])) if failed else None
            update_backup_settings(changes)
            sent = mirrored["uploaded"] + mirrored["updated"]
            message = f"{sent} torrent file(s) uploaded" if sent else "Torrents already up to date"
            if database:
                message += ", database backed up"
            if failed:
                message += f", {failed} failed"
            await _finish(job_id, status=JobStatus.COMPLETED if not failed else JobStatus.FAILED,
                          progress={"percent": 100, "stage": "completed"},
                          summary={"message": f"{message}.", **{key: value for key, value in mirrored.items() if key != "errors"},
                                   "errors": mirrored["errors"], "database": database},
                          error={"code": "REMOTE_BACKUP_PARTIAL", "message": changes["last_error"]} if failed else None)
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            update_backup_settings({"last_error": message})
            await _finish(job_id, status=JobStatus.FAILED, error={"code": "REMOTE_BACKUP_FAILED", "message": message})
        finally:
            if drive:
                await drive.close()


async def run_drive_restore(job_id: UUID) -> None:
    """Bring every torrent and manifest from Drive into the local archive.

    Files already present and identical are left alone, so it is safe to run
    again. The archive list is rebuilt from the manifests afterwards; nothing
    is sent to qBittorrent until the re-import is confirmed.
    """

    from app.services.torrent_archive import rebuild_catalogue_from_manifests

    async with backup_lock:
        drive = None
        try:
            await _finish(job_id, status=JobStatus.RUNNING, progress={"percent": 2, "stage": "connecting"})
            state = read_backup_settings()
            drive = DriveBackups(await access_token())
            folders, _ = await drive_folders(drive, state)
            update_backup_settings({"folders": folders})
            rows = [(kind, row) for kind in MIRRORED for row in await drive.children(folders[kind])]
            counts = {"downloaded": 0, "present": 0, "skipped": 0}
            gate = asyncio.Semaphore(PARALLEL_UPLOADS)

            async def fetch(kind: str, row: dict) -> None:
                name = str(row.get("name") or "")
                stem, _, suffix = name.rpartition(".")
                try:
                    info_hash = _safe_hash(stem)
                except ValueError:
                    counts["skipped"] += 1
                    return
                if (kind, suffix) not in {("torrents", "torrent"), ("manifests", "json")}:
                    counts["skipped"] += 1
                    return
                torrent_path, manifest_path = archive_paths(info_hash)
                target = torrent_path if kind == "torrents" else manifest_path
                if target.is_file() and row.get("sha256Checksum") and file_digest(target) == row["sha256Checksum"]:
                    counts["present"] += 1
                    return
                async with gate:
                    data = await drive.download(row["id"])
                if row.get("sha256Checksum") and hashlib.sha256(data).hexdigest() != row["sha256Checksum"]:
                    raise ValueError(f"{name} did not match its Drive checksum")
                if kind == "manifests":
                    json.loads(data)
                await asyncio.to_thread(_atomic_write, target, data)
                counts["downloaded"] += 1

            for start in range(0, len(rows), 50):
                await asyncio.gather(*(fetch(kind, row) for kind, row in rows[start:start + 50]))
                await _finish(job_id, progress={"percent": 5 + int(85 * min(len(rows), start + 50) / max(1, len(rows))),
                                                "stage": "downloading", "detail": f"{min(len(rows), start + 50)} of {len(rows)} files"})
            async with db_session.async_session_factory() as db:
                catalogue = await rebuild_catalogue_from_manifests(db)
                await db.commit()
            await _finish(job_id, status=JobStatus.COMPLETED, progress={"percent": 100, "stage": "completed"},
                          summary={"message": f"Fetched {counts['downloaded']} file(s) from Google Drive; "
                                              f"{catalogue['imported']} torrent(s) added to the archive list.",
                                   **counts, **{f"catalogue_{key}": value for key, value in catalogue.items() if key != "errors"},
                                   "errors": catalogue["errors"][:20]})
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            await _finish(job_id, status=JobStatus.FAILED, error={"code": "DRIVE_RESTORE_FAILED", "message": message})
        finally:
            if drive:
                await drive.close()
