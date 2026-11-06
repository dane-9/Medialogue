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
from datetime import datetime, timedelta, timezone
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
    ReleaseState,
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
QBittorrentClientFactory = Callable[[str, str, str], QBittorrentClient]


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


def verify_torrent_archive(torrent: Torrent) -> tuple[bool, str | None]:
    """Cryptographically bind valid torrent bytes to the current manifest schema."""

    if torrent.archive_state != TorrentArchiveState.ARCHIVED:
        return False, "database archive state is not archived"
    default_torrent, default_manifest = archive_paths(torrent.info_hash)
    torrent_path = Path(torrent.archive_path) if torrent.archive_path else default_torrent
    manifest_path = Path(torrent.manifest_path) if torrent.manifest_path else default_manifest
    try:
        payload = torrent_path.read_bytes()
        if not _valid_torrent_payload(payload):
            return False, "torrent export is missing or invalid"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return False, "manifest root is invalid"
        if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            return False, "manifest schema is unsupported"
        if str(manifest.get("torrent_info_hash") or "").casefold() != torrent.info_hash.casefold():
            return False, "manifest info hash does not match the torrent"
        archive = manifest.get("archive")
        if not isinstance(archive, dict):
            return False, "manifest archive section is missing"
        expected_sha256 = archive.get("torrent_sha256")
        if not isinstance(expected_sha256, str) or expected_sha256 != hashlib.sha256(payload).hexdigest():
            return False, "torrent export checksum does not match the manifest"
        recovery = manifest.get("recovery")
        if not isinstance(recovery, dict):
            return False, "manifest recovery section is missing"
        if not isinstance(recovery.get("properties"), dict):
            return False, "recovery properties are invalid"
        recovery_file_priorities(manifest)
        if not isinstance(recovery.get("trackers"), list):
            return False, "recovery tracker manifest is invalid"
        return True, None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return False, str(exc)


