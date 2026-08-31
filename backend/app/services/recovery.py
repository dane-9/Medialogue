"""Recovery-bundle export services.

A recovery bundle is intentionally more than a logical application export. It
contains a consistent SQLite online backup, archived torrent evidence, a
human-readable configuration export, and a human-readable library inventory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
import zipfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from enum import Enum
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.core.security import sign_confirmation_payload, verify_confirmation_payload
from app.db import session as db_session
from app.models.domain import (
    CustomFormat,
    DownloadClient,
    Episode,
    EpisodeMediaMap,
    Indexer,
    Job,
    JobStatus,
    MediaDirectory,
    MediaFile,
    MediaProfileOverride,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    MovieTag,
    PlexConfiguration,
    PlexObservation,
    Problem,
    ProfileCustomFormatScore,
    QualityProfile,
    RemotePathMapping,
    Schedule,
    Season,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    StorageRoot,
    Tag,
    TMDBConfiguration,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
    ParseEvidence,
)
from app.services.events import create_event
from app.services.integration_state import ConfiguredDownloadClient, list_configured_download_clients
from app.services.jobs import update_job
from app.services.torrent_archive import (
    MANIFEST_SCHEMA_VERSION,
    QBittorrentClientFactory,
    prepare_torrent_restore,
    read_manifest,
    recovery_file_priorities,
    restore_torrent_archive,
)


RECOVERY_SCHEMA_VERSION = 2
_LIBRARY_INVENTORY_SCHEMA_VERSION = 1
_CONFIGURATION_EXPORT_SCHEMA_VERSION = 1
DatabaseBackupRunner = Callable[[Path, Settings], Awaitable[dict[str, Any]]]
RECOVERY_REIMPORT_CONFIRMATION_TTL_SECONDS = 15 * 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return str(value)


def _model_dict(row: Any) -> dict[str, Any]:
    return {column.name: _json_value(getattr(row, column.name)) for column in row.__table__.columns}


async def _model_rows(db: AsyncSession, model: type) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(model))).all()
    return [_model_dict(row) for row in rows]


def application_version() -> str:
    try:
        return importlib_metadata.version("medialogue-backend")
    except importlib_metadata.PackageNotFoundError:
        return "0.1.0"


def recovery_export_root(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).recovery_export_dir)


def recovery_bundle_path(job_id: UUID, settings: Settings | None = None) -> Path:
    return recovery_export_root(settings) / f"medialogue-recovery-{job_id}.zip"


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def cleanup_expired_recovery_exports(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    root = recovery_export_root(settings)
    if not root.is_dir():
        return 0
    cutoff = utcnow() - timedelta(hours=max(1, settings.recovery_export_retention_hours))
    removed = 0
    for path in root.glob("medialogue-recovery-*.zip"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    for path in root.glob(".recovery-tmp-*"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def sqlite_database_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    url = make_url(settings.database_url)
    if url.drivername not in {"sqlite", "sqlite+aiosqlite"} or not url.database or url.database == ":memory:":
        raise RuntimeError("Recovery exports require a file-backed SQLite database.")
    return Path(url.database)


async def database_backup_metadata(db: AsyncSession) -> dict[str, Any]:
    bind = db.get_bind()
    backend = bind.dialect.name if bind is not None else "unknown"
    if backend != "sqlite":
        return {
            "backend": backend,
            "sqlite_version": None,
            "migration_revision": None,
        }
    sqlite_version = str(await db.scalar(text("SELECT sqlite_version()")) or "")
    has_migration_table = await db.scalar(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'")
    )
    migration_revision = (
        await db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1")) if has_migration_table else None
    )
    return {
        "backend": backend,
        "sqlite_version": sqlite_version,
        "migration_revision": str(migration_revision) if migration_revision else None,
    }


async def recovery_capabilities(db: AsyncSession, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    db_meta = await database_backup_metadata(db)
    try:
        database_path = sqlite_database_path(settings)
        database_readable = database_path.is_file() and os.access(database_path, os.R_OK)
    except RuntimeError:
        database_path = None
        database_readable = False
    archive = Path(settings.torrent_archive_dir)
    output = recovery_export_root(settings)
    try:
        _ensure_private_directory(output)
        output_writable = os.access(output, os.W_OK)
    except OSError:
        output_writable = False
    archive_readable = archive.is_dir() and os.access(archive, os.R_OK)
    compatible = bool(
        db_meta["backend"] == "sqlite"
        and database_readable
        and output_writable
    )
    reasons: list[str] = []
    if db_meta["backend"] != "sqlite":
        reasons.append("The configured database is not SQLite.")
    if not database_readable:
        reasons.append("The SQLite database file is unavailable or unreadable.")
    if not output_writable:
        reasons.append("The recovery export directory is not writable.")
    if not archive_readable:
        reasons.append("The torrent archive mount is unavailable; the database can be backed up but the bundle would be incomplete.")
    return {
        "supported": compatible and archive_readable,
        "database_backend": db_meta["backend"],
        "sqlite_version": db_meta.get("sqlite_version"),
        "database_path": str(database_path) if database_path else None,
        "database_readable": database_readable,
        "migration_revision": db_meta.get("migration_revision"),
        "torrent_archive_readable": archive_readable,
        "export_directory_writable": output_writable,
        "export_directory": str(output),
        "retention_hours": settings.recovery_export_retention_hours,
        "reasons": reasons,
    }


def _manifest_media_type(manifest: dict[str, Any]) -> MediaType | None:
    raw = str(manifest.get("media_type") or "").strip().lower()
    if raw in {"movie", "movies"}:
        return MediaType.MOVIES
    if raw in {"show", "shows", "tv"}:
        return MediaType.SHOWS
    associations = manifest.get("media_associations")
    if isinstance(associations, list):
        for association in associations:
            if not isinstance(association, dict):
                continue
            raw = str(association.get("media_type") or "").strip().lower()
            if raw in {"movie", "movies"}:
                return MediaType.MOVIES
            if raw in {"show", "shows", "tv"}:
                return MediaType.SHOWS
    return None


def _manifest_observations(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    observations = manifest.get("download_client_observations")
    if not isinstance(observations, list):
        return []
    return [item for item in observations if isinstance(item, dict)]


def _manifest_recovery_properties(manifest: dict[str, Any]) -> dict[str, Any]:
    recovery = manifest.get("recovery") if isinstance(manifest.get("recovery"), dict) else {}
    properties = recovery.get("properties")
    return properties if isinstance(properties, dict) else {}


def _restore_save_path(manifest: dict[str, Any], observation: dict[str, Any] | None) -> str | None:
    if observation:
        value = str(observation.get("reported_save_path") or "").strip()
        if value:
            return value
    value = str(_manifest_recovery_properties(manifest).get("save_path") or "").strip()
    return value or None


def _restore_category(observation: dict[str, Any] | None, client: ConfiguredDownloadClient) -> str | None:
    if observation is not None and "category" in observation:
        value = observation.get("category")
        return str(value) if value is not None else None
    return client.category


def _restore_tags(observation: dict[str, Any] | None, client: ConfiguredDownloadClient) -> list[str]:
    if observation is not None and isinstance(observation.get("tags"), list):
        return [str(value) for value in observation["tags"]]
    return [str(value) for value in client.tags or []]


def _client_for_manifest(
    manifest: dict[str, Any],
    clients: list[ConfiguredDownloadClient],
    media_type: MediaType | None,
) -> tuple[ConfiguredDownloadClient | None, dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    by_id = {str(client.id): client for client in clients if client.enabled}
    observations = _manifest_observations(manifest)

    # Prefer the newest observation whose exact configured client still
    # exists.  Recovery Bundles preserve integration IDs, so this is the
    # normal disaster-recovery path after restoring the database/config.
    for observation in reversed(observations):
        client = by_id.get(str(observation.get("download_client_id") or ""))
        if client is None:
            continue
        if media_type is not None and client.scope != media_type:
            continue
        return client, observation, warnings

    # A rebuilt installation can legitimately recreate the same qBittorrent
    # client with a new Medialogue UUID.  A unique name+scope match is safe and
    # explainable; anything more permissive would be a guess.
    for observation in reversed(observations):
        name = str(observation.get("download_client_name") or "").strip().casefold()
        if not name:
            continue
        matches = [
            client
            for client in clients
            if client.enabled
            and client.name.strip().casefold() == name
            and (media_type is None or client.scope == media_type)
        ]
        if len(matches) == 1:
            warnings.append("Original qBittorrent client ID changed; matched the restored client by name and media scope.")
            return matches[0], observation, warnings

    return None, (observations[-1] if observations else None), warnings


async def _live_qbit_hashes(
    clients: list[ConfiguredDownloadClient],
    *,
    client_factory: QBittorrentClientFactory,
) -> tuple[dict[UUID, set[str]], dict[UUID, str]]:
    hashes: dict[UUID, set[str]] = {}
    errors: dict[UUID, str] = {}

    async def inspect(client: ConfiguredDownloadClient) -> None:
        adapter = client_factory(client.url, client.username or "", client.password or "")
        try:
            rows = await adapter.list_torrents()
            hashes[client.id] = {str(row.info_hash).strip().lower() for row in rows if row.info_hash}
        except Exception as exc:
            errors[client.id] = str(exc)
        finally:
            await adapter.close()

    await asyncio.gather(*(inspect(client) for client in clients if client.enabled))
    return hashes, errors


def _reimport_item_digest(items: list[dict[str, Any]]) -> str:
    stable = [
        {
            key: item.get(key)
            for key in (
                "torrent_id",
                "info_hash",
                "status",
                "download_client_id",
                "save_path",
                "resolved_save_path",
                "category",
                "tags",
                "file_priorities",
                "reasons",
                "warnings",
            )
        }
        for item in items
    ]
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def build_recovery_reimport_plan(
    db: AsyncSession,
    *,
    client_factory: QBittorrentClientFactory,
) -> dict[str, Any]:
    """Enumerate every archived torrent that a restored install can recover.

    This is intentionally a live preflight.  It validates qBittorrent client
    availability, confirms whether each torrent is already present, and runs
    the same destination-root validation used by single-torrent restores.
    Nothing is submitted from this function.
    """

    clients = await list_configured_download_clients(db)
    live_hashes, client_errors = await _live_qbit_hashes(clients, client_factory=client_factory)
    torrents = list((await db.scalars(select(Torrent).order_by(Torrent.name.asc(), Torrent.id.asc()))).all())
    items: list[dict[str, Any]] = []

    for torrent in torrents:
        reasons: list[str] = []
        warnings: list[str] = []
        manifest = read_manifest(torrent)
        media_type = _manifest_media_type(manifest)
        client, observation, match_warnings = _client_for_manifest(manifest, clients, media_type)
        warnings.extend(match_warnings)
        save_path = _restore_save_path(manifest, observation)
        category: str | None = None
        tags: list[str] = []
        resolved_save_path: str | None = None

        priorities = recovery_file_priorities(manifest)
        recovery = manifest.get("recovery") if isinstance(manifest.get("recovery"), dict) else {}
        recovery_files = recovery.get("files") if isinstance(recovery.get("files"), list) else []
        total_files = len([item for item in recovery_files if isinstance(item, dict)])
        selected_files = sum(1 for priority in priorities.values() if priority > 0)
        skipped_files = sum(1 for priority in priorities.values() if priority == 0)
        if total_files == 0:
            warnings.append("No per-file selection snapshot is stored; qBittorrent will use its default file selection.")

        if torrent.archive_state != TorrentArchiveState.ARCHIVED or not torrent.archive_path:
            reasons.append("Torrent archive is incomplete.")
        elif not Path(torrent.archive_path).is_file():
            reasons.append("Archived .torrent file is missing from the archive mount.")

        if media_type is None:
            reasons.append("Media scope could not be determined from the recovery manifest.")
        if client is None:
            reasons.append("The original qBittorrent client is not configured and no unique name/scope match exists.")
        elif client.id in client_errors:
            reasons.append(f"qBittorrent client could not be inspected: {client_errors[client.id]}")
        if not save_path:
            reasons.append("The original qBittorrent save path was not preserved.")

        if client is not None:
            category = _restore_category(observation, client)
            tags = _restore_tags(observation, client)

        if not reasons and client is not None and save_path:
            try:
                prepared = await prepare_torrent_restore(db, torrent.id, client.id, save_path)
                resolved_save_path = str(prepared["resolved_save_path"])
            except AppError as exc:
                reasons.append(exc.message)

        already_present = bool(
            client is not None
            and client.id not in client_errors
            and torrent.info_hash.strip().lower() in live_hashes.get(client.id, set())
        )
        status = "blocked" if reasons else "already_present" if already_present else "ready"
        items.append(
            {
                "torrent_id": str(torrent.id),
                "info_hash": torrent.info_hash,
                "torrent_name": torrent.name,
                "media_type": media_type.value if media_type else None,
                "media_title": manifest.get("media_title"),
                "release_name": manifest.get("release_name"),
                "status": status,
                "reasons": reasons,
                "warnings": warnings,
                "download_client_id": str(client.id) if client else None,
                "download_client_name": client.name if client else None,
                "save_path": save_path,
                "resolved_save_path": resolved_save_path,
                "category": category,
                "tags": tags,
                "file_selection_captured": total_files > 0,
                "total_file_count": total_files,
                "selected_file_count": selected_files,
                "skipped_file_count": skipped_files,
                # Kept in the signed core so a later manifest change invalidates
                # confirmation, but omitted by the API schema from normal UI
                # rendering if the client does not need the raw map.
                "file_priorities": {str(key): value for key, value in sorted(priorities.items())},
            }
        )

    digest = _reimport_item_digest(items)
    expiry = int(time.time()) + RECOVERY_REIMPORT_CONFIRMATION_TTL_SECONDS
    token = sign_confirmation_payload(
        {
            "purpose": "recovery_bulk_reimport",
            "plan_digest": digest,
            "exp": expiry,
        },
        get_settings().secret_key,
    )
    return {
        "generated_at": utcnow().isoformat(),
        "expires_at": datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat(),
        "confirmation_token": token,
        "total": len(items),
        "ready": sum(1 for item in items if item["status"] == "ready"),
        "already_present": sum(1 for item in items if item["status"] == "already_present"),
        "blocked": sum(1 for item in items if item["status"] == "blocked"),
        "items": items,
        "_digest": digest,
    }


async def confirm_recovery_reimport_plan(
    db: AsyncSession,
    confirmation_token: str,
    torrent_ids: list[UUID],
    *,
    client_factory: QBittorrentClientFactory,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = verify_confirmation_payload(confirmation_token, get_settings().secret_key)
    if payload is None or payload.get("purpose") != "recovery_bulk_reimport":
        raise AppError("INVALID_CONFIRMATION", "Recovery re-import confirmation is invalid.", status_code=409)
    try:
        expiry = int(payload["exp"])
        expected_digest = str(payload["plan_digest"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("INVALID_CONFIRMATION", "Recovery re-import confirmation is malformed.", status_code=409) from exc
    if expiry < int(time.time()):
        raise AppError("CONFIRMATION_EXPIRED", "Recovery re-import preview expired. Inspect the recovery plan again.", status_code=409)
    if not torrent_ids:
        raise AppError("RECOVERY_REIMPORT_EMPTY", "Select at least one recoverable torrent.", status_code=422)
    if len(set(torrent_ids)) != len(torrent_ids):
        raise AppError("RECOVERY_REIMPORT_DUPLICATE", "The recovery selection contains duplicate torrents.", status_code=422)

    plan = await build_recovery_reimport_plan(db, client_factory=client_factory)
    if plan["_digest"] != expected_digest:
        raise AppError(
            "RECOVERY_REIMPORT_PLAN_STALE",
            "Recovery state changed since the preview. Inspect the recovery plan again before submitting anything.",
            status_code=409,
        )
    by_id = {UUID(item["torrent_id"]): item for item in plan["items"]}
    missing = [str(value) for value in torrent_ids if value not in by_id]
    if missing:
        raise AppError("RECOVERY_REIMPORT_UNKNOWN_TORRENT", "The recovery selection contains an unknown torrent.", status_code=422)
    selected = [by_id[value] for value in torrent_ids]
    unavailable = [item for item in selected if item["status"] != "ready"]
    if unavailable:
        raise AppError(
            "RECOVERY_REIMPORT_NOT_READY",
            "One or more selected torrents are no longer ready for recovery.",
            status_code=409,
            details={"torrent_ids": [item["torrent_id"] for item in unavailable]},
        )
    return plan, selected


async def run_recovery_reimport(
    job_id: UUID,
    items: list[dict[str, Any]],
    *,
    client_factory: QBittorrentClientFactory,
) -> None:
    """Submit an explicitly confirmed recovery selection to qBittorrent."""

    results: list[dict[str, Any]] = []
    total = len(items)
    try:
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is None or job.status == JobStatus.CANCELLED:
                return
            await update_job(
                db,
                job,
                status=JobStatus.RUNNING,
                progress={"current": 0, "total": total, "percent": 0, "stage": "submitting", "detail": "Starting bulk recovery submission."},
            )
            await db.commit()

        for index, item in enumerate(items, start=1):
            torrent_id = UUID(item["torrent_id"])
            client_id = UUID(item["download_client_id"])
            result: dict[str, Any] = {
                "torrent_id": item["torrent_id"],
                "info_hash": item["info_hash"],
                "torrent_name": item["torrent_name"],
            }
            try:
                async with db_session.async_session_factory() as db:
                    # Re-run local validation immediately before each submit.
                    await prepare_torrent_restore(db, torrent_id, client_id, str(item["save_path"]))
                    client = next(
                        (configured for configured in await list_configured_download_clients(db) if configured.id == client_id),
                        None,
                    )
                    if client is None or not client.enabled:
                        raise AppError("DOWNLOAD_CLIENT_NOT_FOUND", "The selected qBittorrent client is unavailable or disabled.", status_code=404)

                    adapter = client_factory(client.url, client.username or "", client.password or "")
                    try:
                        existing = await adapter.get_torrent(str(item["info_hash"]))
                    finally:
                        await adapter.close()
                    if existing is not None:
                        result.update({"status": "skipped", "message": "Torrent is already present in qBittorrent."})
                    else:
                        restored = await restore_torrent_archive(
                            db,
                            torrent_id,
                            client_id,
                            str(item["save_path"]),
                            category=item.get("category"),
                            tags=list(item.get("tags") or []),
                            client_factory=client_factory,
                        )
                        await db.commit()
                        result.update({"status": "submitted", "message": "Submitted to qBittorrent.", "resolved_save_path": restored["resolved_save_path"]})
            except Exception as exc:
                if isinstance(exc, AppError):
                    message = exc.message
                    code = exc.code
                else:
                    message = str(exc)
                    code = "RECOVERY_REIMPORT_ITEM_FAILED"
                result.update({"status": "failed", "message": message, "error_code": code})
            results.append(result)

            async with db_session.async_session_factory() as db:
                job = await db.get(Job, job_id)
                if job is None:
                    return
                percent = int(index / max(1, total) * 100)
                await update_job(
                    db,
                    job,
                    progress={
                        "current": index,
                        "total": total,
                        "percent": percent,
                        "stage": "submitting",
                        "detail": f"Processed {index} of {total} recovery torrents.",
                    },
                    summary={**(job.summary or {}), "results": results},
                )
                await db.commit()

        submitted = sum(1 for item in results if item["status"] == "submitted")
        skipped = sum(1 for item in results if item["status"] == "skipped")
        failed = sum(1 for item in results if item["status"] == "failed")
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is None:
                return
            summary = {
                **(job.summary or {}),
                "message": f"Recovery re-import processed {total} torrent(s): {submitted} submitted, {skipped} already present, {failed} failed.",
                "selected": total,
                "submitted": submitted,
                "skipped": skipped,
                "failed": failed,
                "results": results,
            }
            await update_job(
                db,
                job,
                status=JobStatus.COMPLETED if failed < total else JobStatus.FAILED,
                progress={"current": total, "total": total, "percent": 100, "stage": "completed" if failed < total else "failed"},
                summary=summary,
                error=({"code": "RECOVERY_REIMPORT_FAILED", "message": "Every selected torrent failed to submit."} if failed == total else None),
            )
            await create_event(
                db,
                "recovery.reimport_completed" if failed < total else "recovery.reimport_failed",
                entity_type="job",
                entity_id=job.id,
                message=summary["message"],
                details={"selected": total, "submitted": submitted, "skipped": skipped, "failed": failed},
            )
            await db.commit()
    except Exception as exc:
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is not None and job.status not in {JobStatus.CANCELLED, JobStatus.INTERRUPTED}:
                await update_job(
                    db,
                    job,
                    status=JobStatus.FAILED,
                    error={"code": "RECOVERY_REIMPORT_FAILED", "message": str(exc)},
                    summary={**(job.summary or {}), "results": results},
                )
                await create_event(
                    db,
                    "recovery.reimport_failed",
                    entity_type="job",
                    entity_id=job.id,
                    message="Recovery bulk re-import failed.",
                    details={"error": str(exc)},
                )
                await db.commit()


async def run_sqlite_backup(destination: Path, settings: Settings) -> dict[str, Any]:
    """Produce a transactionally consistent backup through SQLite's online backup API."""

    source = sqlite_database_path(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)

    def backup() -> None:
        with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db)
            result = destination_db.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise RuntimeError("The SQLite backup failed its integrity check.")

    await asyncio.to_thread(backup)
    return {"tool": "sqlite3_backup", "format": "sqlite", "sqlite_version": sqlite3.sqlite_version}


