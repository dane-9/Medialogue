"""Linking a torrent to files already on disk, instead of downloading them again.

Media that was never linked to a torrent can turn up on a tracker as the very
same release. Comparing the torrent's file list with the files on disk finds
that, and qBittorrent's own file check proves it: the torrent is added stopped
and pointed at the existing folder, and only started once every piece checks
out. A torrent that started on a partial match would download its differences
over the good files, so one that falls short is removed, leaving them untouched.
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.integrations.qbittorrent import torrent_file_listing, torrent_payload_info_hash
from app.models.domain import (
    AssociationType,
    EpisodeMediaMap,
    IntegrationType,
    MediaDirectory,
    MediaFile,
    MediaRole,
    MediaType,
    MovieRelease,
    MovieReleaseTorrent,
    RemotePathMapping,
    Severity,
    ShowRelease,
    ShowReleaseTorrent,
    Torrent,
    TorrentClientObservation,
    UpgradeCandidate,
)
from app.services.events import create_event, publish_live_event
from app.services.qbittorrent_paths import _normalized_path

logger = logging.getLogger(__name__)

LINKING = "linking"
#: An observation this soon after adding may predate qBittorrent's check.
SETTLE_AFTER = timedelta(minutes=2)
#: qBittorrent never reported the torrent at all.
LOST_AFTER = timedelta(minutes=30)
_BUSY_STATES = ("checking", "moving", "queuedforchecking")
#: Results of one title whose .torrent is fetched to compare it.
_MAX_COMPARED = 3
_SIZE_TOLERANCE = 0.02


@dataclass(frozen=True, slots=True)
class FilesOnDisk:
    """A release's files, relative to the folder qBittorrent would be pointed at."""

    directory: str
    files: dict[str, int]
    videos: frozenset[str]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _sizes(directory: str, relative_paths: list[str]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for relative in relative_paths:
        try:
            sizes[relative] = os.stat(posixpath.join(directory, relative)).st_size
        except OSError:
            continue
    return sizes


async def files_on_disk(db: AsyncSession, media_type: MediaType, release_ids: list[Any]) -> FilesOnDisk | None:
    """The files behind these releases, or None unless they sit in one folder."""

    if not release_ids:
        return None
    if media_type == MediaType.MOVIES:
        directories = list((await db.scalars(
            select(MediaDirectory).options(selectinload(MediaDirectory.files))
            .where(MediaDirectory.movie_release_id.in_(release_ids), MediaDirectory.exists.is_(True))
        )).all())
        if len(directories) != 1:
            return None
        directory = directories[0]
        wanted = [item.relative_path for item in directory.files if item.exists and item.media_role != MediaRole.OTHER_MEDIA]
    else:
        rows = (await db.execute(
            select(MediaFile.relative_path, MediaFile.media_directory_id)
            .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
            .where(EpisodeMediaMap.show_release_id.in_(release_ids), MediaFile.exists.is_(True))
        )).all()
        if len({directory_id for _, directory_id in rows}) != 1:
            return None
        directory = await db.scalar(
            select(MediaDirectory).options(selectinload(MediaDirectory.files)).where(MediaDirectory.id == rows[0][1])
        )
        if directory is None or not directory.exists:
            return None
        wanted = [relative for relative, _ in rows]
    wanted = [path.replace("\\", "/") for path in wanted]
    if not wanted:
        return None
    # A show's folder can hold every season: the pack's files sit in one of them.
    anchor = posixpath.commonpath([posixpath.dirname(path) for path in wanted])
    prefix = f"{anchor}/" if anchor else ""
    relative_paths = [path for path in (item.relative_path.replace("\\", "/") for item in directory.files if item.exists) if path.startswith(prefix)]
    folder = _normalized_path(posixpath.join(directory.resolved_path.replace("\\", "/"), anchor))
    sizes = await asyncio.to_thread(_sizes, folder, [path[len(prefix):] for path in relative_paths])
    videos = frozenset(path[len(prefix):] for path in wanted)
    if not videos <= set(sizes):
        return None
    return FilesOnDisk(directory=folder, files=sizes, videos=videos)


def torrent_matches_files(listing: list[tuple[str, int]] | None, disk: FilesOnDisk) -> bool:
    """Every torrent file is on disk at its exact size, and it covers every video."""

    if not listing:
        return False
    return all(disk.files.get(path) == size for path, size in listing) and disk.videos <= {path for path, _ in listing}


def _comparable(name: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", "", (name or "").casefold())


def _worth_comparing(value: dict[str, Any], release_name: str, disk: FilesOnDisk) -> bool:
    if not value.get("eligible") or not str(value.get("download_url") or "") or str(value["download_url"]).casefold().startswith("magnet:"):
        return False
    if _comparable(str(value.get("title") or "")) == _comparable(release_name):
        return True
    size = value.get("size")
    total = sum(disk.files[path] for path in disk.videos)
    return bool(size) and total > 0 and abs(int(size) - total) <= total * _SIZE_TOLERANCE


async def note_same_file_results(
    db: AsyncSession,
    *,
    target: Any,
    results: list[dict[str, Any]],
    indexers: list[Any],
    client_factory: Callable[..., Any],
) -> None:
    """Mark the search results that are the very files this title already has."""

    disk = await files_on_disk(db, target.media_type, [item.release_id for item in target.releases])
    if disk is None:
        return
    by_id = {str(indexer.id): indexer for indexer in indexers}
    compared = 0
    for value in results:
        indexer = by_id.get(str(value.get("indexer_id")))
        if indexer is None or not _worth_comparing(value, target.reference.release_name, disk):
            continue
        if compared >= _MAX_COMPARED:
            break
        compared += 1
        torznab = client_factory(indexer.torznab_url, indexer.api_key, timeout=float(indexer.timeout_seconds))
        try:
            payload = await torznab.fetch_torrent(value["download_url"])
        except Exception:
            logger.debug("could not fetch a torrent to compare it", extra={"title": value.get("title")}, exc_info=True)
            continue
        finally:
            await torznab.close()
        if not torrent_matches_files(torrent_file_listing(payload), disk):
            continue
        info_hash = torrent_payload_info_hash(payload)
        known = await db.scalar(select(Torrent).where(Torrent.info_hash == info_hash)) if info_hash else None
        if known is not None and (known.metadata_json or {}).get("link_check_failed"):
            value["link_failed"] = True
        else:
            value["same_file"] = True


def _same_result(row: UpgradeCandidate, value: dict[str, Any]) -> bool:
    return str(value.get("guid") or "") == row.guid and str(value.get("indexer_id") or "") == str(row.indexer_id or "")


async def apply_same_file_results(db: AsyncSession, target_key: str, results: list[dict[str, Any]]) -> None:
    """Point the candidate at the same-file result when none better was found."""

    from app.services.upgrade_candidates import apply_search_result

    row = await db.scalar(select(UpgradeCandidate).where(UpgradeCandidate.target_key == target_key))
    if row is None or row.execution_state is not None:
        return
    own = next((value for value in results if _same_result(row, value)), None)
    if own is None:
        return
    level = int(own.get("revision_rank") or 0)
    linkable = next((
        value for value in results
        if value.get("same_file") and value.get("eligible") and value.get("quality") == row.quality
        and value.get("custom_format_score") == row.custom_format_score and int(value.get("revision_rank") or 0) >= level
    ), None)
    chosen = linkable or own
    if chosen.get("same_file") or chosen.get("link_failed"):
        apply_search_result(row, chosen)
        await db.flush()


def local_to_remote_path(local: str, mappings: list[RemotePathMapping]) -> str | None:
    """A folder as qBittorrent sees it, or None if no mapping covers it."""

    clean = _normalized_path(local)
    for mapping in sorted(mappings, key=lambda item: len(item.local_prefix), reverse=True):
        prefix = _normalized_path(mapping.local_prefix)
        if clean == prefix or clean.startswith(prefix + "/"):
            remote = mapping.remote_prefix.rstrip("/\\")
            separator = "\\" if "\\" in mapping.remote_prefix and "/" not in mapping.remote_prefix else "/"
            suffix = clean[len(prefix):].lstrip("/")
            return f"{remote}{separator}{suffix.replace('/', separator)}" if suffix else remote
    return clean if not mappings else None


async def submit_same_file_link(
    db: AsyncSession,
    row: UpgradeCandidate,
    *,
    client: Any,
    indexer: Any,
    category: str | None,
    torznab_client_factory: Callable[..., Any],
    qbittorrent_client_factory: Callable[..., Any],
) -> UpgradeCandidate:
    """Add the torrent stopped over the existing files and have them checked."""

    from app.services.upgrade_execution import _assert_not_blocklisted
    from app.services.upgrade_payloads import _ids

    disk = await files_on_disk(db, row.media_type, _ids(row.current_release_ids))
    if disk is None or not row.download_url or row.download_url.casefold().startswith("magnet:"):
        raise AppError("SAME_FILE_CHANGED", "The files on disk changed. Search for this title again.", status_code=409)
    mappings = list((await db.scalars(select(RemotePathMapping).where(
        RemotePathMapping.enabled.is_(True),
        RemotePathMapping.integration_type == IntegrationType.QBITTORRENT,
        (RemotePathMapping.integration_id == client.id) | RemotePathMapping.integration_id.is_(None),
    ))).all())
    save_path = local_to_remote_path(disk.directory, mappings)
    if save_path is None:
        raise AppError("SAME_FILE_NO_PATH", f"No remote path mapping tells {client.name} where {disk.directory} is.", status_code=409)

    torznab = torznab_client_factory(indexer.torznab_url, indexer.api_key, timeout=float(indexer.timeout_seconds))
    adapter = qbittorrent_client_factory(client.url, client.username or "", client.password or "")
    try:
        if category is not None and not any(item.name == category for item in await adapter.list_categories()):
            raise AppError("QBITTORRENT_CATEGORY_NOT_FOUND", "The selected qBittorrent category no longer exists.", status_code=409)
        payload = await torznab.fetch_torrent(row.download_url)
        info_hash = torrent_payload_info_hash(payload)
        if not info_hash or not torrent_matches_files(torrent_file_listing(payload), disk):
            raise AppError("SAME_FILE_CHANGED", "This torrent no longer matches the files on disk. Search for this title again.", status_code=409)
        await _assert_not_blocklisted(db, row, info_hash)
        if await adapter.get_torrent(info_hash) is not None:
            raise AppError("TORRENT_ALREADY_PRESENT", "This torrent is already in the selected qBittorrent client.", status_code=409)
        # Stopped, so nothing can be downloaded; off automatic management, so
        # the category cannot send it elsewhere; without its top folder, so a
        # renamed folder on disk still lines up.
        await adapter.add_torrent(
            payload, filename=f"{info_hash}.torrent", save_path=save_path, category=category, auto_tmm=False,
            tags=tuple(client.tags or []), content_layout="nosubfolder", stopped=True,
        )
        try:
            await adapter.recheck_torrents([info_hash])
        except Exception:
            await adapter.remove_torrent(info_hash, delete_files=False)
            raise
    except AppError:
        raise
    except Exception as exc:
        raise AppError("UPGRADE_SUBMISSION_FAILED", f"Could not add the torrent to check its files: {exc}", status_code=503) from exc
    finally:
        await torznab.close()
        await adapter.close()

    row.execution_state = LINKING
    row.submitted_info_hash = info_hash
    row.selected_download_client_id = client.id
    row.selected_download_client_name = client.name
    row.selected_category = category
    row.submitted_at = _utcnow()
    row.torrent_id = None
    row.incoming_release_ids = []
    row.validated_at = None
    row.execution_error = None
    await create_event(
        db, "upgrade.submitted", entity_type="upgrade_candidate", entity_id=row.id,
        message=f"Checking {row.title} against the files already on disk in {client.name}.",
        details={"candidate_id": str(row.id), "download_client_id": str(client.id), "category": category,
                 "current_release_ids": list(row.current_release_ids or [])},
    )
    publish_live_event("upgrade.execution", entity_type="upgrade_candidate", entity_id=row.id, data={"candidate_id": str(row.id), "state": row.execution_state})
    await db.flush()
    return row


def release_linking_row(row: UpgradeCandidate, error: str | None = None, *, check_failed: bool = False) -> None:
    """Hand a title back to the list, as an ordinary found replacement."""

    snapshot = {key: value for key, value in (row.comparison_snapshot or {}).items() if key != "same_file"}
    if check_failed:
        snapshot["link_failed"] = True
    row.comparison_snapshot = snapshot
    row.search_results = [
        {key: value for key, value in item.items() if key != "same_file"} | ({"link_failed": True} if check_failed and _same_result(row, item) else {})
        for item in (row.search_results or []) if isinstance(item, dict)
    ]
    row.execution_state = None
    row.execution_error = error
    row.torrent_id = None
    row.submitted_info_hash = None
    row.selected_download_client_id = None
    row.selected_download_client_name = None
    row.selected_category = None
    row.submitted_at = None
    publish_live_event("upgrade.execution", entity_type="upgrade_candidate", entity_id=row.id, data={"candidate_id": str(row.id), "state": None})


async def _finish(db: AsyncSession, row: UpgradeCandidate, torrent: Torrent, now: datetime) -> None:
    from app.services.media_history import history_snapshot
    from app.services.upgrade_payloads import _ids

    movies = row.media_type == MediaType.MOVIES
    link_model = MovieReleaseTorrent if movies else ShowReleaseTorrent
    release_column = link_model.movie_release_id if movies else link_model.show_release_id
    release_field = "movie_release_id" if movies else "show_release_id"
    for release_id in _ids(row.current_release_ids):
        if await db.scalar(select(link_model).where(release_column == release_id, link_model.torrent_id == torrent.id)) is None:
            db.add(link_model(**{release_field: release_id}, torrent_id=torrent.id, association_type=AssociationType.ATTACHED))
        release = await db.get(MovieRelease if movies else ShowRelease, release_id)
        if release is not None and not release.tracker_title:
            release.tracker_title = row.title
    torrent.metadata_json = {**(torrent.metadata_json or {}), "tracker_title": row.title, "indexer_name": row.indexer_name}
    row.execution_state = "completed"
    row.execution_error = None
    row.validated_at = now
    await create_event(
        db, "replacement.finished", entity_type="upgrade_candidate", entity_id=row.id,
        message=f"{row.title} was linked to the files already on disk; nothing was downloaded.",
        details={"candidate_id": str(row.id), "old_media_retained": True, "linked_existing_files": True,
                 "current_release_ids": list(row.current_release_ids or []),
                 "history": {**await history_snapshot(db, row), "linked": True}},
    )
    publish_live_event("upgrade.execution", entity_type="upgrade_candidate", entity_id=row.id, data={"candidate_id": str(row.id), "state": row.execution_state})


async def settle_same_file_links(
    db: AsyncSession,
    client: Any,
    *,
    client_factory: Callable[..., Any],
    now: datetime | None = None,
) -> None:
    """Start the torrents whose files checked out; remove the ones that did not."""

    now = now or _utcnow()
    rows = list((await db.scalars(select(UpgradeCandidate).where(
        UpgradeCandidate.execution_state == LINKING, UpgradeCandidate.selected_download_client_id == client.id,
    ))).all())
    for row in rows:
        age = now - (_aware(row.submitted_at) or now)
        if row.torrent_id is None:
            if age >= LOST_AFTER:
                release_linking_row(row, "qBittorrent never reported the torrent, so its files could not be checked.")
            continue
        torrent = await db.get(Torrent, row.torrent_id)
        observation = await db.scalar(select(TorrentClientObservation).where(
            TorrentClientObservation.torrent_id == row.torrent_id,
            TorrentClientObservation.download_client_id == client.id,
            TorrentClientObservation.is_present.is_(True),
        ))
        if torrent is None or observation is None or observation.progress is None:
            continue
        if str(observation.state or "").casefold().startswith(_BUSY_STATES):
            continue
        complete = float(observation.progress) >= 1
        if not complete and age < SETTLE_AFTER:
            continue
        adapter = client_factory(client.url, client.username or "", client.password or "")
        try:
            if complete:
                await adapter.start_torrents([torrent.info_hash])
            else:
                await adapter.remove_torrent(torrent.info_hash, delete_files=False)
        finally:
            await adapter.close()
        if complete:
            await _finish(db, row, torrent, now)
            continue
        torrent.metadata_json = {**(torrent.metadata_json or {}), "link_check_failed": True}
        await create_event(
            db, "replacement.link_failed", entity_type="upgrade_candidate", entity_id=row.id, severity=Severity.WARNING,
            message=f"{row.title} has the same name as the files on disk, but its contents differ. The torrent was removed and the files were left alone.",
            details={"candidate_id": str(row.id), "info_hash": torrent.info_hash},
        )
        release_linking_row(row, check_failed=True)
