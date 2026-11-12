"""Google Drive copy of the torrent archive, for rebuilding after losing the server.

Drive holds plain files in a "Medialogue Backups" folder:

* ``torrents/<info hash>.torrent`` and ``manifests/<info hash>.json``, mirrored
  from the local archive within minutes of changing. Each manifest says where
  its torrent downloaded to, so the files alone are enough to re-add
  everything to qBittorrent at its original location.
* ``database/medialogue-database-<time>.zip``: the database and configuration,
  on their own slower schedule. Each copy also holds the list of torrents in
  the archive at that moment and their manifests as they were, so an old copy
  restores the library it describes. Recent copies are all kept, older ones
  thinned to one a week, then one a month (see ``copies_to_keep``).

A torrent that left the archive leaves Drive only once no kept copy lists it.

Nothing is encrypted. The torrent files carry tracker passkeys, so anyone with
access to the folder can use those tracker accounts.
"""

from __future__ import annotations

import logging

import asyncio
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
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import application_secret_path, get_settings
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.db import session as db_session
from app.models.domain import Job, JobStatus
from app.services.jobs import update_job
from app.services.torrent_archive import _atomic_write, recovery_revision

logger = logging.getLogger(__name__)

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
DEFAULT_DATABASE_INTERVAL = 86400
#: How many copies each retention rule keeps (see ``copies_to_keep``).
DEFAULT_RETENTION = {"keep_recent": 0, "keep_daily": 7, "keep_weekly": 4, "keep_monthly": 6}
#: Inside each database copy: the torrents it covers, and their manifests then.
CATALOGUE_TORRENTS = "catalogue/torrents.json"
CATALOGUE_MANIFESTS = "catalogue/manifests"
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

    defaults = {"database_interval_seconds": DEFAULT_DATABASE_INTERVAL}
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


def retention_settings(state: dict) -> dict[str, int]:
    """The retention counts, defaults where unset."""

    values: dict[str, int] = {}
    for key, default in DEFAULT_RETENTION.items():
        raw = state.get(key)
        try:
            values[key] = max(0, int(raw if raw is not None else default))
        except (TypeError, ValueError):
            values[key] = default
    return values


