"""Durable tracker health without persisting tracker passkeys in the database."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.qbittorrent import TorrentTrackerSnapshot
from app.models.domain import Severity, Torrent, TorrentTrackerObservation
from app.services.events import create_event


TERMINAL_CONFIRMATIONS = 2
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_PASSKEY_RE = re.compile(r"(?i)(passkey|auth|token|key)=([^&\s]+)")
_TERMINAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("unregistered", re.compile(r"\bunregistered\s+torrent\b", re.IGNORECASE)),
    ("deleted", re.compile(r"\btorrent\s+(?:has\s+been|was|is)\s+deleted\b", re.IGNORECASE)),
    ("not_registered", re.compile(r"\btorrent\s+(?:is\s+)?not\s+registered\b", re.IGNORECASE)),
    ("not_found", re.compile(r"\b(?:unknown\s+torrent|torrent\s+not\s+found)\b", re.IGNORECASE)),
)


def tracker_endpoint_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8", errors="replace")).hexdigest()


def tracker_endpoint_display(url: str) -> str:
    """Return a useful endpoint label without path/query passkeys."""

    value = url.strip()
    if value.startswith("**"):
        return value
    try:
        parsed = urlsplit(value)
        if parsed.hostname:
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme or 'tracker'}://{parsed.hostname}{port}"
    except ValueError:
        pass
    return "Tracker endpoint"


def redact_tracker_message(message: str | None) -> str | None:
    if not message:
        return None
    redacted = _URL_RE.sub(lambda match: tracker_endpoint_display(match.group(0)), message.strip())
    return _PASSKEY_RE.sub(lambda match: f"{match.group(1)}=[redacted]", redacted)[:1000]


def terminal_tracker_reason(message: str | None) -> str | None:
    if not message:
        return None
    for reason, pattern in _TERMINAL_PATTERNS:
        if pattern.search(message):
            return reason
    return None


def tracker_health_state(status: int) -> str:
    return {0: "disabled", 1: "not_contacted", 2: "working", 3: "updating", 4: "error"}.get(status, "unknown")


def _aggregate(rows: list[TorrentTrackerObservation], checked_at: datetime) -> dict[str, object]:
    considered = [row for row in rows if row.active and row.health_state != "disabled"]
    working = [row for row in considered if row.health_state == "working"]
    failed = [row for row in considered if row.health_state == "error"]
    terminal = [row for row in failed if row.terminal_confirmed]
    if working:
        state = "degraded" if failed else "healthy"
    elif failed and len(failed) == len(considered) and len(terminal) == len(failed):
        state = "unregistered"
    elif failed:
        state = "unavailable"
    else:
        state = "unknown"
    last_success = max((row.last_success_at for row in considered if row.last_success_at), default=None)
    return {
        "state": state,
        "checked_at": checked_at.isoformat(),
        "last_success_at": last_success.isoformat() if last_success else None,
        "total": len(considered),
        "working": len(working),
        "failing": len(failed),
        "terminal": len(terminal),
    }


async def persist_tracker_health(
    db: AsyncSession,
    torrent: Torrent,
    download_client_id: UUID,
    trackers: tuple[TorrentTrackerSnapshot, ...],
    *,
    checked_at: datetime,
) -> dict[str, object]:
    """Upsert current endpoints and classify only repeated terminal failures."""

    real_trackers = tuple(item for item in trackers if not item.url.strip().startswith("**"))
    existing = (
        await db.scalars(
            select(TorrentTrackerObservation).where(
                TorrentTrackerObservation.torrent_id == torrent.id,
                TorrentTrackerObservation.download_client_id == download_client_id,
            )
        )
    ).all()
    by_key = {item.endpoint_key: item for item in existing}
    seen: set[str] = set()
    for tracker in real_trackers:
        key = tracker_endpoint_key(tracker.url)
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            row = TorrentTrackerObservation(
                torrent_id=torrent.id,
                download_client_id=download_client_id,
                endpoint_key=key,
                endpoint_display=tracker_endpoint_display(tracker.url),
                first_seen_at=checked_at,
                last_checked_at=checked_at,
            )
            db.add(row)
        state = tracker_health_state(tracker.status)
        message = redact_tracker_message(tracker.message)
        message_key = hashlib.sha256((message or "").casefold().encode()).hexdigest() if message else None
        reason = terminal_tracker_reason(message)
        row.endpoint_display = tracker_endpoint_display(tracker.url)
        row.tier = tracker.tier
        row.health_state = state
        row.message = message
        row.peers = tracker.peers
        row.seeds = tracker.seeds
        row.leeches = tracker.leeches
        row.downloaded = tracker.downloaded
        row.active = True
        row.last_checked_at = checked_at
        if state == "working":
            row.last_success_at = checked_at
            row.consecutive_failures = 0
            row.terminal_failure_count = 0
            row.terminal_reason = None
            row.terminal_confirmed = False
        elif state == "error":
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            if reason:
                row.terminal_failure_count = (
                    (row.terminal_failure_count or 0) + 1 if row.message_key == message_key else 1
                )
                row.terminal_reason = reason
                row.terminal_confirmed = row.terminal_failure_count >= TERMINAL_CONFIRMATIONS
            else:
                row.terminal_failure_count = 0
                row.terminal_reason = None
                row.terminal_confirmed = False
        row.message_key = message_key

    for row in existing:
        if row.endpoint_key not in seen:
            row.active = False

    await db.flush()
    all_rows = (
        await db.scalars(
            select(TorrentTrackerObservation).where(
                TorrentTrackerObservation.torrent_id == torrent.id,
                TorrentTrackerObservation.active.is_(True),
            )
        )
    ).all()
    previous_state = str((torrent.tracker_summary or {}).get("state") or "unknown")
    summary = _aggregate(list(all_rows), checked_at)
    torrent.tracker_summary = {**dict(torrent.tracker_summary or {}), **summary}
    next_state = str(summary["state"])
    if next_state == "unregistered" and previous_state != "unregistered":
        await create_event(
            db,
            "torrent.tracker_unregistered",
            entity_type="torrent",
            entity_id=torrent.id,
            message=f"Every usable tracker reports that {torrent.name} is no longer registered.",
            severity=Severity.WARNING,
            details={"info_hash": torrent.info_hash, "tracker_state": next_state},
        )
    elif previous_state == "unregistered" and next_state in {"healthy", "degraded"}:
        await create_event(
            db,
            "torrent.tracker_recovered",
            entity_type="torrent",
            entity_id=torrent.id,
            message=f"A tracker for {torrent.name} is responding again.",
            details={"info_hash": torrent.info_hash, "tracker_state": next_state},
        )
    return summary


async def deactivate_client_tracker_health(
    db: AsyncSession,
    torrent_id: UUID,
    download_client_id: UUID,
) -> None:
    rows = (
        await db.scalars(
            select(TorrentTrackerObservation).where(
                TorrentTrackerObservation.torrent_id == torrent_id,
                TorrentTrackerObservation.download_client_id == download_client_id,
            )
        )
    ).all()
    for row in rows:
        row.active = False