async def build_configuration_export(db: AsyncSession, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "schema_version": _CONFIGURATION_EXPORT_SCHEMA_VERSION,
        "generated_at": utcnow().isoformat(),
        "contains_sensitive_values": True,
        "sensitivity_note": (
            "Integration credentials are included so this export can assist disaster recovery. "
            "The Recovery Bundle must be stored as sensitive material."
        ),
        "runtime": {
            "app_name": settings.app_name,
            "environment": settings.environment,
            "config_dir": settings.config_dir,
            "torrent_archive_dir": settings.torrent_archive_dir,
            "recovery_export_dir": settings.recovery_export_dir,
            "session_ttl_hours": settings.session_ttl_hours,
            "cookie_secure": settings.cookie_secure,
            "cookie_samesite": settings.cookie_samesite,
            "api_prefix": settings.api_prefix,
            "log_level": settings.log_level,
        },
        "runtime_sensitive": {
            "database_url": settings.database_url,
            "secret_key": settings.secret_key,
            "note": "These values are intentionally included for disaster recovery and must be protected.",
        },
        "storage_roots": await _model_rows(db, StorageRoot),
        "remote_path_mappings": await _model_rows(db, RemotePathMapping),
        "integration_configuration": get_integration_config_store().export_for_recovery(),
        "integration_runtime_state": {
            "download_clients": await _model_rows(db, DownloadClient),
            "indexers": await _model_rows(db, Indexer),
            "plex": await _model_rows(db, PlexConfiguration),
            "tmdb": await _model_rows(db, TMDBConfiguration),
        },
        "schedules": await _model_rows(db, Schedule),
        "custom_formats": await _model_rows(db, CustomFormat),
        "quality_profiles": await _model_rows(db, QualityProfile),
        "profile_custom_format_scores": await _model_rows(db, ProfileCustomFormatScore),
        "media_profile_overrides": await _model_rows(db, MediaProfileOverride),
        "tags": await _model_rows(db, Tag),
    }