def torrent_archive_complete(torrent: Torrent) -> bool:
    """Return true only when database state, bytes, checksum and manifest agree."""

    return verify_torrent_archive(torrent)[0]


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
    recovery_snapshot: dict[str, Any] | None = None,
    download_client_id: UUID | None = None,
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
    existing_recovery = existing.get("recovery") if isinstance(existing.get("recovery"), dict) else {}
    supplied_recovery = recovery_snapshot if isinstance(recovery_snapshot, dict) else {}
    def recovered_value(key: str, fallback: Any) -> Any:
        if key in supplied_recovery:
            return supplied_recovery[key]
        return existing_recovery.get(key, fallback)

    recovery = {
        "captured_at": _first_nonempty(
            supplied_recovery.get("captured_at"),
            _iso(utcnow()) if recovery_snapshot is not None else None,
            existing_recovery.get("captured_at"),
        ),
        "properties": recovered_value("properties", {}),
        "files": recovered_value("files", []),
        "trackers": recovered_value("trackers", []),
        "settings": {
            key: metadata.get(key)
            for key in (
                "auto_tmm", "download_limit", "upload_limit", "force_start",
                "first_last_piece_priority", "sequential_download", "super_seeding",
                "priority", "ratio_limit", "seeding_time_limit",
                "inactive_seeding_time_limit", "content_path", "scope",
            )
            if metadata.get(key) is not None
        },
    }

    client_recovery = dict(existing.get("client_recovery") or {})
    if download_client_id is not None and recovery_snapshot is not None:
        client_recovery[str(download_client_id)] = recovery

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
        "media_associations": associations,
        "download_client_observations": observations,
        "recovery": recovery,
        "client_recovery": client_recovery,
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
    recovery_snapshot: dict[str, Any] | None = None,
    download_client_id: UUID | None = None,
) -> Path:
    archive_path, manifest_path = archive_paths(torrent.info_hash)
    manifest = await build_recovery_manifest(
        db,
        torrent,
        archive_path=archive_path,
        manifest_path=manifest_path,
        torrent_sha256=torrent_sha256,
        archive_error=archive_error,
        recovery_snapshot=recovery_snapshot,
        download_client_id=download_client_id,
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
    client_factory: QBittorrentClientFactory = QBittorrentClient,
    recovery_snapshot: dict[str, Any] | None = None,
    download_client_id: UUID | None = None,
) -> bool:
    """Archive .torrent bytes and refresh its recovery manifest.

    Failure is durable but non-destructive: qBittorrent/media state is left
    unchanged and future polls retry the archive. A manifest is still attempted
    so identity/path evidence can survive even when qBittorrent cannot export
    the torrent yet (for example while magnet metadata is incomplete).
    """

    download_client_id = download_client.id
    archive_path, manifest_path = archive_paths(torrent.info_hash)
    previous_state = torrent.archive_state
    previous_error = str((torrent.metadata_json or {}).get("archive_error") or "")
    torrent_bytes: bytes | None = None
    torrent_sha256: str | None = None
    error: str | None = None

    adapter: QBittorrentClient | None = None
    try:
        if archive_path.is_file() and archive_path.stat().st_size > 0:
            candidate = archive_path.read_bytes()
            prior_manifest = _load_existing_manifest(manifest_path)
            expected = (prior_manifest.get("archive") or {}).get("torrent_sha256")
            if _valid_torrent_payload(candidate) and expected == hashlib.sha256(candidate).hexdigest():
                torrent_bytes = candidate
        if torrent_bytes is None:
            adapter = client_factory(download_client.url, download_client.username or "", download_client.password or "")
            torrent_bytes = await adapter.export_torrent(torrent.info_hash)
            if not _valid_torrent_payload(torrent_bytes):
                raise RuntimeError("qBittorrent returned an empty or invalid torrent export.")
            _atomic_write(archive_path, torrent_bytes)
        torrent_sha256 = hashlib.sha256(torrent_bytes).hexdigest()
        torrent.archive_path = str(archive_path)
    except Exception as exc:
        error = str(exc)

    if recovery_snapshot is None:
        try:
            if adapter is None:
                adapter = client_factory(download_client.url, download_client.username or "", download_client.password or "")
            recovery_snapshot = await adapter.get_torrent_recovery_snapshot(torrent.info_hash)
        except Exception as exc:
            snapshot_error = f"recovery snapshot: {exc}"
            error = f"{error}; {snapshot_error}" if error else snapshot_error
    if adapter is not None:
        await adapter.close()

    try:
        await write_recovery_manifest(
            db,
            torrent,
            torrent_sha256=torrent_sha256,
            archive_error=error,
            recovery_snapshot=recovery_snapshot,
            download_client_id=download_client_id,
        )
    except Exception as exc:
        manifest_error = str(exc)
        error = f"{error}; manifest: {manifest_error}" if error else f"manifest: {manifest_error}"

    torrent.archive_state = TorrentArchiveState.ARCHIVED if error is None else TorrentArchiveState.FAILED
    success, verification_error = verify_torrent_archive(torrent)
    if not success and error is None:
        error = f"archive verification: {verification_error}"
        torrent.archive_state = TorrentArchiveState.FAILED
    torrent.metadata_json = {
        **{key: value for key, value in (torrent.metadata_json or {}).items() if key != "archive_pruned_at"},
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


async def refresh_torrent_manifest(
    db: AsyncSession,
    torrent: Torrent,
    *,
    recovery_snapshot: dict[str, Any] | None = None,
    download_client_id: UUID | None = None,
) -> bool:
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
            recovery_snapshot=recovery_snapshot,
            download_client_id=download_client_id,
        )
        return True
    except Exception:
        return False


def read_manifest(torrent: Torrent) -> dict[str, Any]:
    path = Path(torrent.manifest_path) if torrent.manifest_path else archive_paths(torrent.info_hash)[1]
    return _load_existing_manifest(path)


