import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import event, false, or_, select
from sqlalchemy.orm import Session
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
_PENDING_LIVE_EVENTS_KEY = "_medialogue_pending_live_events"
_PENDING_NESTED_LIVE_EVENTS_KEY = "_medialogue_pending_nested_live_events"


def _live_payload(
    event_type: str,
    *,
    entity_type: str,
    entity_id: UUID | str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event": event_type,
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }


def queue_live_event(
    db: AsyncSession,
    event_type: str,
    *,
    entity_type: str,
    entity_id: UUID | str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Broadcast a durable-state invalidation only after the transaction commits.

    A browser must never observe a Problem/Event transition that later rolls
    back. AsyncSession exposes its underlying synchronous Session, whose info
    mapping survives until SQLAlchemy's after_commit/after_rollback hooks run.
    """

    payload = _live_payload(event_type, entity_type=entity_type, entity_id=entity_id, data=data)
    nested = db.sync_session.get_nested_transaction()
    if nested is None:
        db.sync_session.info.setdefault(_PENDING_LIVE_EVENTS_KEY, []).append(payload)
        return
    groups = db.sync_session.info.setdefault(_PENDING_NESTED_LIVE_EVENTS_KEY, {})
    groups.setdefault(id(nested), []).append(payload)


@event.listens_for(Session, "after_commit")
def _publish_committed_live_events(session: Session) -> None:
    nested = session.get_nested_transaction()
    if nested is not None:
        groups = session.info.setdefault(_PENDING_NESTED_LIVE_EVENTS_KEY, {})
        payloads = groups.pop(id(nested), [])
        parent = nested.parent
        if parent is not None and parent.nested:
            groups.setdefault(id(parent), []).extend(payloads)
        else:
            session.info.setdefault(_PENDING_LIVE_EVENTS_KEY, []).extend(payloads)
        return
    for payload in session.info.pop(_PENDING_LIVE_EVENTS_KEY, []):
        _publish_payload(payload)
    session.info.pop(_PENDING_NESTED_LIVE_EVENTS_KEY, None)


@event.listens_for(Session, "after_rollback")
def _discard_rolled_back_live_events(session: Session) -> None:
    nested = session.get_nested_transaction()
    if nested is not None:
        groups = session.info.get(_PENDING_NESTED_LIVE_EVENTS_KEY, {})
        groups.pop(id(nested), None)
        return
    session.info.pop(_PENDING_LIVE_EVENTS_KEY, None)
    session.info.pop(_PENDING_NESTED_LIVE_EVENTS_KEY, None)


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
    queue_live_event(
        db,
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
    _publish_payload(_live_payload(event_type, entity_type=entity_type, entity_id=entity_id, data=data))


def _publish_payload(payload: dict[str, Any]) -> None:
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
