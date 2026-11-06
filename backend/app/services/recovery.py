"""Re-importing archived torrents into qBittorrent after a restore.

The plan reads each archived manifest, picks the client and original save
path, and checks qBittorrent and the media folders before anything is sent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import consume_confirmation_jti, sign_confirmation_payload, verify_confirmation_payload
from app.db import session as db_session
from app.models.domain import (
    Job,
    JobStatus,
    MediaType,
    Torrent,
    TorrentArchiveState,
)
from app.services.events import create_event
from app.services.integration_state import ConfiguredDownloadClient, list_configured_download_clients
from app.services.jobs import update_job
from app.services.torrent_archive import (
    QBittorrentClientFactory,
    manifest_for_client,
    prepare_torrent_restore,
    read_manifest,
    recovery_file_priorities,
    restore_torrent_archive,
)


RECOVERY_REIMPORT_CONFIRMATION_TTL_SECONDS = 15 * 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sqlite_database_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    url = make_url(settings.database_url)
    if url.drivername not in {"sqlite", "sqlite+aiosqlite"} or not url.database or url.database == ":memory:":
        raise RuntimeError("Recovery exports require a file-backed SQLite database.")
    return Path(url.database)


def _manifest_media_type(manifest: dict[str, Any]) -> MediaType | None:
    associations = manifest.get("media_associations")
    if isinstance(associations, list):
        for association in associations:
            if not isinstance(association, dict):
                continue
            raw = str(association.get("media_type") or "").strip().lower()
            if raw == "movies":
                return MediaType.MOVIES
            if raw == "shows":
                return MediaType.SHOWS
    return None


def _manifest_primary_association(manifest: dict[str, Any]) -> dict[str, Any]:
    associations = manifest.get("media_associations")
    if isinstance(associations, list) and associations and isinstance(associations[0], dict):
        return associations[0]
    return {}


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

    # A fresh install with one client for this kind of media can only mean
    # that one; the original category, tags and path still come from the
    # manifest.
    if media_type is not None:
        scoped = [client for client in clients if client.enabled and client.scope == media_type]
        if len(scoped) == 1:
            warnings.append("Original qBittorrent client not found; using the only client configured for this media type.")
            return scoped[0], (observations[-1] if observations else None), warnings

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
    torrents = list((await db.scalars(
        select(Torrent).where(Torrent.archive_state != TorrentArchiveState.PRUNED).order_by(Torrent.name.asc(), Torrent.id.asc())
    )).all())
    items: list[dict[str, Any]] = []

    for torrent in torrents:
        reasons: list[str] = []
        warnings: list[str] = []
        manifest = read_manifest(torrent)
        media_type = _manifest_media_type(manifest)
        client, observation, match_warnings = _client_for_manifest(manifest, clients, media_type)
        warnings.extend(match_warnings)
        if client is not None:
            try:
                manifest = manifest_for_client(manifest, client.id)
            except ValueError as exc:
                reasons.append(str(exc))
        save_path = _restore_save_path(manifest, observation)
        category: str | None = None
        tags: list[str] = []
        resolved_save_path: str | None = None

        try:
            priorities = recovery_file_priorities(manifest)
        except ValueError as exc:
            priorities = {}
            reasons.append(f"Recovery manifest is invalid: {exc}.")
        recovery = manifest.get("recovery") if isinstance(manifest.get("recovery"), dict) else {}
        recovery_files = recovery.get("files") if isinstance(recovery.get("files"), list) else []
        total_files = len([item for item in recovery_files if isinstance(item, dict)])
        selected_files = sum(1 for priority in priorities.values() if priority > 0)
        skipped_files = sum(1 for priority in priorities.values() if priority == 0)

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
        primary = _manifest_primary_association(manifest)
        items.append(
            {
                "torrent_id": str(torrent.id),
                "info_hash": torrent.info_hash,
                "torrent_name": torrent.name,
                "media_type": media_type.value if media_type else None,
                "media_title": primary.get("title"),
                "release_name": primary.get("release_name"),
                "status": status,
                "reasons": reasons,
                "warnings": warnings,
                "download_client_id": str(client.id) if client else None,
                "download_client_name": client.name if client else None,
                "save_path": save_path,
                "resolved_save_path": resolved_save_path,
                "category": category,
                "tags": tags,
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
    await consume_confirmation_jti(db, payload)
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


