"""Retiring what an upgrade replaced: the cleanup preview, its guarded commit, and automatic cleanups."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.services.media_history import history_snapshot
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import consume_confirmation_jti, sign_confirmation_payload, verify_confirmation_payload
from app.integrations.filesystem import VIDEO_SUFFIXES
from app.models.domain import (
    AccessMode,
    Episode,
    EpisodeMediaMap,
    MediaDirectory,
    MediaFile,
    MediaType,
    MovieRelease,
    ReleaseState,
    ShowRelease,
    StorageRoot,
    UpgradeCandidate,
)
from app.services.events import create_event, publish_live_event
from app.services.upgrade_payloads import _ids, _inside, _old_live_torrents, _path_key, utcnow


CONFIRMATION_TTL_SECONDS = 10 * 60


async def _block_automatic_cleanup(db: AsyncSession, row: UpgradeCandidate, blocked: list[str]) -> None:
    row.execution_state = "handoff_attention"
    row.execution_error = (
        "Replacement validated and old torrents were retired, but automatic old-media cleanup was blocked: "
        + " ".join(blocked)
    )
    row.comparison_snapshot = {
        key: value for key, value in (row.comparison_snapshot or {}).items() if key != "automatic_cleanup_pending"
    }
    await create_event(
        db,
        "upgrade.cleanup_blocked",
        entity_type="upgrade_candidate",
        entity_id=row.id,
        message="Automatic removal of superseded media was blocked by a safety check.",
        details={"candidate_id": str(row.id), "blocked": list(blocked)},
    )


async def run_automatic_cleanups(db: AsyncSession) -> int:
    """Delete pre-authorized superseded media for committed, validated handoffs.

    Each candidate runs in its own transaction: database state is flushed
    first, files are deleted, then the transaction commits immediately.
    """

    rows = (
        await db.scalars(select(UpgradeCandidate).where(UpgradeCandidate.execution_state == "cleanup_required"))
    ).all()
    pending_ids = [row.id for row in rows if (row.comparison_snapshot or {}).get("automatic_cleanup_pending")]
    completed = 0
    for candidate_id in pending_ids:
        try:
            row = await db.scalar(select(UpgradeCandidate).where(UpgradeCandidate.id == candidate_id).with_for_update())
            if row is None or row.execution_state != "cleanup_required":
                continue
            core = await _cleanup_core(db, row)
            if core["blocked"]:
                await _block_automatic_cleanup(db, row, core["blocked"])
            else:
                row.comparison_snapshot = {
                    key: value
                    for key, value in (row.comparison_snapshot or {}).items()
                    if key != "automatic_cleanup_pending"
                }
                await _perform_cleanup(db, row, core, automatic=True)
                completed += 1
            await db.commit()
        except Exception:
            await db.rollback()
            import logging

            logging.getLogger(__name__).exception(
                "Automatic upgrade cleanup failed", extra={"candidate_id": str(candidate_id)}
            )
    return completed


def _file_stat(path: Path) -> dict[str, Any]:
    stat = path.lstat()
    return {"path": str(path), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "symlink": path.is_symlink()}


def _directory_inventory(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(path.rglob("*"), key=lambda value: str(value).casefold()):
        try:
            if item.is_dir() and not item.is_symlink():
                continue
            rows.append(_file_stat(item))
        except FileNotFoundError:
            continue
    return rows


async def _replacement_cleanup_guard(db: AsyncSession, row: UpgradeCandidate) -> tuple[list[str], list[dict[str, Any]]]:
    """Reconfirm replacement media is still physically present before old-media deletion.

    The signed cleanup preview includes this evidence in its digest. If the
    replacement disappears or changes between preview and commit, the preview
    becomes stale and the destructive step is refused.
    """

    incoming_ids = _ids(row.incoming_release_ids)
    if not incoming_ids:
        return ["Replacement media association is unavailable."], []

    blocked: list[str] = []
    evidence: list[dict[str, Any]] = []
    if row.media_type == MediaType.MOVIES:
        directories = list(
            (
                await db.scalars(
                    select(MediaDirectory)
                    .options(selectinload(MediaDirectory.files))
                    .where(
                        MediaDirectory.movie_release_id.in_(incoming_ids),
                        MediaDirectory.exists.is_(True),
                    )
                )
            ).all()
        )
        if not directories:
            return ["Replacement Movie media is no longer present."], []
        for directory in directories:
            tracked = [media_file for media_file in directory.files if media_file.exists]
            if not tracked:
                blocked.append(f"{directory.resolved_path}: replacement has no validated media files")
                continue
            for media_file in tracked:
                path = Path(directory.resolved_path) / media_file.relative_path
                try:
                    if not path.exists() or not path.is_file():
                        blocked.append(f"{path}: replacement media is missing")
                        continue
                    evidence.append(_file_stat(path))
                except OSError:
                    blocked.append(f"{path}: replacement media could not be verified")
    else:
        if row.season_id is None:
            return ["Replacement season is no longer available."], []
        expected = set(
            await db.scalars(
                select(Episode.id).where(Episode.season_id == row.season_id, Episode.monitored.is_(True))
            )
        )
        rows = (
            await db.execute(
                select(EpisodeMediaMap.episode_id, MediaFile, MediaDirectory)
                .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
                .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
                .where(
                    EpisodeMediaMap.show_release_id.in_(incoming_ids),
                    MediaFile.exists.is_(True),
                )
            )
        ).all()
        verified_episodes: set[UUID] = set()
        seen_paths: set[str] = set()
        for episode_id, media_file, directory in rows:
            path = Path(directory.resolved_path) / media_file.relative_path
            try:
                if not path.exists() or not path.is_file():
                    continue
                verified_episodes.add(episode_id)
                key = str(path)
                if key not in seen_paths:
                    evidence.append(_file_stat(path))
                    seen_paths.add(key)
            except OSError:
                continue
        missing = expected - verified_episodes
        if missing:
            blocked.append(
                f"Replacement season media is missing for {len(missing)} monitored episode(s)."
            )

    if not evidence and not blocked:
        blocked.append("Replacement media could not be verified on disk.")
    return blocked, evidence


async def _cleanup_core(db: AsyncSession, row: UpgradeCandidate) -> dict[str, Any]:
    old_ids = _ids(row.current_release_ids)
    blocked, replacement_guard = await _replacement_cleanup_guard(db, row)
    if row.target_key.startswith("replace:") and await _old_live_torrents(db, row, exclude_torrent_id=row.torrent_id):
        blocked.append("Retire the old torrent entries before deleting their media files.")
    targets: list[dict[str, Any]] = []
    media_file_ids: set[UUID] = set()
    # Replacement media must never fall inside a deletion target, whether it
    # shares the old folder, is nested below it, or matches a sidecar prefix.
    protected = [Path(str(item["path"])) for item in replacement_guard if item.get("path")]
    protected_keys = {_path_key(path) for path in protected}

    if row.media_type == MediaType.MOVIES:
        directories = list(
            (
                await db.scalars(
                    select(MediaDirectory).where(MediaDirectory.movie_release_id.in_(old_ids), MediaDirectory.exists.is_(True))
                )
            ).all()
        )
        for directory in directories:
            root = await db.get(StorageRoot, directory.storage_root_id) if directory.storage_root_id else None
            path = Path(directory.resolved_path)
            if root is None or not root.enabled or root.access_mode != AccessMode.READ_WRITE:
                blocked.append(f"{path}: media folder is not read/write")
                continue
            root_path = Path(root.resolved_root_path)
            if not _inside(path, root_path) or path.resolve(strict=False) == root_path.resolve(strict=False) or path.is_symlink():
                blocked.append(f"{path}: unsafe deletion target")
                continue
            shared = await db.scalar(
                select(MediaDirectory.id)
                .where(
                    MediaDirectory.resolved_path == directory.resolved_path,
                    MediaDirectory.exists.is_(True),
                    MediaDirectory.movie_release_id.is_not(None),
                    MediaDirectory.movie_release_id.not_in(old_ids),
                )
                .limit(1)
            )
            if shared is not None:
                blocked.append(f"{path}: directory is shared with another release")
                continue
            if any(_inside(item, path) for item in protected):
                blocked.append(f"{path}: directory contains the replacement media")
                continue
            inventory = _directory_inventory(path) if path.exists() else []
            targets.append({"path": str(path), "kind": "directory", "inventory": inventory})
    else:
        rows = (
            await db.execute(
                select(MediaFile, MediaDirectory)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
                .where(EpisodeMediaMap.show_release_id.in_(old_ids), MediaFile.exists.is_(True))
            )
        ).all()
        # Media files of any other release in the same folders are never
        # deletable as "sidecars" of an outgoing file.
        old_id_set = set(old_ids)
        directory_ids = {directory.id for _media_file, directory in rows}
        tracked_keys: set[str] = set()
        if directory_ids:
            neighbours = (
                await db.execute(
                    select(MediaFile, MediaDirectory)
                    .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
                    .where(MediaFile.media_directory_id.in_(directory_ids), MediaFile.exists.is_(True))
                )
            ).all()
            for neighbour, neighbour_directory in neighbours:
                owners = {
                    value
                    for value in await db.scalars(
                        select(EpisodeMediaMap.show_release_id).where(EpisodeMediaMap.media_file_id == neighbour.id)
                    )
                    if value is not None
                }
                if not owners or not owners.issubset(old_id_set):
                    tracked_keys.add(_path_key(Path(neighbour_directory.resolved_path) / neighbour.relative_path))
        seen_files: set[UUID] = set()
        file_targets: dict[str, dict[str, Any]] = {}
        for media_file, directory in rows:
            if media_file.id in seen_files:
                continue
            seen_files.add(media_file.id)
            mapping_release_ids = set(
                value
                for value in await db.scalars(select(EpisodeMediaMap.show_release_id).where(EpisodeMediaMap.media_file_id == media_file.id))
                if value is not None
            )
            if not mapping_release_ids or not mapping_release_ids.issubset(set(old_ids)):
                blocked.append(f"{media_file.filename}: file is shared with another release")
                continue
            root = await db.get(StorageRoot, directory.storage_root_id) if directory.storage_root_id else None
            path = Path(directory.resolved_path) / media_file.relative_path
            if root is None or not root.enabled or root.access_mode != AccessMode.READ_WRITE:
                blocked.append(f"{path}: media folder is not read/write")
                continue
            root_path = Path(root.resolved_root_path)
            if not _inside(path, root_path) or path.resolve(strict=False) == root_path.resolve(strict=False):
                blocked.append(f"{path}: unsafe deletion target")
                continue
            if _path_key(path) in protected_keys:
                blocked.append(f"{path}: file is the replacement media")
                continue
            media_file_ids.add(media_file.id)
            parent = path.parent
            prefix = f"{path.stem}."
            siblings = []
            if parent.exists():
                for sibling in parent.iterdir():
                    if not (sibling.is_file() or sibling.is_symlink()):
                        continue
                    if sibling.name == path.name:
                        siblings.append(sibling)
                        continue
                    # Only true sidecars (subtitles, nfo, artwork) share the
                    # stem. Another video file with a longer name, such as a
                    # REPACK of the same episode, is separate media.
                    if (
                        sibling.name.startswith(prefix)
                        and sibling.suffix.casefold() not in VIDEO_SUFFIXES
                        and _path_key(sibling) not in protected_keys
                        and _path_key(sibling) not in tracked_keys
                    ):
                        siblings.append(sibling)
            if not siblings and path.exists():
                siblings = [path]
            for sibling in siblings:
                try:
                    file_targets[str(sibling)] = {"path": str(sibling), "kind": "file", "inventory": [_file_stat(sibling)]}
                except FileNotFoundError:
                    continue
        targets.extend(file_targets.values())

    total_bytes = sum(int(item.get("size") or 0) for target in targets for item in target.get("inventory") or [])
    return {
        "candidate_id": str(row.id),
        "media_type": row.media_type.value,
        "eligible": not blocked,
        "blocked": blocked,
        "targets": targets,
        "media_file_ids": [str(value) for value in sorted(media_file_ids, key=str)],
        "total_bytes": total_bytes,
        "_replacement_guard": replacement_guard,
    }


def _cleanup_digest(core: dict[str, Any]) -> str:
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def cleanup_preview(db: AsyncSession, candidate_id: UUID) -> dict[str, Any]:
    row = await db.get(UpgradeCandidate, candidate_id)
    if row is None:
        raise AppError("NOT_FOUND", "Upgrade candidate was not found.", status_code=404)
    if row.execution_state != "cleanup_required":
        raise AppError("UPGRADE_CLEANUP_NOT_READY", "Old media cleanup is only available after the replacement handoff is validated.", status_code=409)
    core = await _cleanup_core(db, row)
    expiry = int(time.time()) + CONFIRMATION_TTL_SECONDS
    payload = {
        "purpose": "upgrade_media_cleanup",
        "candidate_id": str(row.id),
        "inventory_hash": _cleanup_digest(core),
        "exp": expiry,
    }
    return {
        **core,
        "confirmation_token": sign_confirmation_payload(payload, get_settings().secret_key),
        "expires_at": datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat(),
        "warnings": (["Show cleanup removes only release media and same-stem sidecars; unrelated files are retained."] if row.media_type == MediaType.SHOWS else []),
    }


async def _perform_cleanup(
    db: AsyncSession,
    row: UpgradeCandidate,
    core: dict[str, Any],
    *,
    automatic: bool,
) -> dict[str, Any]:
    # Surface any pending database error before anything irreversible runs.
    # Callers commit immediately after this returns.
    await db.flush()
    deleted: list[str] = []
    for target in core["targets"]:
        path = Path(str(target["path"]))
        if not path.exists() and not path.is_symlink():
            continue
        if target["kind"] == "directory":
            await asyncio.to_thread(shutil.rmtree, path)
        else:
            await asyncio.to_thread(path.unlink)
        deleted.append(str(path))

    now = utcnow()
    old_ids = _ids(row.current_release_ids)
    if row.media_type == MediaType.MOVIES:
        releases = list((await db.scalars(select(MovieRelease).where(MovieRelease.id.in_(old_ids)))).all())
        directories = list(
            (
                await db.scalars(
                    select(MediaDirectory)
                    .options(selectinload(MediaDirectory.files))
                    .where(MediaDirectory.movie_release_id.in_(old_ids))
                )
            ).all()
        )
        for directory in directories:
            path = Path(directory.resolved_path)
            if not path.exists():
                directory.exists = False
                directory.missing_since = directory.missing_since or now
                directory.last_exists_check_at = now
                for media_file in directory.files:
                    media_file.exists = False
                    media_file.missing_since = media_file.missing_since or now
                    media_file.last_exists_check_at = now
        for release in releases:
            release.release_state = ReleaseState.REMOVED
            release.removed_at = release.removed_at or now
    else:
        media_file_ids = [UUID(value) for value in core.get("media_file_ids") or []]
        files = list((await db.scalars(select(MediaFile).where(MediaFile.id.in_(media_file_ids)))).all()) if media_file_ids else []
        for media_file in files:
            directory = await db.get(MediaDirectory, media_file.media_directory_id)
            if directory is None:
                continue
            path = Path(directory.resolved_path) / media_file.relative_path
            if not path.exists():
                media_file.exists = False
                media_file.missing_since = media_file.missing_since or now
                media_file.last_exists_check_at = now
        releases = list((await db.scalars(select(ShowRelease).where(ShowRelease.id.in_(old_ids)))).all())
        for release in releases:
            release.release_state = ReleaseState.REMOVED
        episode_ids = set(
            await db.scalars(select(EpisodeMediaMap.episode_id).where(EpisodeMediaMap.show_release_id.in_(old_ids)))
        )
        if episode_ids:
            from app.services.shows import refresh_episode_presence
            for episode_id in episode_ids:
                episode = await db.get(Episode, episode_id)
                if episode is not None:
                    await refresh_episode_presence(db, episode)

    row.cleanup_completed_at = now
    row.execution_state = "completed"
    row.execution_error = None
    await create_event(
        db,
        "upgrade.cleanup_completed",
        entity_type="upgrade_candidate",
        entity_id=row.id,
        message=(
            "Automatically deleted superseded media authorized when the upgrade was started."
            if automatic
            else "Deleted explicitly reviewed superseded media after the replacement handoff."
        ),
        details={
            "candidate_id": str(row.id),
            "deleted_paths": deleted,
            "old_release_ids": list(row.current_release_ids or []),
            "automatic": automatic,
            "history": await history_snapshot(db, row),
        },
    )
    publish_live_event("upgrade.execution", entity_type="upgrade_candidate", entity_id=row.id, data={"candidate_id": str(row.id), "state": row.execution_state})
    return {"candidate_id": row.id, "state": row.execution_state, "deleted_paths": deleted}


async def commit_cleanup(db: AsyncSession, candidate_id: UUID, confirmation_token: str) -> dict[str, Any]:
    payload = verify_confirmation_payload(confirmation_token, get_settings().secret_key)
    if payload is None or payload.get("purpose") != "upgrade_media_cleanup":
        raise AppError("INVALID_CONFIRMATION", "Upgrade cleanup confirmation is invalid.", status_code=409)
    try:
        token_candidate_id = UUID(str(payload["candidate_id"]))
        expiry = int(payload["exp"])
        digest = str(payload["inventory_hash"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("INVALID_CONFIRMATION", "Upgrade cleanup confirmation is malformed.", status_code=409) from exc
    if token_candidate_id != candidate_id:
        raise AppError("INVALID_CONFIRMATION", "Upgrade cleanup confirmation belongs to another candidate.", status_code=409)
    if expiry < int(time.time()):
        raise AppError("CONFIRMATION_EXPIRED", "Upgrade cleanup preview expired. Review the filesystem again.", status_code=409)

    row = await db.scalar(select(UpgradeCandidate).where(UpgradeCandidate.id == candidate_id).with_for_update())
    if row is None:
        raise AppError("NOT_FOUND", "Upgrade candidate was not found.", status_code=404)
    if row.execution_state != "cleanup_required":
        raise AppError("UPGRADE_CLEANUP_NOT_READY", "This upgrade is no longer waiting for media cleanup.", status_code=409)
    core = await _cleanup_core(db, row)
    if _cleanup_digest(core) != digest:
        raise AppError("DELETE_PREVIEW_STALE", "Old media changed since the cleanup preview. Review it again before deleting anything.", status_code=409)
    if core["blocked"]:
        raise AppError("UPGRADE_CLEANUP_BLOCKED", "Old media cleanup contains shared or unsafe paths and cannot be executed automatically.", status_code=409)

    await consume_confirmation_jti(db, payload)
    return await _perform_cleanup(db, row, core, automatic=False)
