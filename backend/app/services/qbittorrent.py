"""qBittorrent observation and configuration services.

The adapter is deliberately kept separate from reconciliation: polling only
persists what qBittorrent reports.  A later state engine can consume the
durable Torrent/TorrentClientObservation rows without making this integration
move, rename, import, or delete media.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Callable
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.qbittorrent import (
    QBittorrentAuthError,
    QBittorrentClient,
    QBittorrentError,
    TorrentObservation,
    TorrentSyncState,
    TorrentTrackerSnapshot,
)
from app.models.domain import (
    IntegrationType,
    InteractiveSearchResult,
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
from app.services.qbittorrent_paths import (
    _inside_local_root,
    resolve_remote_path,
)
from app.services.torrent_archive import ensure_torrent_archived, refresh_torrent_manifest, torrent_archive_complete, read_manifest
from app.services.torrent_tracking import (
    deactivate_client_tracker_health,
    persist_tracker_health,
    track_other_seeders,
    redact_tracker_message,
    tracker_endpoint_display,
)
from app.services.reconciliation import resolve_problem
from app.services.torrent_reconciliation import (
    associate_incoming_torrent,
    cancel_incoming_torrent,
    finalize_completed_torrent,
    reconcile_torrent_disagreements,
)
from app.services.tracker_checks import (
    _portable_metadata,
    _snapshot_trackers,
    _tracker_detail_batch,
    _tracker_is_active,
    _tracker_request_budget,
)


QBittorrentClientFactory = Callable[[str, str, str], QBittorrentClient]

logger = logging.getLogger(__name__)


async def _selected_tracker_title(
    db: AsyncSession,
    *,
    info_hash: str,
    torrent_name: str,
) -> str | None:
    """Return the exact selected indexer title for an observed torrent."""

    selected = await db.scalar(
        select(InteractiveSearchResult)
        .where(
            InteractiveSearchResult.selected_at.is_not(None),
            InteractiveSearchResult.submitted_info_hash == info_hash,
        )
        .order_by(InteractiveSearchResult.selected_at.desc())
        .limit(1)
    )
    title = str(selected.title or "").strip() if selected is not None else ""
    return title or None

_client_locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: int | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


async def get_download_client(db: AsyncSession, client_id: UUID) -> ConfiguredDownloadClient | None:
    return await get_configured_download_client(db, client_id)


async def refresh_observations_after_action(
    db: AsyncSession,
    client: ConfiguredDownloadClient,
    info_hashes: Sequence[str],
    *,
    client_factory: QBittorrentClientFactory = QBittorrentClient,
) -> int:
    """Re-read the live fields for torrents an action just touched.

    The regular poll is what reconciles media, archives and tracker health, and
    it only runs on the client's own interval. A user action needs the table to
    reflect the change now, so this fetches the current snapshot and updates the
    display fields only - no reconciliation, no archiving, no problems raised.
    """

    wanted = {value.lower() for value in info_hashes}
    if not wanted:
        return 0
    adapter = client_factory(client.url, client.username or "", client.password or "")
    try:
        observations = await adapter.list_torrents()
    except Exception:
        logger.debug("post-action qBittorrent refresh failed", exc_info=True)
        # The action itself already succeeded; a failed refresh only means the
        # table catches up on the next poll instead.
        return 0
    finally:
        await adapter.close()

    observed_by_hash = {item.info_hash.lower(): item for item in observations if item.info_hash.lower() in wanted}
    if not observed_by_hash:
        return 0
    rows = (
        await db.execute(
            select(TorrentClientObservation, Torrent)
            .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
            .where(
                TorrentClientObservation.download_client_id == client.id,
                Torrent.info_hash.in_(list(observed_by_hash)),
            )
        )
    ).all()
    updated = 0
    for row, torrent in rows:
        observed = observed_by_hash.get(torrent.info_hash.lower())
        if observed is None:
            continue
        row.state = observed.state
        row.progress = observed.progress
        row.download_speed = observed.download_speed
        row.upload_speed = observed.upload_speed
        row.eta_seconds = observed.eta
        row.amount_left = observed.amount_left
        row.ratio = observed.ratio
        row.seeds = observed.seeds
        row.seeds_total = observed.seeds_total
        row.leeches = observed.leeches
        row.leeches_total = observed.leeches_total
        row.availability = observed.availability
        track_other_seeders(row, now=utcnow())
        row.last_seen_at = utcnow()
        torrent.metadata_json = {
            **(torrent.metadata_json or {}),
            "progress": float(observed.progress),
            "checking": observed.checking,
        }
        updated += 1
    await db.commit()
    return updated


async def mark_removed_after_action(
    db: AsyncSession,
    client: ConfiguredDownloadClient,
    info_hashes: Sequence[str],
) -> int:
    """Record a user's "remove from qBittorrent" right away.

    Mirrors the poll's removal handling so the table, incoming links and
    tracker health reflect it now rather than on the next poll.
    """

    wanted = [value.lower() for value in info_hashes]
    rows = (
        await db.execute(
            select(TorrentClientObservation, Torrent)
            .join(Torrent, Torrent.id == TorrentClientObservation.torrent_id)
            .where(
                TorrentClientObservation.download_client_id == client.id,
                TorrentClientObservation.is_present.is_(True),
                func.lower(Torrent.info_hash).in_(wanted),
            )
        )
    ).all()
    now = utcnow()
    for observation, torrent in rows:
        observation.is_present = False
        observation.removed_at = now
        # Removed on purpose from Downloads: the re-add task leaves it alone.
        torrent.readd_state = "removed"
        torrent.auto_readded_at = torrent.auto_readded_at or now
        await create_event(
            db,
            "torrent.removed",
            entity_type="torrent",
            entity_id=torrent.id,
            message=f"Torrent removed from qBittorrent client {client.name} from Downloads (files kept).",
            details={"client_id": str(client.id), "info_hash": torrent.info_hash, "delete_files": False},
        )
        await deactivate_client_tracker_health(db, torrent.id, client.id)
        await cancel_incoming_torrent(db, torrent)
        await reconcile_torrent_disagreements(db, torrent, qbit_present=False)
    await db.commit()
    return len(rows)


async def run_torrent_action(
    client: ConfiguredDownloadClient,
    action: str,
    info_hashes: Sequence[str],
    *,
    value: float | None = None,
    client_factory: QBittorrentClientFactory = QBittorrentClient,
) -> None:
    """Apply one qBittorrent action to a batch of torrents on one client.

    Grouping by client matters: an info hash is only meaningful to the
    qBittorrent instance that holds it, so callers resolve ownership first.
    """

    adapter = client_factory(client.url, client.username or "", client.password or "")
    try:
        if action == "start":
            await adapter.start_torrents(info_hashes)
        elif action == "stop":
            await adapter.stop_torrents(info_hashes)
        elif action == "recheck":
            await adapter.recheck_torrents(info_hashes)
        elif action == "reannounce":
            await adapter.reannounce_torrents(info_hashes)
        elif action.startswith("queue_"):
            await adapter.set_queue_position(info_hashes, action.removeprefix("queue_"))
        elif action == "download_limit":
            await adapter.set_download_limit(info_hashes, int(value or 0))
        elif action == "upload_limit":
            await adapter.set_upload_limit(info_hashes, int(value or 0))
        elif action == "share_ratio":
            # -2 hands the torrent back to qBittorrent's global limit.
            await adapter.set_share_limits(info_hashes, float(value if value is not None else -2))
        elif action == "remove":
            # Removal from qBittorrent only. Files are never deleted here: a
            # cross-seed member shares its payload with the rest of its group.
            for info_hash in info_hashes:
                await adapter.remove_torrent(info_hash, delete_files=False)
        else:
            raise QBittorrentError(f"Unsupported torrent action: {action}")
    finally:
        await adapter.close()


#: Failed polls in a row, per client. The first failure after a healthy poll
#: is retried within seconds; only a second one in a row marks the client
#: unavailable, so one dropped request or a qBittorrent restart does not flash
#: "unavailable" across the app.
_FAILURE_STREAK: dict[UUID, int] = {}
FAILURES_BEFORE_UNAVAILABLE = 2
RETRY_AFTER_FAILURE_SECONDS = 5
#: How long to leave a client that banned Medialogue's IP before trying again.
BAN_RETRY_SECONDS = 15 * 60


async def set_client_health(
    db: AsyncSession, client: ConfiguredDownloadClient, *, healthy: bool, error: str | None = None
) -> None:
    """Record a client's health and announce a change.

    The poller, Refresh health and Test all report through here, so a change
    reaches every open page live instead of waiting for a manual refresh.
    """

    previous = client.health
    client.health = "healthy" if healthy else "unavailable"
    client.last_error = None if healthy else error
    if healthy:
        client.last_success_at = utcnow()
        _FAILURE_STREAK.pop(client.id, None)
    if previous == client.health:
        return
    await create_event(
        db,
        "qbittorrent.health",
        entity_type="download_client",
        entity_id=client.id,
        message=f"qBittorrent client {client.name} is {client.health}.",
        severity=Severity.INFO if healthy else Severity.ERROR,
        details={"status": client.health, **({} if healthy else {"error": error or ""})},
    )


async def test_download_client_connection(
    client: ConfiguredDownloadClient,
    *,
    client_factory: QBittorrentClientFactory = QBittorrentClient,
) -> dict[str, object]:
    """Whether the client answers and its torrent list can be read.

    The poller reads the list, so a check that stopped at the version could
    report healthy while every poll failed.
    """

    started = perf_counter()
    adapter = client_factory(client.url, client.username or "", client.password or "")
    try:
        result = await adapter.health()
        if isinstance(adapter, QBittorrentClient):
            await adapter.list_torrents()
        return {
            "status": "healthy",
            "version": result.get("version"),
            "latency_ms": round((perf_counter() - started) * 1000),
        }
    finally:
        await adapter.close()


#: Per-client mirror of qBittorrent's incremental torrent feed.
_SYNC_STATES: dict[UUID, tuple[tuple[str, str, str], TorrentSyncState]] = {}

#: Per-client fingerprints of torrents whose last full processing found nothing
#: left to do. An unchanged torrent then only gets its live telemetry updated.
_SETTLED: dict[UUID, dict[str, tuple[tuple, datetime]]] = {}
#: Even an unchanged torrent is fully re-processed this often, so associations,
#: archive state and reconciliation still catch up with library changes.
_FULL_REPROCESS_SECONDS = 600


async def _fetch_observations(adapter, client: ConfiguredDownloadClient) -> list[TorrentObservation]:
    """The client's full torrent list, fetched incrementally when possible."""

    if not isinstance(adapter, QBittorrentClient):
        return await adapter.list_torrents()
    identity = (client.url, client.username or "", hashlib.sha256((client.password or "").encode()).hexdigest())
    entry = _SYNC_STATES.get(client.id)
    if entry is None or entry[0] != identity:
        entry = (identity, TorrentSyncState())
        _SYNC_STATES[client.id] = entry
        _SETTLED.pop(client.id, None)
    try:
        return await adapter.sync_torrents(entry[1])
    except Exception:
        entry[1].reset()
        raise


