"""Durable .torrent archive and recovery-manifest services.

The archive is deliberately independent from logical Movie/Show lifetime.
Files live under the dedicated ``/torrent-archive`` mount, never beside media.
The info hash is used as the storage key so release names cannot influence
filesystem paths and the same torrent is archived once globally.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.integrations.qbittorrent import QBittorrentClient
from app.models.domain import (
    IntegrationType,
    JobStatus,
    MediaDirectory,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    QualityDefinition,
    RemotePathMapping,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    StorageRoot,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
)
from app.core.integration_config import get_integration_config_store
from app.services.events import create_event
from app.services.integration_state import ConfiguredDownloadClient
from app.services.jobs import JobFailure, checkpoint_job, run_job


MANIFEST_SCHEMA_VERSION = 1
_HASH_RE = re.compile(r"^[0-9A-Za-z_-]{1,128}$")
QBitClientFactory = Callable[[str, str, str], QBittorrentClient]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _safe_hash(info_hash: str) -> str:
    value = info_hash.strip().lower()
    if not _HASH_RE.fullmatch(value):
        raise ValueError("Torrent info hash is not a safe archive identifier.")
    return value


def archive_paths(info_hash: str, archive_root: str | Path | None = None) -> tuple[Path, Path]:
    key = _safe_hash(info_hash)
    root = Path(archive_root or get_settings().torrent_archive_dir)
    shard = key[:2]
    torrent_path = root / "torrents" / shard / f"{key}.torrent"
    manifest_path = root / "manifests" / shard / f"{key}.json"
    return torrent_path, manifest_path


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _load_existing_manifest(path: Path) -> dict[str, Any]:
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _valid_torrent_payload(payload: bytes) -> bool:
    """Apply a cheap sanity check before trusting exported recovery bytes."""

    return len(payload) >= 8 and payload.startswith(b"d") and b"4:info" in payload


def torrent_archive_complete(torrent: Torrent) -> bool:
    """Return true only when the database state and both archive files agree."""

    if torrent.archive_state != TorrentArchiveState.ARCHIVED:
        return False
    default_torrent, default_manifest = archive_paths(torrent.info_hash)
    torrent_path = Path(torrent.archive_path) if torrent.archive_path else default_torrent
    manifest_path = Path(torrent.manifest_path) if torrent.manifest_path else default_manifest
    try:
        return (
            torrent_path.is_file()
            and torrent_path.stat().st_size > 0
            and manifest_path.is_file()
            and manifest_path.stat().st_size > 0
        )
    except OSError:
        return False


async def _movie_associations(db: AsyncSession, torrent: Torrent) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(MovieReleaseTorrent, MovieRelease, Movie, QualityDefinition)
            .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
            .join(Movie, Movie.id == MovieRelease.movie_id)
            .outerjoin(QualityDefinition, QualityDefinition.id == MovieRelease.quality_definition_id)
            .where(MovieReleaseTorrent.torrent_id == torrent.id)
            .order_by(MovieReleaseTorrent.created_at.desc())
        )
    ).all()
    result: list[dict[str, Any]] = []
    for link, release, movie, quality in rows:
        directories = (
            await db.scalars(select(MediaDirectory).where(MediaDirectory.movie_release_id == release.id))
        ).all()
        result.append(
            {
                "media_type": "movies",
                "association_type": link.association_type.value,
                "media_id": str(movie.id),
                "tmdb_id": movie.tmdb_id,
                "tvdb_id": movie.tvdb_id,
                "title": movie.title,
                "year": movie.year,
                "release_id": str(release.id),
                "release_name": release.raw_release_name,
                "release_state": release.release_state.value,
                "quality": quality.name if quality else None,
                "edition": release.effective_edition,
                "release_group": release.release_group,
                "parser_version": release.parser_version,
                "parse_snapshot": release.parse_snapshot,
                "paths": [
                    {
                        "reported_path": item.reported_path,
                        "resolved_path": item.resolved_path,
                        "exists": item.exists,
                        "first_seen_at": _iso(item.first_seen_at),
                        "last_seen_at": _iso(item.last_seen_at),
                        "missing_since": _iso(item.missing_since),
                    }
                    for item in directories
                ],
                "associated_at": _iso(link.created_at),
            }
        )
    return result


async def _show_associations(db: AsyncSession, torrent: Torrent) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(ShowReleaseTorrent, ShowRelease, Show, QualityDefinition)
            .join(ShowRelease, ShowRelease.id == ShowReleaseTorrent.show_release_id)
            .join(Show, Show.id == ShowRelease.show_id)
            .outerjoin(QualityDefinition, QualityDefinition.id == ShowRelease.quality_definition_id)
            .where(ShowReleaseTorrent.torrent_id == torrent.id)
            .order_by(ShowReleaseTorrent.created_at.desc())
        )
    ).all()
    result: list[dict[str, Any]] = []
    for link, release, show, quality in rows:
        directories = (
            await db.scalars(select(MediaDirectory).where(MediaDirectory.show_release_id == release.id))
        ).all()
        result.append(
            {
                "media_type": "shows",
                "association_type": link.association_type.value,
                "media_id": str(show.id),
                "tmdb_id": show.tmdb_id,
                "tvdb_id": show.tvdb_id,
                "title": show.title,
                "year": show.year,
                "release_id": str(release.id),
                "release_name": release.raw_release_name,
                "release_state": release.release_state.value,
                "release_scope": release.release_scope.value,
                "quality": quality.name if quality else None,
                "edition": None,
                "release_group": release.release_group,
                "parser_version": release.parse_snapshot.get("parser_version") if release.parse_snapshot else None,
                "parse_snapshot": release.parse_snapshot,
                "paths": [
                    {
                        "reported_path": item.reported_path,
                        "resolved_path": item.resolved_path,
                        "exists": item.exists,
                        "first_seen_at": _iso(item.first_seen_at),
                        "last_seen_at": _iso(item.last_seen_at),
                        "missing_since": _iso(item.missing_since),
                    }
                    for item in directories
                ],
                "associated_at": _iso(link.created_at),
            }
        )
    return result


async def _client_observations(db: AsyncSession, torrent: Torrent) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(
            select(TorrentClientObservation)
            .where(TorrentClientObservation.torrent_id == torrent.id)
            .order_by(TorrentClientObservation.first_seen_at.asc())
        )
    ).all()
    result: list[dict[str, Any]] = []
    store = get_integration_config_store()
    for observation in rows:
        client = store.get_download_client(observation.download_client_id)
        result.append(
            {
                "download_client_id": str(observation.download_client_id),
                "download_client_name": client.name if client else "Removed client",
                "scope": client.scope if client else None,
                "reported_save_path": observation.reported_save_path,
                "resolved_save_path": observation.resolved_save_path,
                "state": observation.state,
                "progress": float(observation.progress) if observation.progress is not None else None,
                "category": observation.category,
                "tags": list(observation.tags or []),
                "is_present": observation.is_present,
                "first_seen_at": _iso(observation.first_seen_at),
                "last_seen_at": _iso(observation.last_seen_at),
                "removed_at": _iso(observation.removed_at),
            }
        )
    return result


async def build_recovery_manifest(
    db: AsyncSession,
    torrent: Torrent,
    *,
    archive_path: Path | None = None,
    manifest_path: Path | None = None,
    torrent_sha256: str | None = None,
    archive_error: str | None = None,
) -> dict[str, Any]:
    """Build a manifest without erasing evidence already persisted on disk.

    This matters after a logical Movie/Show record is removed: the manifest
    must keep the identity and old path even though the database association no
    longer exists.
    """

    default_archive, default_manifest = archive_paths(torrent.info_hash)
    archive_path = archive_path or default_archive
    manifest_path = manifest_path or default_manifest
    existing = _load_existing_manifest(manifest_path)

    associations = await _movie_associations(db, torrent)
    associations.extend(await _show_associations(db, torrent))
    observations = await _client_observations(db, torrent)

    # If application records were intentionally removed, retain the last
    # complete association evidence from the existing manifest.
    if not associations:
        prior_associations = existing.get("media_associations")
        if isinstance(prior_associations, list):
            associations = prior_associations
    if not observations:
        prior_observations = existing.get("download_client_observations")
        if isinstance(prior_observations, list):
            observations = prior_observations

    primary = associations[0] if associations else {}
    original_client = observations[0] if observations else {}
    metadata = torrent.metadata_json or {}
    archive_info = existing.get("archive") if isinstance(existing.get("archive"), dict) else {}

    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": _iso(utcnow()),
        "torrent_info_hash": torrent.info_hash,
        "torrent_name": torrent.name,
        "torrent": {
            "id": str(torrent.id),
            "info_hash": torrent.info_hash,
            "name": torrent.name,
            "total_size": torrent.total_size,
            "first_seen_at": _iso(torrent.first_seen_at),
            "last_seen_at": _iso(torrent.last_seen_at),
            "completed_at": _iso(torrent.completed_at),
            "tracker_summary": torrent.tracker_summary or {},
            "metadata": metadata,
        },
        # Compatibility/convenience fields intentionally mirror the original
        # architecture example while the richer arrays preserve many-to-many
        # history.
        "media_type": _first_nonempty(primary.get("media_type"), existing.get("media_type")),
        "internal_media_id": _first_nonempty(primary.get("media_id"), existing.get("internal_media_id")),
        "tmdb_id": _first_nonempty(primary.get("tmdb_id"), existing.get("tmdb_id")),
        "tvdb_id": _first_nonempty(primary.get("tvdb_id"), existing.get("tvdb_id")),
        "media_title": _first_nonempty(primary.get("title"), existing.get("media_title")),
        "media_year": _first_nonempty(primary.get("year"), existing.get("media_year")),
        "release_name": _first_nonempty(primary.get("release_name"), existing.get("release_name")),
        "quality": _first_nonempty(primary.get("quality"), existing.get("quality")),
        "edition": _first_nonempty(primary.get("edition"), existing.get("edition")),
        "release_group": _first_nonempty(primary.get("release_group"), existing.get("release_group")),
        "previous_reported_path": _first_nonempty(
            next((path.get("reported_path") for item in associations for path in item.get("paths", []) if path.get("reported_path")), None),
            original_client.get("reported_save_path"),
            existing.get("previous_reported_path"),
        ),
        "previous_resolved_path": _first_nonempty(
            next((path.get("resolved_path") for item in associations for path in item.get("paths", []) if path.get("resolved_path")), None),
            original_client.get("resolved_save_path"),
            existing.get("previous_resolved_path"),
        ),
        "download_client_name": _first_nonempty(
            original_client.get("download_client_name"), existing.get("download_client_name")
        ),
        "media_associations": associations,
        "download_client_observations": observations,
        "archive": {
            "torrent_path": str(archive_path),
            "manifest_path": str(manifest_path),
            "torrent_sha256": _first_nonempty(torrent_sha256, archive_info.get("torrent_sha256")),
            "archived_at": _first_nonempty(archive_info.get("archived_at"), _iso(utcnow()) if archive_path.is_file() else None),
            "last_manifest_update_at": _iso(utcnow()),
            "archive_error": archive_error,
        },
        "created_at": _first_nonempty(existing.get("created_at"), _iso(torrent.first_seen_at)),
    }
    return manifest


async def write_recovery_manifest(
    db: AsyncSession,
    torrent: Torrent,
    *,
    torrent_sha256: str | None = None,
    archive_error: str | None = None,
) -> Path:
    archive_path, manifest_path = archive_paths(torrent.info_hash)
    manifest = await build_recovery_manifest(
        db,
        torrent,
        archive_path=archive_path,
        manifest_path=manifest_path,
        torrent_sha256=torrent_sha256,
        archive_error=archive_error,
    )
    payload = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    _atomic_write(manifest_path, payload)
    torrent.manifest_path = str(manifest_path)
    torrent.manifest_schema_version = MANIFEST_SCHEMA_VERSION
    return manifest_path


async def ensure_torrent_archived(
    db: AsyncSession,
    torrent: Torrent,
    download_client: ConfiguredDownloadClient,
    *,
    client_factory: QBitClientFactory = QBittorrentClient,
) -> bool:
    """Archive .torrent bytes and refresh its recovery manifest.

    Failure is durable but non-destructive: qBittorrent/media state is left
    unchanged and future polls retry the archive. A manifest is still attempted
    so identity/path evidence can survive even when qBittorrent cannot export
    the torrent yet (for example while magnet metadata is incomplete).
    """

    archive_path, manifest_path = archive_paths(torrent.info_hash)
    previous_state = torrent.archive_state
    previous_error = str((torrent.metadata_json or {}).get("archive_error") or "")
    torrent_bytes: bytes | None = None
    torrent_sha256: str | None = None
    error: str | None = None

    try:
        if archive_path.is_file() and archive_path.stat().st_size > 0:
            candidate = archive_path.read_bytes()
            if _valid_torrent_payload(candidate):
                torrent_bytes = candidate
        if torrent_bytes is None:
            adapter = client_factory(download_client.url, download_client.username or "", download_client.password or "")
            try:
                export = getattr(adapter, "export_torrent", None)
                if export is None:
                    raise RuntimeError("qBittorrent adapter does not support torrent export.")
                torrent_bytes = await export(torrent.info_hash)
            finally:
                await adapter.close()
            if not _valid_torrent_payload(torrent_bytes):
                raise RuntimeError("qBittorrent returned an empty or invalid torrent export.")
            _atomic_write(archive_path, torrent_bytes)
        torrent_sha256 = hashlib.sha256(torrent_bytes).hexdigest()
        torrent.archive_path = str(archive_path)
    except Exception as exc:  # archive failure must never interrupt reconciliation
        error = str(exc)

    try:
        await write_recovery_manifest(db, torrent, torrent_sha256=torrent_sha256, archive_error=error)
    except Exception as exc:
        manifest_error = str(exc)
        error = f"{error}; manifest: {manifest_error}" if error else f"manifest: {manifest_error}"

    success = error is None and archive_path.is_file() and manifest_path.is_file()
    torrent.archive_state = TorrentArchiveState.ARCHIVED if success else TorrentArchiveState.FAILED
    torrent.metadata_json = {
        **(torrent.metadata_json or {}),
        "archive_error": error,
        "archive_last_attempt_at": _iso(utcnow()),
        "archive_sha256": torrent_sha256,
    }

    if success and previous_state != TorrentArchiveState.ARCHIVED:
        await create_event(
            db,
            "torrent.archived",
            entity_type="torrent",
            entity_id=torrent.id,
            message=f"Torrent recovery data archived for {torrent.name}.",
            details={
                "info_hash": torrent.info_hash,
                "archive_path": str(archive_path),
                "manifest_path": str(manifest_path),
                "schema_version": MANIFEST_SCHEMA_VERSION,
            },
        )
    elif not success and error and (previous_state != TorrentArchiveState.FAILED or error != previous_error):
        await create_event(
            db,
            "torrent.archive_failed",
            entity_type="torrent",
            entity_id=torrent.id,
            message=f"Torrent recovery archive could not be completed for {torrent.name}.",
            details={"info_hash": torrent.info_hash, "error": error},
        )
    return success


async def refresh_torrent_manifest(db: AsyncSession, torrent: Torrent) -> bool:
    """Refresh historical identity/path evidence without re-exporting bytes."""

    try:
        archive_path, _ = archive_paths(torrent.info_hash)
        sha256 = None
        if archive_path.is_file():
            sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        await write_recovery_manifest(
            db,
            torrent,
            torrent_sha256=sha256,
            archive_error=(torrent.metadata_json or {}).get("archive_error"),
        )
        return True
    except Exception:
        return False


def read_manifest(torrent: Torrent) -> dict[str, Any]:
    path = Path(torrent.manifest_path) if torrent.manifest_path else archive_paths(torrent.info_hash)[1]
    return _load_existing_manifest(path)


def archive_mount_health(archive_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(archive_root or get_settings().torrent_archive_dir)
    if not root.exists():
        return {"status": "unavailable", "path": str(root), "writable": False, "message": "Archive mount does not exist."}
    if not root.is_dir():
        return {"status": "unavailable", "path": str(root), "writable": False, "message": "Archive path is not a directory."}
    writable = os.access(root, os.W_OK | os.X_OK)
    return {
        "status": "healthy" if writable else "unavailable",
        "path": str(root),
        "writable": writable,
        "message": None if writable else "Archive mount is not writable.",
    }


async def select_archive_client(db: AsyncSession, torrent: Torrent) -> ConfiguredDownloadClient | None:
    """Pick the most recently observed live configured client for a manual archive retry."""

    observations = (
        await db.scalars(
            select(TorrentClientObservation)
            .where(
                TorrentClientObservation.torrent_id == torrent.id,
                TorrentClientObservation.is_present.is_(True),
            )
            .order_by(TorrentClientObservation.last_seen_at.desc())
        )
    ).all()
    from app.services.integration_state import get_configured_download_client

    for observation in observations:
        client = await get_configured_download_client(db, observation.download_client_id)
        if client is not None and client.enabled:
            return client
    return None


def _inside_restore_path(path: str, root: str) -> bool:
    try:
        Path(path).resolve(strict=False).relative_to(Path(root).resolve(strict=False))
        return True
    except ValueError:
        return False


async def prepare_torrent_restore(
    db: AsyncSession,
    torrent_id: UUID,
    download_client_id: UUID,
    save_path: str,
) -> dict[str, Any]:
    """Validate a restore request without contacting qBittorrent."""

    torrent = await db.get(Torrent, torrent_id)
    if torrent is None:
        raise AppError("NOT_FOUND", "Torrent archive record was not found.", status_code=404)
    if torrent.archive_state != TorrentArchiveState.ARCHIVED or not torrent.archive_path:
        raise AppError("TORRENT_NOT_ARCHIVED", "This torrent does not have a complete recovery archive.", status_code=409)
    archive_path = Path(torrent.archive_path)
    if not archive_path.is_file():
        raise AppError("TORRENT_ARCHIVE_FILE_MISSING", "The archived .torrent file is missing from the archive mount.", status_code=409)

    from app.services.integration_state import get_configured_download_client

    client = await get_configured_download_client(db, download_client_id)
    if client is None or not client.enabled:
        raise AppError("DOWNLOAD_CLIENT_NOT_FOUND", "The selected qBittorrent client is unavailable or disabled.", status_code=404)

    manifest = read_manifest(torrent)
    raw_media_type = str(manifest.get("media_type") or "").lower()
    if raw_media_type in {"movie", "movies"}:
        media_type = MediaType.MOVIES
    elif raw_media_type in {"show", "shows", "tv"}:
        media_type = MediaType.SHOWS
    else:
        media_type = client.scope
    if client.scope != media_type:
        raise AppError(
            "DOWNLOAD_CLIENT_SCOPE_MISMATCH",
            f"This archive belongs to {media_type.value}, but the selected client is scoped to {client.scope.value}.",
            status_code=409,
        )

    mappings = (
        await db.scalars(
            select(RemotePathMapping).where(
                RemotePathMapping.enabled.is_(True),
                RemotePathMapping.integration_type == IntegrationType.QBITTORRENT,
                (RemotePathMapping.integration_id == client.id) | RemotePathMapping.integration_id.is_(None),
            )
        )
    ).all()
    # Imported lazily because the polling service imports this module for
    # automatic archiving; keeping this dependency local avoids an import cycle.
    from app.services.qbittorrent import resolve_remote_path

    resolved_save_path, _ = resolve_remote_path(save_path, mappings)
    roots = (
        await db.scalars(
            select(StorageRoot).where(StorageRoot.enabled.is_(True), StorageRoot.media_type == client.scope)
        )
    ).all()
    if not resolved_save_path or not any(_inside_restore_path(resolved_save_path, root.resolved_root_path) for root in roots):
        raise AppError(
            "RESTORE_PATH_OUTSIDE_CONFIGURED_ROOTS",
            "The restore destination must resolve inside an enabled storage root for this qBittorrent client scope.",
            status_code=422,
        )

    return {
        "torrent": torrent,
        "client": client,
        "archive_path": archive_path,
        "resolved_save_path": resolved_save_path,
    }


async def restore_torrent_archive(
    db: AsyncSession,
    torrent_id: UUID,
    download_client_id: UUID,
    save_path: str,
    *,
    category: str | None,
    tags: list[str] | None,
    client_factory: QBitClientFactory,
) -> dict[str, Any]:
    prepared = await prepare_torrent_restore(db, torrent_id, download_client_id, save_path)
    torrent = prepared["torrent"]
    client = prepared["client"]
    archive_path = prepared["archive_path"]
    resolved_save_path = prepared["resolved_save_path"]
    adapter = client_factory(client.url, client.username or "", client.password or "")
    try:
        await adapter.add_torrent(
            archive_path.read_bytes(),
            filename=f"{torrent.info_hash}.torrent",
            save_path=save_path,
            category=category if category is not None else client.category,
            tags=tuple(tags if tags is not None else (client.tags or [])),
        )
    except Exception as exc:
        raise AppError("QBITTORRENT_RESTORE_FAILED", f"qBittorrent rejected the archived torrent: {exc}", status_code=503) from exc
    finally:
        await adapter.close()

    await create_event(
        db,
        "torrent.restored",
        entity_type="torrent",
        entity_id=torrent.id,
        message=f"Archived torrent submitted to qBittorrent client {client.name}.",
        details={
            "download_client_id": str(client.id),
            "download_client_name": client.name,
            "reported_save_path": save_path,
            "resolved_save_path": resolved_save_path,
            "info_hash": torrent.info_hash,
        },
    )
    return {
        "torrent_id": str(torrent.id),
        "download_client_id": str(client.id),
        "client_name": client.name,
        "info_hash": torrent.info_hash,
        "save_path": save_path,
        "resolved_save_path": resolved_save_path,
        "status": "submitted",
    }


async def run_torrent_archive_retry(
    job_id: UUID,
    torrent_id: UUID,
    *,
    client_factory: QBitClientFactory,
) -> None:
    """Retry a torrent archive export as a durable Job."""

    async def worker(db, job) -> None:
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"current": 0, "total": 1, "percent": 0, "stage": "archiving", "detail": "Retrying torrent recovery archive…"},
        )
        try:
            torrent = await db.get(Torrent, torrent_id)
            if torrent is None:
                raise AppError("NOT_FOUND", "Torrent archive record was not found.", status_code=404)
            client = await select_archive_client(db, torrent)
            if client is None:
                raise AppError(
                    "TORRENT_NOT_AVAILABLE_IN_QBITTORRENT",
                    "No enabled qBittorrent client currently has this torrent, so its .torrent file cannot be re-exported.",
                    status_code=409,
                )
            success = await ensure_torrent_archived(db, torrent, client, client_factory=client_factory)
            summary = {
                "torrent_id": str(torrent.id),
                "archive_state": torrent.archive_state.value,
                "archive_path": torrent.archive_path,
                "manifest_path": torrent.manifest_path,
                "message": None if success else str((torrent.metadata_json or {}).get("archive_error") or "Archive failed."),
            }
            await checkpoint_job(
                db,
                job,
                status=JobStatus.COMPLETED if success else JobStatus.FAILED,
                progress={"current": 1, "total": 1, "percent": 100, "stage": "completed" if success else "failed", "detail": "Recovery archive completed." if success else summary["message"]},
                summary=summary,
                error=None if success else {"code": "TORRENT_ARCHIVE_FAILED", "message": summary["message"]},
            )
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            raise JobFailure(
                exc.code,
                exc.message,
                details=exc.details,
                progress={"current": 0, "total": 1, "percent": 0, "stage": "failed", "detail": exc.message},
            ) from exc
        except Exception as exc:
            raise JobFailure(
                "TORRENT_ARCHIVE_FAILED",
                str(exc),
                progress={"current": 0, "total": 1, "percent": 0, "stage": "failed", "detail": "Recovery archive retry failed."},
            ) from exc

    await run_job(
        job_id,
        worker,
        failure_code="TORRENT_ARCHIVE_FAILED",
        failure_message="Recovery archive retry failed.",
    )


async def run_torrent_archive_restore(
    job_id: UUID,
    torrent_id: UUID,
    download_client_id: UUID,
    save_path: str,
    *,
    category: str | None,
    tags: list[str] | None,
    client_factory: QBitClientFactory,
) -> None:
    """Submit an archived torrent to qBittorrent as a durable Job."""

    async def worker(db, job) -> None:
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"current": 0, "total": 1, "percent": 0, "stage": "restoring", "detail": "Submitting archived torrent to qBittorrent…"},
        )
        try:
            result = await restore_torrent_archive(
                db,
                torrent_id,
                download_client_id,
                save_path,
                category=category,
                tags=tags,
                client_factory=client_factory,
            )
            await checkpoint_job(db, job, status=JobStatus.COMPLETED, progress={"current": 1, "total": 1, "percent": 100, "stage": "completed", "detail": "Archived torrent submitted to qBittorrent."}, summary=result)
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            raise JobFailure(
                exc.code,
                exc.message,
                details=exc.details,
                progress={"current": 0, "total": 1, "percent": 0, "stage": "failed", "detail": exc.message},
            ) from exc
        except Exception as exc:
            raise JobFailure(
                "QBITTORRENT_RESTORE_FAILED",
                str(exc),
                progress={"current": 0, "total": 1, "percent": 0, "stage": "failed", "detail": "Torrent restore failed."},
            ) from exc

    await run_job(
        job_id,
        worker,
        failure_code="QBITTORRENT_RESTORE_FAILED",
        failure_message="Torrent restore failed.",
    )