async def build_library_inventory(db: AsyncSession) -> dict[str, Any]:
    sections: dict[str, list[dict[str, Any]]] = {
        "movies": await _model_rows(db, Movie),
        "movie_releases": await _model_rows(db, MovieRelease),
        "movie_tags": await _model_rows(db, MovieTag),
        "shows": await _model_rows(db, Show),
        "seasons": await _model_rows(db, Season),
        "episodes": await _model_rows(db, Episode),
        "show_releases": await _model_rows(db, ShowRelease),
        "media_directories": await _model_rows(db, MediaDirectory),
        "media_files": await _model_rows(db, MediaFile),
        "episode_media_mappings": await _model_rows(db, EpisodeMediaMap),
        "torrents": await _model_rows(db, Torrent),
        "torrent_client_observations": await _model_rows(db, TorrentClientObservation),
        "movie_release_torrents": await _model_rows(db, MovieReleaseTorrent),
        "show_release_torrents": await _model_rows(db, ShowReleaseTorrent),
        "plex_observations": await _model_rows(db, PlexObservation),
        "parse_evidence": await _model_rows(db, ParseEvidence),
        "open_and_historical_problems": await _model_rows(db, Problem),
    }
    return {
        "schema_version": _LIBRARY_INVENTORY_SCHEMA_VERSION,
        "generated_at": utcnow().isoformat(),
        "counts": {name: len(rows) for name, rows in sections.items()},
        **sections,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe_archive_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        try:
            mode = path.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISLNK(mode):
            continue
        if stat.S_ISREG(mode):
            files.append(path)
    return sorted(files)


def _torrent_arcname(relative: Path) -> str:
    parts = relative.parts
    if parts and parts[0] == "manifests":
        return str(Path("manifests", *parts[1:]))
    if parts and parts[0] == "torrents":
        return str(Path("torrent-archive", *parts))
    return str(Path("torrent-archive", "misc", relative))


def _zip_tree(bundle: zipfile.ZipFile, root: Path, prefix: str) -> int:
    count = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        bundle.write(path, str(Path(prefix) / path.relative_to(root)))
        count += 1
    return count


def _archive_inventory(root: Path, files: list[Path]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": utcnow().isoformat(),
        "archive_root": str(root),
        "files": [
            {
                "source_relative_path": str(path.relative_to(root)),
                "bundle_path": _torrent_arcname(path.relative_to(root)),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in files
        ],
    }


def _build_bundle_zip(temp_zip: Path, temp_root: Path, torrent_root: Path, archive_files: list[Path]) -> None:
    with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        bundle.write(temp_root / "backup-metadata.json", "backup-metadata.json")
        _zip_tree(bundle, temp_root / "database", "database")
        _zip_tree(bundle, temp_root / "config", "config")
        _zip_tree(bundle, temp_root / "inventory", "inventory")
        for path in archive_files:
            bundle.write(path, _torrent_arcname(path.relative_to(torrent_root)))


async def _job_progress(job_id: UUID, percent: int, stage: str, detail: str | None = None) -> None:
    async with db_session.async_session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None or job.status in {JobStatus.CANCELLED, JobStatus.INTERRUPTED}:
            return
        await update_job(
            db,
            job,
            progress={"percent": max(0, min(100, int(percent))), "stage": stage, "detail": detail},
        )
        await db.commit()


async def run_recovery_export(
    job_id: UUID,
    *,
    database_backup_runner: DatabaseBackupRunner = run_sqlite_backup,
    settings: Settings | None = None,
    database_metadata_override: dict[str, Any] | None = None,
) -> None:
    """Build a complete Recovery Bundle for a persisted Job."""

    settings = settings or get_settings()
    output_root = recovery_export_root(settings)
    _ensure_private_directory(output_root)
    cleanup_expired_recovery_exports(settings)
    final_path = recovery_bundle_path(job_id, settings)
    final_path.unlink(missing_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".recovery-tmp-{job_id}-", dir=output_root))
    try:
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is None or job.status == JobStatus.CANCELLED:
                return
            await update_job(db, job, status=JobStatus.RUNNING, progress={"percent": 2, "stage": "preparing"})
            await db.commit()

        async with db_session.async_session_factory() as db:
            db_meta = database_metadata_override or await database_backup_metadata(db)
            if db_meta.get("backend") != "sqlite" and database_metadata_override is None:
                raise RuntimeError("Recovery backups require SQLite.")
            configuration = await build_configuration_export(db, settings)
            inventory = await build_library_inventory(db)

        await _job_progress(job_id, 8, "database_backup", "Creating consistent SQLite online backup")
        database_file = temp_root / "database" / "medialogue.db"
        database_backup_result = await database_backup_runner(database_file, settings)
        await _job_progress(job_id, 58, "inventory", "Writing configuration and library inventory")

        _write_json(temp_root / "config" / "application-config-export.json", configuration)
        config_store = get_integration_config_store()
        for source in (
            config_store.config_path,
            config_store.secrets_path,
            Path(settings.config_dir) / "setup-state.json",
            Path(settings.config_dir) / "custom-format-layout.json",
        ):
            if source.is_file():
                destination = temp_root / "config" / "live" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        _write_json(temp_root / "inventory" / "library-inventory.json", inventory)

        torrent_root = Path(settings.torrent_archive_dir)
        archive_files = _safe_archive_files(torrent_root)
        archive_inventory = await asyncio.to_thread(_archive_inventory, torrent_root, archive_files)
        _write_json(temp_root / "inventory" / "torrent-archive-inventory.json", archive_inventory)

        backup_timestamp = utcnow()
        expires_at = backup_timestamp + timedelta(hours=max(1, settings.recovery_export_retention_hours))
        metadata = {
            "recovery_schema_version": RECOVERY_SCHEMA_VERSION,
            "backup_timestamp": backup_timestamp.isoformat(),
            "application_version": application_version(),
            "application_package": "medialogue-backend",
            "sqlite": {
                "version": db_meta.get("sqlite_version"),
                "database_file": "database/medialogue.db",
                "backup": _json_value(database_backup_result),
            },
            "schema_migration_revision": db_meta.get("migration_revision"),
            "torrent_manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "configuration_export_schema_version": _CONFIGURATION_EXPORT_SCHEMA_VERSION,
            "library_inventory_schema_version": _LIBRARY_INVENTORY_SCHEMA_VERSION,
            "sensitive": True,
            "sensitive_note": (
                "This bundle contains a complete database backup and integration credentials. "
                "Treat it like a password/database backup."
            ),
            "download_expires_at": expires_at.isoformat(),
        }
        _write_json(temp_root / "backup-metadata.json", metadata)

        await _job_progress(job_id, 72, "packaging", "Packaging database and torrent recovery evidence")
        temp_zip = output_root / f".{final_path.name}.tmp"
        temp_zip.unlink(missing_ok=True)
        await asyncio.to_thread(_build_bundle_zip, temp_zip, temp_root, torrent_root, archive_files)
        os.replace(temp_zip, final_path)
        try:
            final_path.chmod(0o600)
        except OSError:
            pass
        bundle_sha256 = await asyncio.to_thread(_sha256, final_path)
        bundle_size = final_path.stat().st_size

        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is None:
                return
            summary = dict(job.summary or {})
            summary.update(
                {
                    "message": "Recovery Bundle is ready for download.",
                    "download_ready": True,
                    "download_url": f"/api/v1/recovery/exports/{job_id}/download",
                    "bundle_filename": final_path.name,
                    "bundle_path": str(final_path),
                    "bundle_size": bundle_size,
                    "bundle_sha256": bundle_sha256,
                    "expires_at": expires_at.isoformat(),
                    "sqlite_version": db_meta.get("sqlite_version"),
                    "migration_revision": db_meta.get("migration_revision"),
                    "torrent_archive_files": len(archive_files),
                }
            )
            await update_job(db, job, status=JobStatus.COMPLETED, progress={"percent": 100, "stage": "completed"}, summary=summary)
            await create_event(
                db,
                "recovery.export_completed",
                entity_type="job",
                entity_id=job.id,
                message="Recovery Bundle export completed.",
                details={
                    "bundle_filename": final_path.name,
                    "bundle_size": bundle_size,
                    "bundle_sha256": bundle_sha256,
                    "expires_at": expires_at.isoformat(),
                    "torrent_archive_files": len(archive_files),
                },
            )
            await db.commit()
    except Exception as exc:
        final_path.unlink(missing_ok=True)
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is not None and job.status not in {JobStatus.CANCELLED, JobStatus.INTERRUPTED}:
                await update_job(
                    db,
                    job,
                    status=JobStatus.FAILED,
                    error={"code": "RECOVERY_EXPORT_FAILED", "message": str(exc)},
                )
                await create_event(
                    db,
                    "recovery.export_failed",
                    entity_type="job",
                    entity_id=job.id,
                    message="Recovery Bundle export failed.",
                    details={"error": str(exc)},
                )
                await db.commit()
    finally:
        (output_root / f".{final_path.name}.tmp").unlink(missing_ok=True)
        shutil.rmtree(temp_root, ignore_errors=True)
