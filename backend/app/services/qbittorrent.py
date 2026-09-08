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
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.qbittorrent import QBittorrentClient, QBittorrentError, TorrentTrackerSnapshot
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
from app.services.torrent_archive import ensure_torrent_archived, refresh_torrent_manifest, torrent_archive_complete, read_manifest
from app.services.torrent_tracking import (
    deactivate_client_tracker_health,
    persist_tracker_health,
    redact_tracker_message,
    tracker_endpoint_display,
)
from app.services.reconciliation import (
    associate_incoming_torrent,
    cancel_incoming_torrent,
    finalize_completed_torrent,
    reconcile_torrent_disagreements,
    resolve_problem,
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


#: How stale a torrent's tracker health may get. The per-poll batch is sized to
#: hold this regardless of library size, so the guarantee does not weaken as a
#: client grows; only the number of requests per cycle does.
_TRACKER_MAX_STALENESS_SECONDS = 300

#: Floor for the per-poll batch, so a small client refreshes everything every
#: cycle instead of trickling a couple of torrents at a time.
_TRACKER_MIN_REQUESTS_PER_POLL = 50


def _tracker_request_budget(torrent_count: int, poll_interval_seconds: int) -> int:
    """How many tracker detail calls this poll may make.

    Sized so every torrent is revisited within _TRACKER_MAX_STALENESS_SECONDS:
    spreading that work evenly across the polls in the window keeps the request
    rate flat without letting any torrent go unchecked for longer than a fixed
    budget would have allowed.
    """

    if torrent_count <= 0:
        return 0
    polls_in_window = max(1, _TRACKER_MAX_STALENESS_SECONDS // max(1, poll_interval_seconds))
    per_poll = -(-torrent_count // polls_in_window)  # ceil
    return max(_TRACKER_MIN_REQUESTS_PER_POLL, per_poll)


def _tracker_check_age(metadata: dict[str, object] | None, now: datetime) -> float:
    """Seconds since this torrent's tracker detail was last fetched.

    A torrent that has never been checked sorts first.
    """

    raw = (metadata or {}).get("tracker_checked_at")
    if not isinstance(raw, str):
        return float("inf")
    try:
        previous = datetime.fromisoformat(raw)
    except ValueError:
        return float("inf")
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    return (now - previous).total_seconds()


async def _tracker_detail_batch(
    db: AsyncSession,
    info_hashes: Sequence[str],
    now: datetime,
    *,
    budget: int,
) -> set[str]:
    """Which torrents get their tracker detail refreshed this cycle.

    Staleness decides the order and a fixed budget decides how many, rather
    than each torrent carrying its own deadline. Deadlines bunch up: every
    torrent seen in the same first poll shares a check time and so falls due in
    the same later poll, producing one burst of N requests instead of a steady
    trickle. Spending a constant budget on the oldest entries keeps the request
    rate flat and still covers the whole client in ceil(N / budget) polls.
    """

    if not info_hashes or budget <= 0:
        return set()
    lowered = [value.lower() for value in info_hashes]
    rows: list[tuple[str, dict | None]] = []
    # Chunked so a very large client cannot exceed SQLite's variable limit.
    for index in range(0, len(lowered), 500):
        chunk = lowered[index : index + 500]
        rows.extend(
            (row[0], row[1])
            for row in (
                await db.execute(
                    select(Torrent.info_hash, Torrent.metadata_json).where(Torrent.info_hash.in_(chunk))
                )
            ).all()
        )
    known = {info_hash: metadata for info_hash, metadata in rows}
    # A torrent with no row yet has never been checked, so it leads the queue.
    ordered = sorted(lowered, key=lambda value: -_tracker_check_age(known.get(value), now))
    return set(ordered[:budget])


def _portable_metadata(observed) -> dict[str, object]:
    values = {
        "added_at": observed.added_at,
        "amount_left": observed.amount_left,
        "auto_tmm": observed.auto_tmm,
        "download_limit": observed.download_limit,
        "upload_limit": observed.upload_limit,
        "downloaded": observed.downloaded,
        "uploaded": observed.uploaded,
        "force_start": observed.force_start,
        "first_last_piece_priority": observed.first_last_piece_priority,
        "sequential_download": observed.sequential_download,
        "super_seeding": observed.super_seeding,
        "priority": observed.priority,
        "ratio": observed.ratio,
        "ratio_limit": observed.ratio_limit,
        "seeding_time": observed.seeding_time,
        "seeding_time_limit": observed.seeding_time_limit,
        "inactive_seeding_time_limit": observed.inactive_seeding_time_limit,
        "seeds": observed.seeds,
        "seeds_total": observed.seeds_total,
        "leeches": observed.leeches,
        "leeches_total": observed.leeches_total,
        "download_speed": observed.download_speed,
        "upload_speed": observed.upload_speed,
        "eta": observed.eta,
        "availability": observed.availability,
    }
    return {key: value for key, value in values.items() if value is not None}


def _snapshot_trackers(snapshot: dict[str, object] | None) -> tuple[TorrentTrackerSnapshot, ...]:
    if not snapshot or not isinstance(snapshot.get("trackers"), list):
        return ()
    result: list[TorrentTrackerSnapshot] = []
    for raw in snapshot["trackers"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("url"), str):
            continue
        result.append(
            TorrentTrackerSnapshot(
                url=raw["url"],
                status=int(raw.get("status") or 0),
                tier=int(raw.get("tier") or 0),
                peers=raw.get("peers") if isinstance(raw.get("peers"), int) else None,
                seeds=raw.get("seeds") if isinstance(raw.get("seeds"), int) else None,
                leeches=raw.get("leeches") if isinstance(raw.get("leeches"), int) else None,
                downloaded=raw.get("downloaded") if isinstance(raw.get("downloaded"), int) else None,
                message=raw.get("message") if isinstance(raw.get("message"), str) else None,
            )
        )
    return tuple(result)


def _comparison_path(value: str | None) -> str:
    """Normalize path separators for prefix comparisons without touching FS."""

    return _normalized_path(value).casefold()


def _normalized_path(value: str | None) -> str:
    """Normalize separators while preserving the path's original casing.

    qBittorrent paths are compared case-insensitively because the remote
    client may be Windows, but the mapped local path can be a case-sensitive
    Linux filesystem.  Never derive the local suffix from a case-folded path.
    """

    if not value:
        return ""
    normalized = value.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized


def _inside_local_root(path: str | None, root: str) -> bool:
    """Check a resolved *local* path against a configured media folder.

    Remote qBittorrent prefixes are matched case-insensitively, but after
    mapping Medialogue is operating in a Linux container.  The local mount
    namespace is case-sensitive.  Treating ``/Movies/...`` as if it were under
    the local root ``/movies`` lets an unmatched remote path masquerade as a
    valid local path and creates false TORRENT_PATH_NOT_FOUND Problems.
    """

    if not path or not root:
        return False
    try:
        # This is a path in the container's mount namespace, even when the
        # check happens on a Windows development host.  PurePosixPath keeps
        # Linux's case-sensitive component comparison without touching the
        # host filesystem.
        PurePosixPath(_normalized_path(path)).relative_to(PurePosixPath(_normalized_path(root)))
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
    reported_clean = _normalized_path(reported_path)
    reported_normalized = reported_clean.casefold()
    candidates = sorted(
        mappings,
        key=lambda mapping: len(_comparison_path(mapping.remote_prefix)),
        reverse=True,
    )
    for mapping in candidates:
        remote_clean = _normalized_path(mapping.remote_prefix)
        remote_prefix = remote_clean.casefold()
        if reported_normalized == remote_prefix or reported_normalized.startswith(remote_prefix + "/"):
            suffix = reported_clean[len(remote_clean) :].lstrip("/")
            return _join_prefix(mapping.local_prefix, suffix), mapping.id
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
        row.last_seen_at = utcnow()
        torrent.metadata_json = {
            **(torrent.metadata_json or {}),
            "progress": float(observed.progress),
            "checking": observed.checking,
        }
        updated += 1
    await db.commit()
    return updated


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
        else:
            raise QBittorrentError(f"Unsupported torrent action: {action}")
    finally:
        await adapter.close()


async def test_download_client_connection(
    client: ConfiguredDownloadClient,
    *,
    client_factory: QBittorrentClientFactory = QBittorrentClient,
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
    client_factory: QBittorrentClientFactory = QBittorrentClient,
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
    prior_by_hash = {
        prior_hash_by_torrent[row.torrent_id]: row
        for row in prior_rows
        if row.torrent_id in prior_hash_by_torrent
    }

    current_hashes: set[str] = set()
    detail_due = await _tracker_detail_batch(
        db,
        [item.info_hash for item in observations],
        checked_at,
        budget=_tracker_request_budget(len(observations), client.poll_interval_seconds or 30),
    )
    relevant_count = added = completed = ignored = removed = processing_errors = 0
    completed_upgrade_torrent_ids: set[UUID] = set()
    detail_adapter = None
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
        if client.health == "unavailable" and client.last_error and (
            "rejected the configured username/password" in client.last_error
            or "temporarily banned Medialogue's IP" in client.last_error
            or "rejected WebAPI authentication" in client.last_error
        ):
            continue
        # SQLite does not preserve tzinfo, so a stored timestamp comes back naive
        # while utcnow() is aware. Treat a naive value as UTC rather than letting
        # the subtraction raise and kill the whole polling cycle.
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
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