def recovery_revision(manifest: dict[str, Any]) -> str:
    """Fingerprint recovery inputs, excluding volatile progress and observation times."""
    # A manifest written before any client snapshot was captured has only the
    # top-level one.
    snapshots = manifest.get("client_recovery") or {"default": manifest.get("recovery") or {}}
    stable = {}
    for client_id, recovery in snapshots.items():
        properties = recovery.get("properties") or {}
        stable[client_id] = {
            "settings": recovery.get("settings"),
            "save_path": properties.get("save_path"),
            "files": [{key: item.get(key) for key in ("index", "name", "size", "priority")}
                      for item in recovery.get("files", [])],
        }
    context = [{key: row.get(key) for key in ("download_client_id", "category", "tags", "reported_save_path")}
               for row in manifest.get("download_client_observations", [])]
    payload = {"clients": stable, "context": context,
               "torrent_sha256": (manifest.get("archive") or {}).get("torrent_sha256")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def manifest_for_client(manifest: dict[str, Any], client_id: UUID | str) -> dict[str, Any]:
    """Select client-specific evidence; never borrow another client's selection.

    A manifest with no client snapshots uses its top-level one. With several,
    an exact client match or a unique name/scope observation match is required.
    """
    snapshots = manifest.get("client_recovery") or {}
    if not snapshots:
        return manifest
    recovery = snapshots.get(str(client_id))
    if recovery is None:
        client = get_integration_config_store().get_download_client(UUID(str(client_id)))
        matches = [row for row in manifest.get("download_client_observations", [])
                   if client and row.get("download_client_name") == client.name
                   and row.get("scope") == client.scope
                   and str(row.get("download_client_id")) in snapshots]
        if len(matches) == 1:
            recovery = snapshots[str(matches[0]["download_client_id"])]
        elif len(snapshots) == 1:
            # Explicit single-torrent restore to another client has one unambiguous source.
            recovery = next(iter(snapshots.values()))
        else:
            raise ValueError("No unambiguous recovery settings exist for the selected client")
    return {**manifest, "recovery": recovery}


def recovery_file_priorities(manifest: dict[str, Any]) -> dict[int, int]:
    """Return the complete qBittorrent file-selection state from a current manifest."""

    recovery = manifest.get("recovery")
    if not isinstance(recovery, dict):
        raise ValueError("manifest recovery section is missing")
    files = recovery.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("recovery file selection is missing")
    priorities: dict[int, int] = {}
    for position, item in enumerate(files):
        if not isinstance(item, dict):
            raise ValueError(f"recovery file entry {position} is invalid")
        try:
            file_id = int(item["index"])
            priority = int(item["priority"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"recovery file entry {position} is missing a valid index or priority") from None
        if file_id < 0 or priority not in {0, 1, 6, 7}:
            raise ValueError(f"recovery file entry {position} has an unsupported index or priority")
        if file_id in priorities:
            raise ValueError(f"recovery file index {file_id} is duplicated")
        priorities[file_id] = priority
    return priorities


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
    archive_valid, archive_error = verify_torrent_archive(torrent)
    if not archive_valid:
        raise AppError(
            "TORRENT_ARCHIVE_INVALID",
            f"This torrent does not have a valid recovery archive: {archive_error}.",
            status_code=409,
        )
    assert torrent.archive_path is not None
    archive_path = Path(torrent.archive_path)

    from app.services.integration_state import get_configured_download_client

    client = await get_configured_download_client(db, download_client_id)
    if client is None or not client.enabled:
        raise AppError("DOWNLOAD_CLIENT_NOT_FOUND", "The selected qBittorrent client is unavailable or disabled.", status_code=404)

    manifest = read_manifest(torrent)
    try:
        recovery_file_priorities(manifest_for_client(manifest, client.id))
    except ValueError as exc:
        raise AppError("TORRENT_RECOVERY_AMBIGUOUS", str(exc), status_code=409) from exc
    associations = manifest.get("media_associations")
    primary = associations[0] if isinstance(associations, list) and associations and isinstance(associations[0], dict) else {}
    raw_media_type = str(primary.get("media_type") or "").lower()
    if raw_media_type == "movies":
        media_type = MediaType.MOVIES
    elif raw_media_type == "shows":
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
            "The restore destination must resolve inside an enabled media folder for this qBittorrent client scope.",
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
    client_factory: QBittorrentClientFactory,
) -> dict[str, Any]:
    prepared = await prepare_torrent_restore(db, torrent_id, download_client_id, save_path)
    torrent = prepared["torrent"]
    client = prepared["client"]
    archive_path = prepared["archive_path"]
    resolved_save_path = prepared["resolved_save_path"]
    manifest = manifest_for_client(read_manifest(torrent), client.id)
    observations = manifest.get("download_client_observations")
    observation_rows = [item for item in observations if isinstance(item, dict)] if isinstance(observations, list) else []
    original = next(
        (
            item
            for item in reversed(observation_rows)
            if str(item.get("download_client_id") or "") == str(client.id)
        ),
        observation_rows[0] if observation_rows else {},
    )
    recovery = manifest.get("recovery") if isinstance(manifest.get("recovery"), dict) else {}
    settings = recovery.get("settings") if isinstance(recovery.get("settings"), dict) else {}
    restored_category = category
    if restored_category is None:
        restored_category = original.get("category") if "category" in original else client.category
    restored_tags = tags
    if restored_tags is None:
        original_tags = original.get("tags")
        restored_tags = original_tags if isinstance(original_tags, list) else list(client.tags or [])
    adapter = client_factory(client.url, client.username or "", client.password or "")
    added = False
    try:
        existing = await adapter.get_torrent(torrent.info_hash)
        if existing is not None:
            raise AppError(
                "TORRENT_ALREADY_PRESENT",
                "This torrent is already present in the selected qBittorrent client.",
                status_code=409,
            )
        await adapter.add_torrent(
            archive_path.read_bytes(),
            filename=f"{torrent.info_hash}.torrent",
            save_path=save_path,
            category=restored_category,
            tags=tuple(str(item) for item in restored_tags),
            auto_tmm=settings.get("auto_tmm") if isinstance(settings.get("auto_tmm"), bool) else None,
            sequential_order=settings.get("sequential_download") is True,
            first_last_first=settings.get("first_last_piece_priority") is True,
            stopped=True,
        )
        added = True
        priorities = recovery_file_priorities(manifest)
        try:
            await adapter.set_file_priorities(torrent.info_hash, priorities)
            await adapter.restore_torrent_settings(torrent.info_hash, settings)
            if not str(original.get("state") or "").lower().startswith(("paused", "stopped")):
                await adapter.start_torrents([torrent.info_hash])
        except Exception:
            # Do not leave a partially restored torrent running with the wrong
            # file selection. Removing the qBittorrent entry without deleting
            # data is the least-destructive rollback; a later recovery attempt
            # can safely retry after the underlying issue is corrected.
            if added:
                try:
                    await adapter.remove_torrent(torrent.info_hash, delete_files=False)
                except Exception:
                    pass
            raise
    except AppError:
        raise
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
    client_factory: QBittorrentClientFactory,
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

    from app.services.recovery_checkpoint import archive_checkpoint
    async with archive_checkpoint:
        await run_job(
            job_id,
            worker,
            failure_code="TORRENT_ARCHIVE_FAILED",
            failure_message="Recovery archive retry failed.",
        )


async def rebuild_catalogue_from_manifests(db: AsyncSession) -> dict[str, Any]:
    """Add an archive row for every manifest on disk that has none.

    Only the archive is read; no client is contacted. This is how a fresh
    install learns what a restored (or Drive-fetched) archive holds. A row that
    exists but lost its archive files is pointed back at them once they verify.
    """

    root = Path(get_settings().torrent_archive_dir).resolve()
    imported = skipped = repaired = 0
    errors: list[dict[str, str]] = []
    for path in sorted((root / "manifests").glob("*/*.json")):
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Manifest is outside the archive directory")
            manifest = json.loads(path.read_text(encoding="utf-8"))
            info_hash = str(manifest["torrent_info_hash"]).lower()
            torrent_path, expected_manifest = archive_paths(info_hash)
            if expected_manifest.resolve() != path.resolve() or not torrent_path.resolve().is_relative_to(root):
                raise ValueError("Manifest path does not match its torrent hash")
            existing = await db.scalar(select(Torrent).where(Torrent.info_hash == info_hash))
            if existing is not None:
                if existing.archive_state == TorrentArchiveState.ARCHIVED and verify_torrent_archive(existing)[0]:
                    skipped += 1
                    continue
                candidate = Torrent(info_hash=info_hash, name=existing.name, archive_path=str(torrent_path), manifest_path=str(path))
                if not verify_torrent_archive(candidate)[0]:
                    skipped += 1
                    continue
                existing.archive_state = TorrentArchiveState.ARCHIVED
                existing.archive_path = str(torrent_path)
                existing.manifest_path = str(path)
                repaired += 1
                continue
            torrent = Torrent(info_hash=info_hash, name=str(manifest.get("torrent_name") or info_hash),
                              archive_state=TorrentArchiveState.ARCHIVED, archive_path=str(torrent_path),
                              manifest_path=str(path), manifest_schema_version=manifest.get("schema_version"),
                              metadata_json={"rebuilt_from_archive": True}, tracker_summary={"state": "unknown"})
            valid, error = verify_torrent_archive(torrent)
            if not valid:
                raise ValueError(error)
            async with db.begin_nested():
                db.add(torrent)
                await db.flush()
            imported += 1
        except (ValueError, KeyError, TypeError, OSError) as exc:
            errors.append({"manifest": path.name, "error": str(exc)})
    return {"imported": imported, "skipped": skipped, "repaired": repaired, "errors": errors}


#: Release states that still mean "this is on the server". A release whose
#: files went missing is still yours: a dead disk must never cost its backup.
KEPT_RELEASE_STATES = (ReleaseState.CURRENT, ReleaseState.MISSING, ReleaseState.CONFLICT, ReleaseState.DUPLICATE)
#: How long Google Drive keeps a pruned torrent after it leaves the archive.
DRIVE_GRACE = timedelta(days=30)


async def prune_retired_archives(db: AsyncSession) -> dict[str, Any]:
    """Drop archived torrents that nothing on the server uses any more.

    A torrent is kept while any library release still uses it (cross-seeds
    included) or any qBittorrent client still lists it. It is pruned only on
    evidence: every client it was seen in confirmed removing it, and no
    release that is still yours uses it. A client being unreachable, or files
    going missing, never prunes anything. Its Drive copy stays for
    ``DRIVE_GRACE`` before the backup removes it.
    """

    kept: set[UUID] = set()
    for link, release, column in (
        (MovieReleaseTorrent, MovieRelease, MovieReleaseTorrent.movie_release_id),
        (ShowReleaseTorrent, ShowRelease, ShowReleaseTorrent.show_release_id),
    ):
        kept.update(await db.scalars(
            select(link.torrent_id).join(release, release.id == column)
            .where(release.release_state.in_(KEPT_RELEASE_STATES))
        ))
    observations: dict[UUID, list[TorrentClientObservation]] = {}
    for observation in await db.scalars(select(TorrentClientObservation)):
        observations.setdefault(observation.torrent_id, []).append(observation)

    pruned: list[str] = []
    candidates = await db.scalars(select(Torrent).where(
        Torrent.archive_state.in_([TorrentArchiveState.ARCHIVED, TorrentArchiveState.FAILED])
    ))
    for torrent in candidates:
        seen = observations.get(torrent.id) or []
        # Never seen in a client (e.g. rebuilt from a backup): no evidence, keep.
        if torrent.id in kept or not seen or any(item.is_present or item.removed_at is None for item in seen):
            continue
        for path in archive_paths(torrent.info_hash):
            path.unlink(missing_ok=True)
        torrent.archive_state = TorrentArchiveState.PRUNED
        torrent.archive_path = None
        torrent.manifest_path = None
        torrent.metadata_json = {**(torrent.metadata_json or {}), "archive_pruned_at": _iso(utcnow())}
        pruned.append(torrent.info_hash)
        await create_event(
            db,
            "torrent.archive_pruned",
            entity_type="torrent",
            entity_id=torrent.id,
            message=f"Removed {torrent.name} from the torrent archive: it is no longer in qBittorrent or the library.",
            details={"info_hash": torrent.info_hash, "drive_removal_after_days": DRIVE_GRACE.days},
        )
    return {"pruned": len(pruned), "info_hashes": pruned}


async def drive_retired_hashes(db: AsyncSession) -> set[str]:
    """Pruned torrents whose grace period in Google Drive has passed."""

    cutoff = utcnow() - DRIVE_GRACE
    due: set[str] = set()
    for torrent in await db.scalars(select(Torrent).where(Torrent.archive_state == TorrentArchiveState.PRUNED)):
        raw = (torrent.metadata_json or {}).get("archive_pruned_at")
        try:
            when = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if (when if when.tzinfo else when.replace(tzinfo=timezone.utc)) <= cutoff:
            due.add(torrent.info_hash.lower())
    return due
