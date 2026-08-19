"""qBittorrent observation and configuration services.

The adapter is deliberately kept separate from reconciliation: polling only
persists what qBittorrent reports.  A later state engine can consume the
durable Torrent/TorrentClientObservation rows without making this integration
move, rename, import, or delete media.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from time import perf_counter
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.qbittorrent import QBittorrentClient, QBittorrentError, TorrentObservation
from app.models.domain import (
    DownloadClient,
    IntegrationType,
    MediaType,
    RemotePathMapping,
    Severity,
    StorageRoot,
    Torrent,
    TorrentClientObservation,
)
from app.services.events import create_event, publish_live_event
from app.services.torrent_archive import ensure_torrent_archived, refresh_torrent_manifest, torrent_archive_complete
from app.services.reconciliation import (
    associate_incoming_torrent,
    cancel_incoming_torrent,
    finalize_completed_torrent,
    reconcile_torrent_disagreements,
)


QBitClientFactory = Callable[[str, str, str], QBittorrentClient]

_client_locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normal_path(value: str | None) -> str:
    """Normalize path separators for prefix comparisons without touching FS."""

    if not value:
        return ""
    normalized = value.replace("\\", "/")
    # Preserve a POSIX root and a Windows drive while removing duplicate
    # separators and trailing slashes.  Comparisons are case-insensitive so a
    # remote Windows client can be used by a Linux container safely.
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized.casefold()


def _inside(path: str, root: str) -> bool:
    child = _normal_path(path)
    parent = _normal_path(root)
    return bool(child and parent and (child == parent or child.startswith(parent + "/")))


def _join_prefix(local_prefix: str, suffix: str) -> str:
    local = local_prefix.replace("\\", "/").rstrip("/")
    if not suffix:
        return local
    return f"{local}/{suffix.lstrip('/')}"


def resolve_remote_path(
    reported_path: str | None,
    mappings: list[RemotePathMapping],
) -> tuple[str | None, UUID | None]:
    """Translate a qBit path while retaining the original path separately."""

    if not reported_path:
        return None, None
    reported_normalized = _normal_path(reported_path)
    candidates = sorted(
        mappings,
        key=lambda mapping: len(_normal_path(mapping.remote_prefix)),
        reverse=True,
    )
    for mapping in candidates:
        remote_prefix = _normal_path(mapping.remote_prefix)
        if reported_normalized == remote_prefix or reported_normalized.startswith(remote_prefix + "/"):
            # Calculate suffix from normalized values only for mapping.  The
            # actual reported spelling remains in the observation field.
            suffix = reported_normalized[len(remote_prefix) :].lstrip("/")
            return _join_prefix(mapping.local_prefix, suffix), mapping.id
    # No mapping is needed when qBit and the app share a mount path.  It is
    # still checked against configured roots before being persisted.
    return reported_path, None


def _timestamp(value: int | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


async def get_download_client(db: AsyncSession, client_id: UUID) -> DownloadClient | None:
    return await db.get(DownloadClient, client_id)


async def test_download_client_connection(
    client: DownloadClient,
    *,
    client_factory: QBitClientFactory = QBittorrentClient,
) -> dict[str, object]:
    started = perf_counter()
    adapter = client_factory(client.url, client.username or "", client.password or "")
    try:
        result = await adapter.health()
        return {
            "status": "healthy",
            "version": result.get("version"),
            "latency_ms": round((perf_counter() - started) * 1000),
        }
    finally:
        await adapter.close()


async def _poll_download_client(
    db: AsyncSession,
    client: DownloadClient,
    *,
    client_factory: QBitClientFactory = QBittorrentClient,
) -> dict[str, object]:
    """Poll one qBit client and persist relevant observations only."""

    adapter = client_factory(client.url, client.username or "", client.password or "")
    client.last_polled_at = utcnow()
    previous_health = client.health
    try:
        observations = await adapter.list_torrents()
    except Exception as exc:
        client.health = "unavailable"
        if previous_health != client.health:
            await create_event(
                db,
                "qbittorrent.health",
                entity_type="download_client",
                entity_id=client.id,
                message=f"qBittorrent client {client.name} is unavailable.",
                severity=Severity.ERROR,
                details={"status": "unavailable", "error": str(exc)},
            )
        return {
            "client_id": client.id,
            "status": "unavailable",
            "observed": 0,
            "relevant": 0,
            "added": 0,
            "completed": 0,
            "removed": 0,
            "ignored": 0,
            "message": str(exc),
        }
    finally:
        await adapter.close()

    client.health = "healthy"
    if previous_health != client.health:
        await create_event(
            db,
            "qbittorrent.health",
            entity_type="download_client",
            entity_id=client.id,
            message=f"qBittorrent client {client.name} is healthy.",
            details={"status": "healthy"},
        )
    roots = (
        await db.scalars(
            select(StorageRoot).where(
                StorageRoot.enabled.is_(True), StorageRoot.media_type == MediaType(client.scope.value)
            )
        )
    ).all()
    mappings = (
        await db.scalars(
            select(RemotePathMapping).where(
                RemotePathMapping.enabled.is_(True),
                RemotePathMapping.integration_type == IntegrationType.QBITTORRENT,
                (RemotePathMapping.integration_id == client.id) | RemotePathMapping.integration_id.is_(None),
            )
        )
    ).all()
    prior_rows = (
        await db.scalars(
            select(TorrentClientObservation)
            .where(TorrentClientObservation.download_client_id == client.id)
        )
    ).all()
    prior_by_hash: dict[str, TorrentClientObservation] = {}
    for row in prior_rows:
        prior_by_hash.setdefault(row.torrent_id.hex, row)
    # Hashes are used below after loading the related Torrent rows.  Keeping a
    # dedicated query avoids relying on lazy loading in async request paths.
    prior_torrents = (
        await db.scalars(select(Torrent).where(Torrent.id.in_({row.torrent_id for row in prior_rows})))
    ).all() if prior_rows else []
    prior_hash_by_torrent = {torrent.id: torrent.info_hash.lower() for torrent in prior_torrents}
    prior_by_hash = {
        prior_hash_by_torrent[row.torrent_id]: row
        for row in prior_rows
        if row.torrent_id in prior_hash_by_torrent
    }

    current_hashes: set[str] = set()
    relevant_count = added = completed = ignored = removed = 0
    for observed in observations:
        info_hash = observed.info_hash.lower()
        current_hashes.add(info_hash)
        reported_content = observed.content_path or observed.save_path
        resolved_content, mapping_id = resolve_remote_path(reported_content, mappings)
        in_scope = any(_inside(resolved_content, root.resolved_root_path) for root in roots)
        previously_known = info_hash in prior_by_hash
        # A known association remains observable after a move or root outage;
        # an unrelated torrent outside configured roots is never imported.
        if not in_scope and not previously_known:
            ignored += 1
            continue

        relevant_count += 1
        torrent = await db.scalar(select(Torrent).where(Torrent.info_hash == info_hash))
        timestamp_completed = _timestamp(observed.completed_at)
        was_complete = False
        if torrent is None:
            torrent = Torrent(
                info_hash=info_hash,
                name=observed.name,
                total_size=observed.total_size,
                completed_at=timestamp_completed,
                tracker_summary={"tracker": observed.tracker} if observed.tracker else {},
                metadata_json={
                    "content_path": observed.content_path,
                    "mapping_id": str(mapping_id) if mapping_id else None,
                    "completed": observed.complete,
                },
            )
            db.add(torrent)
            await db.flush()
            added += 1
            await create_event(
                db,
                "torrent.detected",
                entity_type="torrent",
                entity_id=torrent.id,
                message=f"Torrent detected from qBittorrent client {client.name}.",
                details={"client_id": str(client.id), "info_hash": info_hash, "name": observed.name},
            )
            if observed.complete:
                completed += 1
                await create_event(
                    db,
                    "download.completed",
                    entity_type="torrent",
                    entity_id=torrent.id,
                    message=f"Download completed: {observed.name}.",
                    details={"client_id": str(client.id), "info_hash": info_hash},
                )
        else:
            was_complete = torrent.completed_at is not None or bool(
                torrent.metadata_json.get("completed") if torrent.metadata_json else False
            )
            torrent.name = observed.name
            torrent.total_size = observed.total_size
            torrent.last_seen_at = utcnow()
            if timestamp_completed:
                torrent.completed_at = timestamp_completed
            if observed.tracker:
                torrent.tracker_summary = {"tracker": observed.tracker}
            torrent.metadata_json = {
                **(torrent.metadata_json or {}),
                "content_path": observed.content_path,
                "mapping_id": str(mapping_id) if mapping_id else None,
                "completed": observed.complete,
            }
            if observed.complete and not was_complete:
                completed += 1
                await create_event(
                    db,
                    "download.completed",
                    entity_type="torrent",
                    entity_id=torrent.id,
                    message=f"Download completed: {observed.name}.",
                    details={"client_id": str(client.id), "info_hash": info_hash},
                )

        row = await db.scalar(
            select(TorrentClientObservation).where(
                TorrentClientObservation.torrent_id == torrent.id,
                TorrentClientObservation.download_client_id == client.id,
            )
        )
        previous_present = row.is_present if row is not None else None
        previous_progress = float(row.progress) if row is not None and row.progress is not None else None
        previous_state = row.state if row is not None else None
        if row is None:
            row = TorrentClientObservation(
                torrent_id=torrent.id,
                download_client_id=client.id,
                first_seen_at=utcnow(),
            )
            db.add(row)
        row.reported_save_path = reported_content
        row.resolved_save_path = resolved_content
        row.state = observed.state
        row.progress = observed.progress
        row.category = observed.category or None
        row.tags = list(observed.tags)
        row.is_present = True
        row.last_seen_at = utcnow()
        row.removed_at = None
        torrent.metadata_json = {
            **(torrent.metadata_json or {}),
            "progress": float(observed.progress),
            "state": observed.state,
            "scope": client.scope.value,
        }
        current_progress = float(observed.progress)
        if (
            previous_progress is None
            or abs(previous_progress - current_progress) >= 0.0001
            or previous_state != observed.state
            or previous_present is False
        ):
            publish_live_event(
                "download.progress",
                entity_type="torrent",
                entity_id=torrent.id,
                data={
                    "torrent_id": str(torrent.id),
                    "client_id": str(client.id),
                    "client_name": client.name,
                    "name": observed.name,
                    "progress": current_progress,
                    "percent": round(current_progress * 100, 2),
                    "state": observed.state,
                    "scope": client.scope.value,
                    "reported_path": reported_content,
                    "resolved_path": resolved_content,
                },
            )
        if previous_present is False:
            await create_event(
                db,
                "torrent.reappeared",
                entity_type="torrent",
                entity_id=torrent.id,
                message=f"Torrent reappeared in qBittorrent client {client.name}.",
                details={"client_id": str(client.id), "info_hash": info_hash},
            )

        # Persist an Incoming association while downloading. Completion is
        # authoritative, but attachment additionally requires shared path,
        # directory, filename, identity, and Plex-conflict verification.
        await associate_incoming_torrent(
            db,
            torrent,
            resolved_path=resolved_content,
            scope=MediaType(client.scope.value),
            complete=observed.complete,
        )
        if observed.complete:
            await finalize_completed_torrent(
                db,
                torrent,
                resolved_path=resolved_content,
                scope=MediaType(client.scope.value),
            )

        # Archive tracked torrents as soon as qBittorrent exposes their
        # metadata. The archive is independent of live qBit state and is
        # refreshed after reconciliation so identity/release/path evidence is
        # captured in the manifest. Failed exports are non-fatal and retry on
        # later polls (common while magnet metadata is still resolving).
        if not torrent_archive_complete(torrent):
            await ensure_torrent_archived(db, torrent, client, client_factory=client_factory)
        else:
            await refresh_torrent_manifest(db, torrent)

        await reconcile_torrent_disagreements(db, torrent, qbit_present=True)

    # A missing qBit row is historical evidence, not a delete operation.
    for row in prior_rows:
        info_hash = prior_hash_by_torrent.get(row.torrent_id)
        if info_hash not in current_hashes:
            if row.is_present:
                row.is_present = False
                row.removed_at = utcnow()
                row.last_seen_at = utcnow()
                removed += 1
                await create_event(
                    db,
                    "torrent.removed",
                    entity_type="torrent",
                    entity_id=row.torrent_id,
                    message=f"Torrent removed externally from qBittorrent client {client.name}.",
                    details={"client_id": str(client.id), "info_hash": info_hash},
                )
                torrent = await db.get(Torrent, row.torrent_id)
                if torrent is not None:
                    await cancel_incoming_torrent(db, torrent)
                    await reconcile_torrent_disagreements(db, torrent, qbit_present=False)

    return {
        "client_id": client.id,
        "status": "healthy",
        "observed": len(observations),
        "relevant": relevant_count,
        "added": added,
        "completed": completed,
        "removed": removed,
        "ignored": ignored,
    }


async def poll_download_client(
    db: AsyncSession,
    client: DownloadClient,
    *,
    client_factory: QBitClientFactory = QBittorrentClient,
) -> dict[str, object]:
    """Poll one qBit instance with a per-client non-overlap lock."""

    async with _client_locks[client.id]:
        return await _poll_download_client(db, client, client_factory=client_factory)


async def poll_due_download_clients(
    db: AsyncSession,
    *,
    client_factory: QBitClientFactory = QBittorrentClient,
) -> list[dict[str, object]]:
    """Poll enabled clients whose configured interval has elapsed."""

    now = utcnow()
    clients = (await db.scalars(select(DownloadClient).where(DownloadClient.enabled.is_(True)))).all()
    results: list[dict[str, object]] = []
    for client in clients:
        last = client.last_polled_at
        interval = max(5, client.poll_interval_seconds or 15)
        if last is not None and (now - last).total_seconds() < interval:
            continue
        result = await poll_download_client(db, client, client_factory=client_factory)
        await db.commit()
        results.append(result)
    return results
