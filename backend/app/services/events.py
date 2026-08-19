import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    Episode,
    Event,
    MediaDirectory,
    MediaFile,
    MovieRelease,
    MovieReleaseTorrent,
    Season,
    Severity,
    ShowRelease,
    ShowReleaseTorrent,
)


_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()


async def create_event(
    db: AsyncSession,
    event_type: str,
    *,
    entity_type: str,
    entity_id: UUID | None = None,
    message: str,
    severity: Severity = Severity.INFO,
    details: dict[str, Any] | None = None,
) -> Event:
    """Persist a meaningful state transition and broadcast it live.

    High-frequency telemetry (download/scan progress) should call
    publish_live_event directly so Event History stays useful indefinitely.
    """

    event = Event(
        event_type=event_type,
        severity=severity,
        entity_type=entity_type,
        entity_id=entity_id,
        message=message,
        details=details or {},
    )
    db.add(event)
    await db.flush()
    publish_live_event(
        event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        data={"event_id": str(event.id), "message": message, "severity": severity.value, **(details or {})},
    )
    return event


def publish_live_event(
    event_type: str,
    *,
    entity_type: str,
    entity_id: UUID | str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Publish transient SSE state without adding Event-history noise."""

    payload = {
        "event": event_type,
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }
    for queue in list(_subscribers):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # A stale browser should not be allowed to grow memory without
            # bound. It can reconnect and recover all durable state via REST.
            unsubscribe(queue)


def subscribe() -> asyncio.Queue[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue[dict[str, Any]]) -> None:
    _subscribers.discard(queue)


def scope_predicate(scope: dict[str, set[UUID]]):
    clauses = [
        (Event.entity_type == entity_type) & Event.entity_id.in_(entity_ids)
        for entity_type, entity_ids in scope.items()
        if entity_ids
    ]
    return or_(*clauses) if clauses else false()


async def movie_event_scope(db: AsyncSession, movie_id: UUID) -> dict[str, set[UUID]]:
    release_ids = set(await db.scalars(select(MovieRelease.id).where(MovieRelease.movie_id == movie_id)))
    directory_ids = set(
        await db.scalars(
            select(MediaDirectory.id)
            .join(MovieRelease, MovieRelease.id == MediaDirectory.movie_release_id)
            .where(MovieRelease.movie_id == movie_id)
        )
    )
    file_ids = set(
        await db.scalars(
            select(MediaFile.id)
            .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
            .join(MovieRelease, MovieRelease.id == MediaDirectory.movie_release_id)
            .where(MovieRelease.movie_id == movie_id)
        )
    )
    torrent_ids = set(
        await db.scalars(
            select(MovieReleaseTorrent.torrent_id)
            .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
            .where(MovieRelease.movie_id == movie_id)
        )
    )
    return {
        "movie": {movie_id},
        "movie_release": release_ids,
        "media_directory": directory_ids,
        "media_file": file_ids,
        "torrent": torrent_ids,
    }


async def show_event_scope(db: AsyncSession, show_id: UUID) -> dict[str, set[UUID]]:
    season_ids = set(await db.scalars(select(Season.id).where(Season.show_id == show_id)))
    episode_ids = set(await db.scalars(select(Episode.id).where(Episode.show_id == show_id)))
    release_ids = set(await db.scalars(select(ShowRelease.id).where(ShowRelease.show_id == show_id)))
    directory_ids = set(
        await db.scalars(
            select(MediaDirectory.id)
            .join(ShowRelease, ShowRelease.id == MediaDirectory.show_release_id)
            .where(ShowRelease.show_id == show_id)
        )
    )
    file_ids = set(
        await db.scalars(
            select(MediaFile.id)
            .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
            .join(ShowRelease, ShowRelease.id == MediaDirectory.show_release_id)
            .where(ShowRelease.show_id == show_id)
        )
    )
    torrent_ids = set(
        await db.scalars(
            select(ShowReleaseTorrent.torrent_id)
            .join(ShowRelease, ShowRelease.id == ShowReleaseTorrent.show_release_id)
            .where(ShowRelease.show_id == show_id)
        )
    )
    return {
        "show": {show_id},
        "season": season_ids,
        "episode": episode_ids,
        "show_release": release_ids,
        "media_directory": directory_ids,
        "media_file": file_ids,
        "torrent": torrent_ids,
    }
