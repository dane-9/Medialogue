"""Budgeting qBittorrent tracker detail requests, and reading tracker state from snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.qbittorrent import TorrentTrackerSnapshot
from app.models.domain import Torrent


#: How stale a torrent's tracker health may get. Torrents still downloading,
#: errored, or without a working tracker are checked often because their state
#: drives decisions; settled seeding torrents change rarely and are checked far
#: less often, which keeps request volume low on clients with thousands of them.
_TRACKER_ACTIVE_STALENESS_SECONDS = 300
_TRACKER_SETTLED_STALENESS_SECONDS = 3600

#: Floor so a small, mostly-due client still clears its queue quickly.
_TRACKER_MIN_REQUESTS_PER_POLL = 10
#: Ceiling so one poll never turns into thousands of detail requests.
_TRACKER_MAX_REQUESTS_PER_POLL = 200


def _tracker_is_active(observed) -> bool:
    state = str(getattr(observed, "state", "") or "").casefold()
    return (
        float(getattr(observed, "progress", 0) or 0) < 1
        or "error" in state
        or "missing" in state
        or not getattr(observed, "tracker", None)
    )


def _tracker_request_budget(active_count: int, settled_count: int, poll_interval_seconds: int) -> int:
    """How many tracker detail calls this poll may make.

    Each class is spread evenly over its own staleness window, so the request
    rate stays flat and every torrent is still revisited within its window.
    """

    total = active_count + settled_count
    if total <= 0:
        return 0
    interval = max(1, poll_interval_seconds)
    active_polls = max(1, _TRACKER_ACTIVE_STALENESS_SECONDS // interval)
    settled_polls = max(1, _TRACKER_SETTLED_STALENESS_SECONDS // interval)
    per_poll = -(-active_count // active_polls) + -(-settled_count // settled_polls)
    return min(total, _TRACKER_MAX_REQUESTS_PER_POLL, max(_TRACKER_MIN_REQUESTS_PER_POLL, per_poll))


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
    active_hashes: set[str] | None = None,
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
    active = {value.lower() for value in active_hashes} if active_hashes is not None else set(lowered)

    def window(value: str) -> int:
        return _TRACKER_ACTIVE_STALENESS_SECONDS if value in active else _TRACKER_SETTLED_STALENESS_SECONDS

    # Only torrents past their window are due; the rest are left alone rather
    # than refreshed just because budget remains. Never-checked ones lead.
    due = [value for value in lowered if _tracker_check_age(known.get(value), now) >= window(value)]
    ordered = sorted(due, key=lambda value: -_tracker_check_age(known.get(value), now) / window(value))
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