def _created(row: dict) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(row.get("createdTime") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def copies_to_keep(copies: list[dict], *, keep_recent: int = 0, keep_daily: int = 7, keep_weekly: int = 4,
                   keep_monthly: int = 6) -> dict[str, list[str]]:
    """Which database copies stay, by file id, with the rules that keep each one.

    Every rule looks at the copies newest first and keeps the newest copy of
    each period, for as many periods (that have a copy) as it is allowed:

    - ``newest``: the latest copy, always;
    - ``recent``: the latest ``keep_recent`` copies, whatever their age;
    - ``daily``: one per day, for the last ``keep_daily`` days;
    - ``weekly``: one per ISO week, for the last ``keep_weekly`` weeks;
    - ``monthly``: one per month, for the last ``keep_monthly`` months.

    A copy whose time cannot be read is never deleted.
    """

    keep: dict[str, list[str]] = {}
    dated: list[tuple[datetime, dict]] = []
    for row in copies:
        created = _created(row)
        if created is None:
            keep.setdefault(row["id"], []).append("undated")
        else:
            dated.append((created, row))
    dated.sort(key=lambda item: item[0], reverse=True)
    if dated:
        keep.setdefault(dated[0][1]["id"], []).append("newest")
    for _, row in dated[:keep_recent]:
        keep.setdefault(row["id"], []).append("recent")
    periods = (
        ("daily", keep_daily, lambda value: value.date()),
        ("weekly", keep_weekly, lambda value: tuple(value.isocalendar())[:2]),
        ("monthly", keep_monthly, lambda value: (value.year, value.month)),
    )
    for rule, count, period in periods:
        seen: set = set()
        for created, row in dated:
            key = period(created)
            if key in seen:
                continue
            if len(seen) >= count:
                break
            seen.add(key)
            keep.setdefault(row["id"], []).append(rule)
    return keep


# --- Copy index ---------------------------------------------------------------


def _index_path() -> Path:
    return Path(get_settings().config_dir) / "backup-copies.json"


def read_copy_index() -> dict[str, dict]:
    """The torrents each database copy on Drive lists, by Drive file id."""

    try:
        value = json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_copy_index(index: dict[str, dict]) -> None:
    _atomic_write(_index_path(), json.dumps(index, sort_keys=True).encode("utf-8"))


def _catalogue_hashes(data: bytes) -> list[str] | None:
    """The torrent list inside a database copy; None when it cannot be read."""

    import io

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            payload = json.loads(archive.read(CATALOGUE_TORRENTS))
    except (KeyError, ValueError, zipfile.BadZipFile):
        return None
    torrents = payload.get("torrents") if isinstance(payload, dict) else None
    return [str(item).lower() for item in torrents] if isinstance(torrents, list) else None


async def protected_hashes(drive: "DriveBackups", folders: dict[str, str]) -> set[str]:
    """Every torrent a database copy still on Drive lists, so it stays on Drive.

    Copies the local index does not know (it was lost, or another install made
    them) are read from Drive once and remembered.
    """

    index = read_copy_index()
    copies = [row for row in await drive.children(folders[DATABASE])
              if (row.get("appProperties") or {}).get("kind") == "database"]
    changed = False
    for row in copies:
        if row["id"] in index:
            # Copies indexed before sizes were recorded learn theirs here.
            if index[row["id"]].get("size") is None and row.get("size") is not None:
                index[row["id"]]["size"] = int(row["size"])
                changed = True
            continue
        try:
            hashes = _catalogue_hashes(await drive.download(row["id"]))
        except DriveMissing:
            continue
        index[row["id"]] = {"name": row.get("name"), "created_at": row.get("createdTime"), "torrents": hashes or [],
                            "size": int(row["size"]) if row.get("size") is not None else None}
        changed = True
    live = {row["id"] for row in copies}
    for file_id in [file_id for file_id in index if file_id not in live]:
        del index[file_id]
        changed = True
    if changed:
        _write_copy_index(index)
    return {item for entry in index.values() for item in entry.get("torrents") or []}


def bytes_on_drive(state: dict) -> int:
    """Total size of the torrents, manifests and database copies Medialogue keeps on Drive.

    Read from local bookkeeping, so it reflects the last backup run; a file
    whose size is not yet known (state rebuilt before sizes were kept) counts
    once the next run records it.
    """

    mirrored = sum(int(entry.get("size") or 0) for entry in (state.get("mirror") or {}).values())
    copies = sum(int(entry.get("size") or 0) for entry in read_copy_index().values())
    return mirrored + copies


# --- Google account ---------------------------------------------------------
#
# Medialogue signs in with Google's device flow: it shows a short code, the
# administrator approves it at google.com/device on any device, and Medialogue
# polls until Google hands over the token. There is no redirect URI, so it
# works on a plain LAN address. The OAuth client must be of type "TVs and
# Limited Input devices". Its ID and secret are entered on the Backups page
# (kept encrypted here), or come from MEDIALOGUE_GOOGLE_DRIVE_CLIENT_ID/_SECRET.

DEVICE_CODE = "https://oauth2.googleapis.com/device/code"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def client_credentials(state: dict | None = None) -> tuple[str, str, str]:
    """The OAuth client ID and secret, and where they came from ("saved", "environment" or "")."""

    state = read_backup_settings() if state is None else state
    if state.get("client_id") and state.get("client_secret"):
        return state["client_id"], state["client_secret"], "saved"
    s = get_settings()
    if s.google_drive_client_id and s.google_drive_client_secret:
        return s.google_drive_client_id, s.google_drive_client_secret, "environment"
    return "", "", ""


def oauth_ready() -> bool:
    return bool(client_credentials()[2])


def save_client_credentials(client_id: str, client_secret: str) -> None:
    """A different client cannot use the account connected through the old one."""

    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com") or not client_secret:
        raise AppError("DRIVE_CLIENT_INVALID", "Paste the client ID (ending in .apps.googleusercontent.com) and its client secret.", status_code=422)
    state = read_backup_settings()
    changes: dict[str, Any] = {"client_id": client_id, "client_secret": client_secret, "device_code": None}
    if client_id != client_credentials(state)[0]:
        changes["refresh_token"] = None
    update_backup_settings(changes)


def clear_client_credentials() -> None:
    """Forget the saved client, and the account connected through it."""

    update_backup_settings({"client_id": None, "client_secret": None, "device_code": None, "refresh_token": None})


def _oauth_error(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error") or "")
    except ValueError:
        return ""


async def begin_device_sign_in(admin_id: str) -> dict:
    client_id, _, source = client_credentials()
    if not source:
        raise AppError("DRIVE_SETUP_REQUIRED", "Add the Google OAuth client ID and secret first.", status_code=409)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(DEVICE_CODE, data={"client_id": client_id, "scope": SCOPE})
        except httpx.HTTPError:
            raise AppError("DRIVE_UNREACHABLE", "Google could not be reached. Try again.", status_code=502) from None
    if response.status_code != 200:
        if _oauth_error(response) in {"invalid_client", "unauthorized_client"}:
            raise AppError("DRIVE_CLIENT_INVALID", "Google rejected this OAuth client. Create it as type \"TVs and Limited Input devices\", "
                           "in a project with the Google Drive API enabled.", status_code=400)
        raise AppError("DRIVE_AUTH_FAILED", "Google would not start the sign-in. Check the client ID and try again.", status_code=400)
    payload = response.json()
    expires_at = time.time() + int(payload.get("expires_in") or 1800)
    interval = max(1, int(payload.get("interval") or 5))
    update_backup_settings({"device_code": payload["device_code"], "device_admin": admin_id,
                            "device_expires": expires_at, "device_interval": interval, "device_next_poll": 0})
    return {"user_code": payload["user_code"],
            "verification_url": payload.get("verification_url") or "https://www.google.com/device",
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(), "interval": interval}


async def check_device_sign_in(admin_id: str) -> str:
    """'pending' until the code is approved at google.com/device, then 'connected'."""

    saved = read_backup_settings()
    if not saved.get("device_code") or saved.get("device_admin") != admin_id:
        raise AppError("DRIVE_OAUTH_STATE_INVALID", "No Google sign-in is in progress. Start connecting again.", status_code=409)
    if saved.get("device_expires", 0) < time.time():
        update_backup_settings({"device_code": None})
        raise AppError("DRIVE_OAUTH_STATE_INVALID", "The sign-in code expired. Start connecting again.", status_code=409)
    if saved.get("device_next_poll", 0) > time.time():
        return "pending"  # Google asks for its interval between polls.
    client_id, client_secret, _ = client_credentials(saved)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(TOKEN, data={"client_id": client_id, "client_secret": client_secret,
                                                      "device_code": saved["device_code"], "grant_type": DEVICE_GRANT})
        except httpx.HTTPError:
            return "pending"
    if response.status_code != 200:
        error = _oauth_error(response)
        if error in {"authorization_pending", "slow_down"}:
            interval = int(saved.get("device_interval") or 5) + (5 if error == "slow_down" else 0)
            update_backup_settings({"device_interval": interval, "device_next_poll": time.time() + interval})
            return "pending"
        update_backup_settings({"device_code": None})
        if error == "access_denied":
            raise AppError("DRIVE_AUTH_DENIED", "Access was declined in Google. Connect again and allow Drive access.", status_code=400)
        if error == "expired_token":
            raise AppError("DRIVE_OAUTH_STATE_INVALID", "The sign-in code expired. Start connecting again.", status_code=409)
        raise AppError("DRIVE_AUTH_FAILED", "Google authorization failed. Check the client secret and connect again.", status_code=400)
    payload = response.json()
    if not payload.get("refresh_token") or SCOPE not in payload.get("scope", "").split():
        update_backup_settings({"device_code": None})
        raise AppError("DRIVE_AUTH_FAILED", "Google did not grant offline backup access. Connect again and allow Drive access.", status_code=400)
    # A different account starts from an empty mirror; the next run rebuilds
    # what this account already holds from Drive itself.
    update_backup_settings({"refresh_token": payload["refresh_token"], "folders": {}, "mirror": {}, "device_code": None,
                            "last_sync_at": None, "last_database_at": None, "last_error": None})
    return "connected"


async def access_token() -> str:
    saved = read_backup_settings()
    if not saved.get("refresh_token"):
        raise AppError("DRIVE_NOT_CONNECTED", "Connect Google Drive first.", status_code=409)
    client_id, client_secret, _ = client_credentials(saved)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(TOKEN, data={"grant_type": "refresh_token",
            "refresh_token": saved["refresh_token"], "client_id": client_id, "client_secret": client_secret})
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
                mirror[f"{kind}/{row['name']}"] = {"id": row["id"], "fingerprint": fingerprint,
                                                   **({"size": int(row["size"])} if row.get("size") is not None else {})}
    return mirror


async def mirror_archive(drive: DriveBackups, state: dict, folders: dict[str, str], *, rebuilt: bool,
                         retired: set[str] = frozenset()) -> dict[str, Any]:
    """Upload every torrent and manifest that is new or changed since the last run.

    ``retired`` are info hashes pruned from the archive long enough ago to
    leave Drive too. Nothing else is ever deleted from Drive: a file missing
    locally for any other reason stays.
    """

    mirror: dict[str, dict] = {} if rebuilt else dict(state.get("mirror") or {})
    if not mirror:
        mirror = await _rebuild_mirror(drive, folders)
    removed = 0
    for key in [key for key in mirror if key.split("/", 1)[1].rsplit(".", 1)[0].lower() in retired]:
        try:
            await drive.trash(mirror[key]["id"])
        except DriveMissing:
            pass
        del mirror[key]
        removed += 1
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
    return {**counts, "removed": removed, "errors": errors, "torrents": sum(1 for key in mirror if key.startswith("torrents/"))}


# --- Database ---------------------------------------------------------------


def _database_due(state: dict) -> bool:
    last = state.get("last_database_at")
    if not last:
        return True
    interval = int(state.get("database_interval_seconds") or DEFAULT_DATABASE_INTERVAL)
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(seconds=interval)


async def backup_database(drive: DriveBackups, state: dict, folders: dict[str, str]) -> dict[str, Any]:
    """Upload the database, configuration and torrent list as one ZIP, then thin old copies."""

    from app.services.recovery import run_sqlite_backup

    settings = get_settings()
    work = Path(tempfile.mkdtemp(prefix="medialogue-database-"))
    try:
        await run_sqlite_backup(work / "database" / "medialogue.db", settings)
        store = get_integration_config_store()
        sources = [store.config_path, store.secrets_path,
                   Path(settings.config_dir) / "custom-format-layout.json",
                   application_secret_path(settings)]
        for source in sources:
            if source.is_file():
                (work / "config").mkdir(exist_ok=True)
                shutil.copy2(source, work / "config" / source.name)
        # What this copy covers: the torrents an old copy needs kept on Drive,
        # and their manifests as they were, so it restores the paths it knew.
        hashes = sorted({path.stem.lower() for path in _local_files("torrents") if path.suffix == ".torrent"})
        catalogue = work / CATALOGUE_TORRENTS
        catalogue.parent.mkdir(parents=True, exist_ok=True)
        catalogue.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "torrents": hashes}),
                             encoding="utf-8")
        manifests = work / CATALOGUE_MANIFESTS
        manifests.mkdir(parents=True, exist_ok=True)
        for path in _local_files("manifests"):
            shutil.copy2(path, manifests / path.name)
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

        index = read_copy_index()
        index[row["id"]] = {"name": name, "created_at": row.get("createdTime"), "torrents": hashes, "size": data_size}
        _write_copy_index(index)

        # Older copies go only after this one is confirmed.
        copies = [item for item in await drive.children(folders[DATABASE])
                  if (item.get("appProperties") or {}).get("kind") == "database"]
        keep = copies_to_keep(copies, **retention_settings(state))
        trashed = 0
        for old in copies:
            if old["id"] not in keep:
                await drive.trash(old["id"])
                index.pop(old["id"], None)
                trashed += 1
        _write_copy_index(index)
        return {"file": name, "size": data_size, "kept": len(keep), "trashed": trashed}
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
            from app.services.torrent_archive import drive_retired_hashes
            async with db_session.async_session_factory() as db:
                retired = await drive_retired_hashes(db)
            # A torrent a kept database copy lists stays, so that copy can
            # still be restored with every torrent it knew.
            retired -= await protected_hashes(drive, folders)
            mirrored = await mirror_archive(drive, read_backup_settings(), folders, rebuilt=rebuilt, retired=retired)
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
            logger.warning("remote backup job failed", extra={"job_id": str(job_id)}, exc_info=True)
            message = exc.message if isinstance(exc, AppError) else str(exc)
            update_backup_settings({"last_error": message})
            await _finish(job_id, status=JobStatus.FAILED, error={"code": "REMOTE_BACKUP_FAILED", "message": message})
        finally:
            if drive:
                await drive.close()