def _archive_files_present(torrent: Torrent | None) -> bool:
    """Cheap guard for the fast path: a deleted backup forces a full pass."""

    return bool(
        torrent is not None
        and torrent.archive_path
        and torrent.manifest_path
        and os.path.exists(torrent.archive_path)
        and os.path.exists(torrent.manifest_path)
    )


def _settled_fingerprint(observed, reported: str | None, resolved: str | None) -> tuple:
    """Everything whose change requires full processing of a torrent."""

    return (
        observed.name,
        observed.state,
        round(float(observed.progress or 0), 4),
        bool(observed.complete),
        bool(observed.checking),
        reported,
        resolved,
        observed.category or None,
        tuple(observed.tags),
        observed.tracker or None,
        observed.total_size,
        observed.completed_at,
    )


async def _poll_download_client(
    db: AsyncSession,
    client: ConfiguredDownloadClient,
    *,
    client_factory: QBittorrentClientFactory = QBittorrentClient,
) -> dict[str, object]:
    """Poll one qBit client and persist relevant observations only."""

    adapter = client_factory(client.url, client.username or "", client.password or "")
    poll_started = perf_counter()
    checked_at = utcnow()
    client.last_polled_at = checked_at
    client.last_health_checked_at = checked_at
    try:
        observations = await _fetch_observations(adapter, client)
    except Exception as exc:
        client.latency_ms = round((perf_counter() - poll_started) * 1000)
        streak = _FAILURE_STREAK.get(client.id, 0) + 1
        _FAILURE_STREAK[client.id] = streak
        if (
            client.health == "healthy"
            and streak < FAILURES_BEFORE_UNAVAILABLE
            and not isinstance(exc, QBittorrentAuthError)
        ):
            # Retry within seconds before calling it unavailable.
            client.last_error = str(exc)
            interval = max(5, client.poll_interval_seconds or 30)
            client.last_polled_at = checked_at - timedelta(seconds=max(0, interval - RETRY_AFTER_FAILURE_SECONDS))
        else:
            await set_client_health(db, client, healthy=False, error=str(exc))
        return {
            "client_id": client.id,
            "status": client.health or "unknown",
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
    client.latency_ms = round((perf_counter() - poll_started) * 1000)
    await set_client_health(db, client, healthy=True)
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
    # A mapping explicitly attached to a media folder must not remain active
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
    prior_torrents = (
        await db.scalars(select(Torrent).where(Torrent.id.in_({row.torrent_id for row in prior_rows})))
    ).all() if prior_rows else []
    prior_hash_by_torrent = {torrent.id: torrent.info_hash.lower() for torrent in prior_torrents}
    prior_torrent_by_id = {torrent.id: torrent for torrent in prior_torrents}
    prior_by_hash = {
        prior_hash_by_torrent[row.torrent_id]: row
        for row in prior_rows
        if row.torrent_id in prior_hash_by_torrent
    }

    def scope_of(observed) -> tuple[str, str | None, UUID | None, bool]:
        reported = observed.content_path or observed.save_path
        resolved, found_mapping_id = resolve_remote_path(reported, mappings)
        found_mapping = mapping_by_id.get(found_mapping_id) if found_mapping_id else None
        if found_mapping is not None and found_mapping.storage_root_id is not None:
            mapped_root = root_by_id.get(found_mapping.storage_root_id)
            inside = bool(mapped_root and _inside_local_root(resolved, mapped_root.resolved_root_path))
        else:
            inside = any(_inside_local_root(resolved, root.resolved_root_path) for root in roots)
        return reported, resolved, found_mapping_id, inside

    # Path scope is computed once per torrent and reused below. Tracker detail
    # is only fetched for torrents Medialogue manages: a client can hold
    # thousands of unrelated torrents that must not cost requests.
    scopes = {item.info_hash.lower(): scope_of(item) for item in observations}
    managed = [item for item in observations if scopes[item.info_hash.lower()][3]]
    active_hashes = {item.info_hash.lower() for item in managed if _tracker_is_active(item)}
    current_hashes: set[str] = set()
    settled_cache = _SETTLED.setdefault(client.id, {})
    fast_torrent_ids: list[UUID] = []
    detail_due = await _tracker_detail_batch(
        db,
        [item.info_hash for item in managed],
        checked_at,
        budget=_tracker_request_budget(
            len(active_hashes), len(managed) - len(active_hashes), client.poll_interval_seconds or 30
        ),
        active_hashes=active_hashes,
    )
    relevant_count = added = completed = ignored = removed = processing_errors = 0
    completed_upgrade_torrent_ids: set[UUID] = set()
    failed_downloads: list[tuple[TorrentClientObservation, Torrent, str]] = []
    detail_adapter = None
    # The lazily opened detail connection must close even when a later stage raises.
    try:
        for observed in observations:
            info_hash = observed.info_hash.lower()
            current_hashes.add(info_hash)
            reported_content, resolved_content, mapping_id, in_scope = scopes[info_hash]
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
            # Configured media folders are the hard boundary for reconciliation.
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
                            await cancel_incoming_torrent(db, torrent, emit_event=False)
                            await reconcile_torrent_disagreements(db, torrent, qbit_present=None)
                continue

            relevant_count += 1
            fingerprint = _settled_fingerprint(observed, reported_content, resolved_content)
            settled = settled_cache.get(info_hash)
            prior = prior_by_hash.get(info_hash)
            if (
                settled is not None
                and settled[0] == fingerprint
                and (checked_at - settled[1]).total_seconds() < _FULL_REPROCESS_SECONDS
                and prior is not None
                and prior.is_present
                and _archive_files_present(prior_torrent_by_id.get(prior.torrent_id))
                and info_hash not in detail_due
                and not observed.checking
                and str(observed.state or "").casefold() not in {"error", "missingfiles", "moving"}
            ):
                # Nothing that drives Medialogue's decisions changed since the last
                # full pass: refresh the live numbers and move on.
                prior.state = observed.state
                prior.progress = observed.progress
                prior.download_speed = observed.download_speed
                prior.upload_speed = observed.upload_speed
                prior.eta_seconds = observed.eta
                prior.amount_left = observed.amount_left
                prior.ratio = observed.ratio
                prior.seeds = observed.seeds
                prior.seeds_total = observed.seeds_total
                prior.leeches = observed.leeches
                prior.leeches_total = observed.leeches_total
                prior.availability = observed.availability
                track_other_seeders(prior, now=checked_at)
                prior.last_seen_at = checked_at
                fast_torrent_ids.append(prior.torrent_id)
                continue
            settled_cache.pop(info_hash, None)
            added_before = added
            queued_handoff: UUID | None = None
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
                            tracker_summary={"tracker": tracker_endpoint_display(observed.tracker)} if observed.tracker else {},
                            metadata_json={
                                "content_path": observed.content_path,
                                "mapping_id": str(mapping_id) if mapping_id else None,
                                "completed": observed.complete,
                                "checking": observed.checking,
                                **_portable_metadata(observed),
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
                            torrent.tracker_summary = {
                                **(torrent.tracker_summary or {}),
                                "tracker": tracker_endpoint_display(observed.tracker),
                            }
                        torrent.metadata_json = {
                            **(torrent.metadata_json or {}),
                            "content_path": observed.content_path,
                            "mapping_id": str(mapping_id) if mapping_id else None,
                            # A force recheck is transient and must not erase the
                            # durable fact that this torrent completed previously.
                            "completed": was_complete if observed.checking else observed.complete,
                            "checking": observed.checking,
                            **_portable_metadata(observed),
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

                    tracker_title = await _selected_tracker_title(
                        db,
                        info_hash=info_hash,
                        torrent_name=observed.name,
                    )
                    if tracker_title:
                        torrent.metadata_json = {
                            **(torrent.metadata_json or {}),
                            "tracker_title": tracker_title,
                        }

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
                    row.download_speed = observed.download_speed
                    row.upload_speed = observed.upload_speed
                    row.eta_seconds = observed.eta
                    row.amount_left = observed.amount_left
                    row.ratio = observed.ratio
                    row.seeds = observed.seeds
                    row.seeds_total = observed.seeds_total
                    row.leeches = observed.leeches
                    row.leeches_total = observed.leeches_total
                    row.availability = observed.availability
                    track_other_seeders(row, now=utcnow())
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
                        **_portable_metadata(observed),
                    }
                    from app.services.blocklist import clear_recovered_failure_marker, terminal_incomplete_failure
                    clear_recovered_failure_marker(torrent, row)
                    if terminal_incomplete_failure(row, torrent):
                        failed_downloads.append((row, torrent, info_hash))
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
                                # Pages that show speed and ETA update from the event
                                # instead of re-reading their lists.
                                "download_speed": observed.download_speed,
                                "eta_seconds": observed.eta,
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

                    if observed.checking:
                        continue

                    recovery_snapshot = None
                    tracker_snapshots: tuple[TorrentTrackerSnapshot, ...] = ()
                    tracker_details_collected = False
                    needs_archive = not torrent_archive_complete(torrent) or str(client.id) not in (read_manifest(torrent).get("client_recovery") or {})
                    if needs_archive or info_hash in detail_due:
                        try:
                            if detail_adapter is None:
                                detail_adapter = client_factory(
                                    client.url, client.username or "", client.password or ""
                                )
                            recovery_snapshot = await detail_adapter.get_torrent_recovery_snapshot(info_hash)
                            tracker_snapshots = _snapshot_trackers(recovery_snapshot)
                            tracker_details_collected = True
                            torrent.metadata_json = {
                                **(torrent.metadata_json or {}),
                                "tracker_checked_at": checked_at.isoformat(),
                                "tracker_check_error": None,
                            }
                        except Exception as exc:
                            # Detail collection must never turn a reachable qBit client
                            # into an integration outage or mutate torrent/media state.
                            torrent.metadata_json = {
                                **(torrent.metadata_json or {}),
                                "tracker_checked_at": checked_at.isoformat(),
                                "tracker_check_error": (
                                    redact_tracker_message(str(exc)) or "qBittorrent detail request failed"
                                )[:500],
                            }
                        if tracker_details_collected:
                            await persist_tracker_health(
                                db,
                                torrent,
                                client.id,
                                tracker_snapshots,
                                checked_at=checked_at,
                            )

                    # Persist an Incoming association while downloading. Completion is
                    # authoritative, but attachment additionally requires shared path,
                    # directory, filename, and TMDB/manual identity verification. Plex
                    # remains read-only presence/path evidence and never overrides identity.
                    association = await associate_incoming_torrent(
                        db,
                        torrent,
                        resolved_path=resolved_content,
                        scope=MediaType(client.scope.value),
                        complete=observed.complete,
                        download_client_id=client.id,
                    )
                    upgrade_execution = bool(association and association.get("upgrade_execution_id"))
                    if observed.complete and (client.completed_download_handling or upgrade_execution):
                        await finalize_completed_torrent(
                            db,
                            torrent,
                            resolved_path=resolved_content,
                            scope=MediaType(client.scope.value),
                        )

                    if not torrent_archive_complete(torrent):
                        await ensure_torrent_archived(
                            db,
                            torrent,
                            client,
                            client_factory=client_factory,
                            recovery_snapshot=recovery_snapshot,
                        )
                    else:
                        await refresh_torrent_manifest(db, torrent, recovery_snapshot=recovery_snapshot, download_client_id=client.id)

                    if observed.complete and upgrade_execution:
                        # Remove the old qBittorrent torrent only after the entire
                        # poll snapshot has been processed. Otherwise an old torrent
                        # that appeared later in this same qBittorrent snapshot could
                        # be marked present again after the replacement removed it.
                        completed_upgrade_torrent_ids.add(torrent.id)
                        queued_handoff = torrent.id

                    await reconcile_torrent_disagreements(db, torrent, qbit_present=True)
                    if (
                        torrent_archive_complete(torrent)
                        and str(client.id) in (read_manifest(torrent).get("client_recovery") or {})
                        and str(observed.state or "").casefold() not in {"error", "missingfiles", "moving"}
                    ):
                        settled_cache[info_hash] = (fingerprint, checked_at)
            except Exception as exc:
                settled_cache.pop(info_hash, None)
                # Python-side counters are not part of the database SAVEPOINT.
                # Restore them when the torrent's database work was rolled back.
                added = added_before
                completed = completed_before
                # This torrent's SAVEPOINT rolled back, so its queued failure
                # handling would act on state that no longer exists.
                failed_downloads[:] = [item for item in failed_downloads if item[2] != info_hash]
                # Same for the upgrade handoff: it would act on rolled-back state.
                if queued_handoff is not None:
                    completed_upgrade_torrent_ids.discard(queued_handoff)
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

        if failed_downloads:
            from app.services.blocklist import handle_failed_download

            failure_adapter = client_factory(client.url, client.username or "", client.password or "")
            try:
                for failed_observation, failed_torrent, _failed_hash in failed_downloads:
                    try:
                        async with db.begin_nested():
                            await handle_failed_download(
                                db,
                                torrent=failed_torrent,
                                observation=failed_observation,
                                client_name=client.name,
                                adapter=failure_adapter,
                            )
                            if not failed_observation.is_present:
                                await deactivate_client_tracker_health(db, failed_torrent.id, client.id)
                                await cancel_incoming_torrent(db, failed_torrent)
                                await reconcile_torrent_disagreements(db, failed_torrent, qbit_present=False)
                    except Exception:
                        processing_errors += 1
                        logger.exception(
                            "Failed-download handling failed",
                            extra={"torrent_id": str(failed_torrent.id), "client_id": str(client.id)},
                        )
            finally:
                await failure_adapter.close()

        if completed_upgrade_torrent_ids:
            from app.services.upgrade_execution import advance_upgrade_handoff

            for torrent_id in completed_upgrade_torrent_ids:
                try:
                    async with db.begin_nested():
                        upgrade_torrent = await db.get(Torrent, torrent_id)
                        if upgrade_torrent is not None:
                            await advance_upgrade_handoff(
                                db,
                                upgrade_torrent,
                                qbittorrent_client_factory=client_factory,
                            )
                except Exception:
                    processing_errors += 1
                    logger.exception(
                        "Upgrade replacement handoff failed after qBittorrent snapshot processing",
                        extra={
                            "entity_type": "download_client",
                            "entity_id": str(client.id),
                            "torrent_id": str(torrent_id),
                        },
                    )

        if fast_torrent_ids:
            # One statement for every torrent that took the fast path.
            for index in range(0, len(fast_torrent_ids), 500):
                await db.execute(
                    update(Torrent)
                    .where(Torrent.id.in_(fast_torrent_ids[index : index + 500]))
                    .values(last_seen_at=checked_at)
                )

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
                        await deactivate_client_tracker_health(db, torrent.id, client.id)
                        await cancel_incoming_torrent(db, torrent)
                        await reconcile_torrent_disagreements(db, torrent, qbit_present=False)
    finally:
        if detail_adapter is not None:
            await detail_adapter.close()

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
    client_factory: QBittorrentClientFactory = QBittorrentClient,
) -> dict[str, object]:
    """Poll one qBit instance with a per-client non-overlap lock."""

    async with _client_locks[client.id]:
        from app.services.recovery_checkpoint import archive_checkpoint
        async with archive_checkpoint:
            result = await _poll_download_client(db, client, client_factory=client_factory)
            await db.commit()
            from app.services.payload_groups import refresh_due, refresh_payload_groups

            if refresh_due(structure_changed=bool(result.get("added") or result.get("removed"))):
                try:
                    await refresh_payload_groups(db)
                    await db.commit()
                except Exception:
                    await db.rollback()
                    logger.exception("Payload group refresh failed", extra={"client_id": str(client.id)})
            from app.services.upgrade_cleanup import run_automatic_cleanups
            await run_automatic_cleanups(db)
            return result


async def poll_due_download_clients(
    db: AsyncSession,
    *,
    client_factory: QBittorrentClientFactory = QBittorrentClient,
) -> list[dict[str, object]]:
    """Poll enabled clients whose configured interval has elapsed."""

    now = utcnow()
    clients = [client for client in await list_configured_download_clients(db) if client.enabled]
    results: list[dict[str, object]] = []
    for client in clients:
        client_id = client.id
        last = client.last_polled_at
        interval = max(5, client.poll_interval_seconds or 30)
        # SQLite does not preserve tzinfo, so a stored timestamp comes back naive
        # while utcnow() is aware. Treat a naive value as UTC rather than letting
        # the subtraction raise and kill the whole polling cycle.
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if client.health == "unavailable" and client.last_error:
            # Rejected credentials wait for the user: retrying them only earns
            # an IP ban. A ban lifts by itself, so it is retried now and then.
            if (
                "rejected the configured username/password" in client.last_error
                or "rejected WebAPI authentication" in client.last_error
            ):
                continue
            if "temporarily banned Medialogue's IP" in client.last_error:
                interval = BAN_RETRY_SECONDS
        if last is not None and (now - last).total_seconds() < interval:
            continue
        try:
            result = await poll_download_client(db, client, client_factory=client_factory)
            await db.commit()
        except Exception as exc:
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
