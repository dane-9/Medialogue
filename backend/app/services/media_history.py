"""Finished upgrades and replacements, for the History view on both pages.

A finished upgrade's row is cleared on the next upgrade search, so the history
reads the completion events instead. Each completion event carries a snapshot
of what was replaced (``details["history"]``) taken when it finished.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Event, Movie, Season, Show, UpgradeCandidate

#: The two ways an upgrade or replacement finishes.
FINISHED_EVENTS = ("upgrade.cleanup_completed", "replacement.finished")


async def history_snapshot(db: AsyncSession, row: UpgradeCandidate) -> dict[str, Any]:
    """What a finished upgrade or replacement replaced, frozen for its event."""

    title, year, season_number = None, None, None
    if row.movie_id:
        movie = await db.get(Movie, row.movie_id)
        if movie is not None:
            title, year = movie.title, movie.year
    elif row.season_id:
        season = await db.get(Season, row.season_id)
        show = await db.get(Show, season.show_id) if season is not None else None
        if show is not None:
            title, year = show.title, show.year
        if season is not None:
            season_number = season.season_number
    return {
        "replacement": row.target_key.startswith("replace:"),
        "media_type": row.media_type.value,
        "title": title,
        "year": year,
        "season_number": season_number,
        "old_release": row.current_release_name,
        "old_quality": row.current_quality,
        "new_release": row.title,
        "new_quality": row.quality,
        "old_torrents_removed": row.old_torrents_removed_at is not None,
    }


def _outcome(event_type: str, snapshot: dict[str, Any]) -> str:
    if snapshot.get("linked"):
        return "Linked to existing files"
    if event_type == "upgrade.cleanup_completed":
        return "Old files deleted" if snapshot.get("replacement") else "Old release removed"
    if snapshot.get("old_torrents_removed"):
        return "Old torrents retired, files kept"
    return "Kept everything"


async def finished_history(db: AsyncSession, *, replacements: bool, limit: int = 100) -> list[dict[str, Any]]:
    """Finished replacements (or upgrades), newest first."""

    events = (await db.scalars(
        select(Event).where(Event.event_type.in_(FINISHED_EVENTS)).order_by(Event.created_at.desc()).limit(limit * 3)
    )).all()
    items: list[dict[str, Any]] = []
    for event in events:
        details = event.details or {}
        snapshot = details.get("history")
        if not isinstance(snapshot, dict):
            continue
        if bool(snapshot.get("replacement")) != replacements:
            continue
        items.append({
            "id": str(event.id),
            # Stored without a zone; it is UTC, said so the browser reads it right.
            "finished_at": event.created_at if event.created_at.tzinfo else event.created_at.replace(tzinfo=timezone.utc),
            "media_type": snapshot.get("media_type"),
            "title": snapshot.get("title") or snapshot.get("old_release") or "Unknown title",
            "year": snapshot.get("year"),
            "season_number": snapshot.get("season_number"),
            "old_release": snapshot.get("old_release"),
            "old_quality": snapshot.get("old_quality"),
            "new_release": snapshot.get("new_release"),
            "new_quality": snapshot.get("new_quality"),
            "outcome": _outcome(event.event_type, snapshot),
        })
        if len(items) >= limit:
            break
    return items

