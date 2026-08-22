"""qBittorrent observation and configuration services.

The adapter is deliberately kept separate from reconciliation: polling only
persists what qBittorrent reports.  A later state engine can consume the
durable Torrent/TorrentClientObservation rows without making this integration
move, rename, import, or delete media.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.qbittorrent import QBittorrentClient, QBittorrentError, TorrentObservation
from app.models.domain import (
    IntegrationType,
    JobStatus,
    MediaType,
    RemotePathMapping,
    Severity,
    StorageRoot,
    Torrent,
    TorrentClientObservation,
)
from app.services.events import create_event, publish_live_event
from app.services.integration_state import ConfiguredDownloadClient, get_configured_download_client, list_configured_download_clients
from app.services.jobs import JobFailure, checkpoint_job, run_job
from app.services.torrent_archive import ensure_torrent_archived, refresh_torrent_manifest, torrent_archive_complete
from app.services.reconciliation import (
    associate_incoming_torrent,
    cancel_incoming_torrent,
    finalize_completed_torrent,
    reconcile_torrent_disagreements,
    resolve_problem,
)


QBitClientFactory = Callable[[str, str, str], QBittorrentClient]

logger = logging.getLogger(__name__)

_client_locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normal_path(value: str | None) -> str:
    """Normalize path separators for prefix comparisons without touching FS."""

    return _clean_path(value).casefold()


def _clean_path(value: str | None) -> str:
    """Normalize separators while preserving the path's original casing.

    qBittorrent paths are compared case-insensitively because the remote
    client may be Windows, but the mapped local path can be a case-sensitive
    Linux filesystem.  Never derive the local suffix from a case-folded path.
    """

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
    return normalized


def _inside_local_root(path: str | None, root: str) -> bool:
    """Check a resolved *local* path against a local storage root.

    Remote qBittorrent prefixes are matched case-insensitively, but after
    mapping Medialogue is operating in a Linux container.  The local mount
    namespace is case-sensitive.  Treating ``/Movies/...`` as if it were under
    the local root ``/movies`` lets an unmatched remote path masquerade as a
    valid local path and creates false TORRENT_PATH_NOT_FOUND Problems.
    """

    if not path or not root:
        return False
    try:
        Path(_clean_path(path)).resolve(strict=False).relative_to(
            Path(_clean_path(root)).resolve(strict=False)
        )
        return True
    except ValueError:
        return False


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
    reported_clean = _clean_path(reported_path)
    reported_normalized = reported_clean.casefold()
    candidates = sorted(
        mappings,
        key=lambda mapping: len(_normal_path(mapping.remote_prefix)),
        reverse=True,
    )
    for mapping in candidates:
        remote_clean = _clean_path(mapping.remote_prefix)
        remote_prefix = remote_clean.casefold()
        if reported_normalized == remote_prefix or reported_normalized.startswith(remote_prefix + "/"):
            # Comparison is case-insensitive, but preserve the remote path's
            # original suffix casing when constructing the Linux-local path.
            # Lower-casing this suffix makes valid mapped paths fail exists()
            # checks on case-sensitive filesystems.
            suffix = reported_clean[len(remote_clean) :].lstrip("/")
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


async def get_download_client(db: AsyncSession, client_id: UUID) -> ConfiguredDownloadClient | None:
    return await get_configured_download_client(db, client_id)


async def test_download_client_connection(
    client: ConfiguredDownloadClient,
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
    client: ConfiguredDownloadClient,
    *,
    client_factory: QBitClientFactory = QBittorrentClient,
) -> dict[str, object]:
    """Poll one qBit client and persist relevant observations only."""

    adapter = client_factory(client.url, client.username or "", client.password or "")
    poll_started = perf_counter()
    checked_at = utcnow()
    client.last_polled_at = checked_at
    client.last_health_checked_at = checked_at
    previous_health = client.health
    try:
        observations = await adapter.list_torrents()
    except Exception as exc:
        client.health = "unavailable"
        client.latency_ms = round((perf_counter() - poll_started) * 1000)
        client.last_error = str(exc)
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

    # Connectivity health is deliberately independent from Medialogue's
    # reconciliation/parser/archive processing.  A bug while processing one
    # torrent must not make a reachable qBittorrent server appear offline.
    client.health = "healthy"
    client.last_success_at = utcnow()
    client.latency_ms = round((perf_counter() - poll_started) * 1000)
    client.last_error = None
    if previous_health != client.health:
        await create_event(
            db,
            "qbittorrent.health",
            entity_type="download_client",
            entity_id=client.id,
            message=f"qBittorrent client {client.name} is healthy.",
            details={"status": "healthy"},
        )
    # Persist the connectivity result before heavier reconciliation begins.
    # This prevents a later application-side processing failure from rolling
    # the qBittorrent health state back to an old/unavailable value.
    await db.commit()
    roots = (
        await db.scalars(
            select(StorageRoot).where(
                StorageRoot.enabled.is_(True),
                StorageRoot.last_scan_at.is_not(None),
                StorageRoot.media_type == MediaType(client.scope.value),
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
    root_by_id = {root.id: root for root in roots}
    # A mapping explicitly attached to a storage root must not remain active
    # for a different/disabled root.  Unscoped mappings remain available, but
    # the translated result is still checked against enabled roots below.
    mappings = [
        mapping
        for mapping in mappings
        if (
            mapping.storage_root_id is None
            or (
                mapping.storage_root_id in root_by_id
                and _inside_local_root(
                    mapping.local_prefix,
                    root_by_id[mapping.storage_root_id].resolved_root_path,
                )
            )
        )
    ]
    mapping_by_id = {mapping.id: mapping for mapping in mappings}
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
    relevant_count = added = completed = ignored = removed = processing_errors = 0
    for observed in observations:
        info_hash = observed.info_hash.lower()
        current_hashes.add(info_hash)
        reported_content = observed.content_path or observed.save_path
        resolved_content, mapping_id = resolve_remote_path(reported_content, mappings)
        matched_mapping = mapping_by_id.get(mapping_id) if mapping_id else None
        if matched_mapping is not None and matched_mapping.storage_root_id is not None:
            mapped_root = root_by_id.get(matched_mapping.storage_root_id)
            in_scope = bool(
                mapped_root
                and _inside_local_root(resolved_content, mapped_root.resolved_root_path)
            )
        else:
            in_scope = any(
                _inside_local_root(resolved_content, root.resolved_root_path)
                for root in roots
            )
        previously_known = info_hash in prior_by_hash
        if observed.checking and previously_known and not in_scope:
            # checkingResumeData can temporarily omit or alter paths. A known
            # torrent must remain managed during that transient state; path
            # scope is re-evaluated after qBittorrent finishes checking.
            relevant_count += 1
            prior = prior_by_hash[info_hash]
            torrent = await db.get(Torrent, prior.torrent_id)
            if torrent is not None:
                async with db.begin_nested():
                    if reported_content:
                        prior.reported_save_path = reported_content
                    if resolved_content:
                        prior.resolved_save_path = resolved_content
                    prior.state = observed.state
                    prior.progress = observed.progress
                    prior.category = observed.category or None
                    prior.tags = list(observed.tags)
                    prior.is_present = True
                    prior.last_seen_at = utcnow()
                    prior.removed_at = None
                    torrent.name = observed.name
                    torrent.last_seen_at = utcnow()
                    torrent.metadata_json = {
                        **(torrent.metadata_json or {}),
                        "progress": float(observed.progress),
                        "state": observed.state,
                        "checking": True,
                        "scope": client.scope.value,
                    }
                    publish_live_event(
                        "download.progress",
                        entity_type="torrent",
                        entity_id=torrent.id,
                        data={
                            "torrent_id": str(torrent.id),
                            "client_id": str(client.id),
                            "client_name": client.name,
                            "name": observed.name,
                            "progress": float(observed.progress),
                            "percent": round(float(observed.progress) * 100, 2),
                            "state": observed.state,
                            "scope": client.scope.value,
                            "reported_path": prior.reported_save_path,
                            "resolved_path": prior.resolved_save_path,
                        },
                    )
            continue
        # Configured storage roots are the hard boundary for reconciliation.
        # qBittorrent often contains torrents for libraries Medialogue cannot
        # and should not see.  Even a historically-known torrent must not
        # generate path/parser Problems after it moves outside those roots.
        # Keep its durable qBit observation for history, but stop treating it
        # as managed media and clear Problems whose premise was in-scope
        # filesystem access.
        if not in_scope:
            ignored += 1
            if previously_known:
                prior = prior_by_hash[info_hash]
                torrent = await db.get(Torrent, prior.torrent_id)
                if torrent is not None:
                    async with db.begin_nested():
                        prior.reported_save_path = reported_content
                        prior.resolved_save_path = resolved_content
                        prior.state = observed.state
                        prior.progress = observed.progress
                        prior.category = observed.category or None
                        prior.tags = list(observed.tags)
                        prior.is_present = True
                        prior.last_seen_at = utcnow()
                        prior.removed_at = None
                        await resolve_problem(db, "TORRENT_PATH_NOT_FOUND", "torrent", torrent.id)
                        await resolve_problem(db, "LOW_CONFIDENCE_MATCH", "torrent", torrent.id)
                        await resolve_problem(db, "TORRENT_SHOW_CONTAINER_REQUIRED", "torrent", torrent.id)
                        await cancel_incoming_torrent(db, torrent, emit_event=False)
                        # None means qBit still has the torrent, but its path is
                        # outside Medialogue's configured storage scope.  That
                        # is neither "removed externally" nor "path missing".
                        await reconcile_torrent_disagreements(db, torrent, qbit_present=None)
            continue

        relevant_count += 1
        added_before = added
        completed_before = completed
        try:
            # Isolate each torrent in a SAVEPOINT. A malformed/unexpected
            # torrent must not poison the entire qBittorrent client poll or
            # prevent later torrents from being observed.
            async with db.begin_nested():
                torrent = await db.scalar(select(Torrent).where(Torrent.info_hash == info_hash))
                timestamp_completed = _timestamp(observed.completed_at)
                was_complete = False
                if torrent is None:
                    torrent = Torrent(
                        info_hash=info_hash,
                        name=observed.name,
                        total_size=observed.total_size,
                        # A completion timestamp reported during a force check
                        # must not suppress the real completion transition once
                        # qBittorrent returns to a normal state.
                        completed_at=None if observed.checking else timestamp_completed,
                        tracker_summary={"tracker": observed.tracker} if observed.tracker else {},
                        metadata_json={
                            "content_path": observed.content_path,
                            "mapping_id": str(mapping_id) if mapping_id else None,
                            "completed": observed.complete,
                            "checking": observed.checking,
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
                        # A force recheck is transient and must not erase the
                        # durable fact that this torrent completed previously.
                        "completed": was_complete if observed.checking else observed.complete,
                        "checking": observed.checking,
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

                # qBittorrent's checkingUP/checkingDL/checkingResumeData states
                # are verification telemetry, not lifecycle transitions. Keep
                # the observation fresh but preserve every existing association
                # and wait for the next normal state before attaching,
                # finalizing, archiving, or reconciling disagreements.
                if observed.checking:
                    continue

                # Persist an Incoming association while downloading. Completion is
                # authoritative, but attachment additionally requires shared path,
                # directory, filename, and TMDB/manual identity verification. Plex
                # remains read-only presence/path evidence and never overrides identity.
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
        except Exception as exc:
            # Python-side counters are not part of the database SAVEPOINT.
            # Restore them when the torrent's database work was rolled back.
            added = added_before
            completed = completed_before
            processing_errors += 1
            logger.exception(
                "qBittorrent torrent processing failed; continuing with remaining torrents",
                extra={
                    "entity_type": "download_client",
                    "entity_id": str(client.id),
                    "torrent_info_hash": info_hash,
                    "torrent_name": observed.name,
                },
            )
            continue

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
                    # A prior duplicate-resolution removal may have failed,
                    # but the torrent can later disappear through a retry or
                    # manual qBittorrent action. Fresh qBit evidence then
                    # proves that failure Problem is no longer current.
                    await resolve_problem(db, "QBIT_REMOVE_FAILED", "torrent", torrent.id)
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
        "processing_errors": processing_errors,
        "message": (
            f"{processing_errors} torrent(s) could not be processed; qBittorrent connectivity remained healthy."
            if processing_errors else None
        ),
    }


async def poll_download_client(
    db: AsyncSession,
    client: ConfiguredDownloadClient,
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
    clients = [client for client in await list_configured_download_clients(db) if client.enabled]
    results: list[dict[str, object]] = []
    for client in clients:
        client_id = client.id
        last = client.last_polled_at
        interval = max(5, client.poll_interval_seconds or 30)
        # Repeated credential retries are actively harmful: qBittorrent can
        # ban the caller IP after only a few failed logins. Once an auth error
        # is confirmed, leave recovery to an explicit Test/Refresh or config
        # update instead of retrying every poll interval.
        if client.health == "unavailable" and client.last_error and (
            "rejected the configured username/password" in client.last_error
            or "temporarily banned Medialogue's IP" in client.last_error
            or "rejected WebAPI authentication" in client.last_error
        ):
            continue
        if last is not None and (now - last).total_seconds() < interval:
            continue
        try:
            result = await poll_download_client(db, client, client_factory=client_factory)
            await db.commit()
        except Exception as exc:
            # Connectivity is committed immediately after list_torrents() succeeds.
            # A parser/reconciliation/archive bug for one client must neither mark
            # qBittorrent offline nor prevent the remaining clients from polling.
            await db.rollback()
            fresh = await get_configured_download_client(db, client_id)
            logger.exception(
                "qBittorrent client processing failed after connectivity succeeded",
                extra={"entity_type": "download_client", "entity_id": str(client_id)},
            )
            result = {
                "client_id": client_id,
                "status": fresh.health if fresh and fresh.health else "unknown",
                "observed": 0,
                "relevant": 0,
                "added": 0,
                "completed": 0,
                "removed": 0,
                "ignored": 0,
                "message": f"qBittorrent connectivity succeeded, but Medialogue could not process this poll: {exc}",
            }
        results.append(result)
    return results


def _job_poll_result(result: dict[str, object]) -> dict[str, object]:
    return {key: (str(value) if key == "client_id" and value is not None else value) for key, value in result.items()}


async def run_download_client_poll(
    job_id: UUID,
    client_id: UUID,
    *,
    client_factory=QBittorrentClient,
) -> None:
    """Poll one qBittorrent client as a durable background Job."""

    async def worker(db, job) -> None:
        client = await get_configured_download_client(db, client_id)
        if client is None:
            return
        if not client.enabled:
            raise JobFailure(
                "DOWNLOAD_CLIENT_DISABLED",
                "qBittorrent client is disabled.",
                progress={"current": 0, "total": 1, "percent": 0, "stage": "failed", "detail": "qBittorrent client is disabled."},
            )

        client_name = client.name
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"current": 0, "total": 1, "percent": 0, "stage": "polling", "detail": f"Polling qBittorrent client {client_name}…"},
        )
        try:
            result = _job_poll_result(await poll_download_client(db, client, client_factory=client_factory))
            summary = {"client_id": str(client.id), "client_name": client_name, **result, "message": f"qBittorrent poll completed for {client_name}."}
            await checkpoint_job(
                db,
                job,
                status=JobStatus.COMPLETED,
                progress={"current": 1, "total": 1, "percent": 100, "stage": "completed", "detail": summary["message"]},
                summary=summary,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise JobFailure(
                "QBITTORRENT_POLL_FAILED",
                str(exc),
                progress={"current": 1, "total": 1, "percent": 100, "stage": "failed", "detail": f"qBittorrent poll failed for {client_name}."},
            )

    await run_job(
        job_id,
        worker,
        failure_code="QBITTORRENT_POLL_FAILED",
        failure_message="qBittorrent poll failed.",
    )


async def run_all_download_client_polls(
    job_id: UUID,
    *,
    client_factory=QBittorrentClient,
) -> None:
    """Poll all enabled qBittorrent clients as one durable background Job."""

    async def worker(db, job) -> None:
        clients = [client for client in await list_configured_download_clients(db) if client.enabled]
        total = len(clients)
        results: list[dict[str, object]] = []
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"current": 0, "total": total, "percent": 0, "stage": "polling", "detail": f"Polling {total} qBittorrent client{'s' if total != 1 else ''}…"},
            summary={"client_count": total, "results": []},
        )
        for index, client in enumerate(clients, start=1):
            await db.refresh(job)
            if job.status == JobStatus.CANCELLED:
                return
            client_name = client.name
            try:
                results.append(_job_poll_result(await poll_download_client(db, client, client_factory=client_factory)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await db.rollback()
                results.append({"client_id": str(client.id), "client_name": client_name, "status": "unavailable", "message": str(exc)})
            summary = {"client_count": total, "results": results}
            await checkpoint_job(
                db,
                job,
                progress={"current": index, "total": total, "percent": round(index * 100 / total, 1) if total else 100, "stage": "polling", "detail": f"Polled {client_name} ({index}/{total})."},
                summary=summary,
            )
        summary = {"client_count": total, "results": results, "message": f"qBittorrent poll completed for {total} client{'s' if total != 1 else ''}."}
        await checkpoint_job(
            db,
            job,
            status=JobStatus.COMPLETED,
            progress={"current": total, "total": total, "percent": 100, "stage": "completed", "detail": summary["message"]},
            summary=summary,
        )

    await run_job(
        job_id,
        worker,
        failure_code="QBITTORRENT_POLL_FAILED",
        failure_message="qBittorrent polling failed.",
        failure_progress={"current": 0, "total": 0, "percent": 0, "stage": "failed", "detail": "qBittorrent polling failed."},
    )
