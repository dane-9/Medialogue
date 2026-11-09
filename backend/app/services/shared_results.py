"""Expensive lists, kept ready on the server and shared between every client.

Building a library list reads every title, and the Replace Media queue scores
every affected title against every Custom Format. Pages, sidebar counts and live
refreshes all ask for these, so one copy of each is kept and rebuilt when it
goes out of date:

- A change made through the API (see main.py) makes every copy unusable: the
  next read waits for a fresh one, so what someone just did is always visible.
- A recorded event, or a copy passing its age limit, only makes it out of date:
  reads keep getting it while it is rebuilt in the background, and open pages
  are told by a ``results.<topic>`` live event when the new copy is different.

A rebuild is mostly Python running on the event loop, holding up other requests
meanwhile, so they run one at a time and each list is rebuilt at most once per
``_ACTIVE_GAP_SECONDS``, or ``_IDLE_GAP_SECONDS`` if nobody has read it lately.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session

logger = logging.getLogger(__name__)

Build = Callable[[AsyncSession], Awaitable[Any]]

#: A list read this recently has someone waiting on it.
_ACTIVE_READ_SECONDS = 300.0
_ACTIVE_GAP_SECONDS = 15.0
_IDLE_GAP_SECONDS = 120.0
#: A list nobody has read for this long is dropped, unless it is pinned.
_FORGET_AFTER_SECONDS = 1800.0
_TICK_SECONDS = 1.0

_MISSING: Any = object()


@dataclass(slots=True)
class _Entry:
    build: Build
    topic: str
    pinned: bool = False
    value: Any = _MISSING
    change_generation: int = -1
    event_generation: int = -1
    built_at: float = -math.inf
    attempted_at: float = -math.inf
    read_at: float = -math.inf


_entries: dict[tuple, _Entry] = {}
_locks: dict[tuple[int, tuple], asyncio.Lock] = {}
_pins: dict[tuple, Build] = {}
_change_generation = 0
_event_generation = 0
_refresher_running = False
#: True while building a list: a list built from another must use a current one.
_building: ContextVar[bool] = ContextVar("shared_results_building", default=False)


def invalidate_shared_results() -> None:
    """Something changed through the API: no copy may be served until rebuilt."""

    global _change_generation
    _change_generation += 1


def mark_shared_results_stale() -> None:
    """Something changed in the background: copies may be served while rebuilt."""

    global _event_generation
    _event_generation += 1


def pin_shared_result(key: tuple, build: Build) -> None:
    """Keep this list ready from startup, whether or not anyone has asked yet."""

    _pins[key] = build


def _ttl() -> float:
    from app.core.config import get_settings

    return get_settings().replacement_results_seconds


def _full_key(key: tuple) -> tuple:
    from app.core.config import get_settings

    return (get_settings().database_url, *key)


def _lock(full_key: tuple) -> asyncio.Lock:
    return _locks.setdefault((id(asyncio.get_running_loop()), full_key), asyncio.Lock())


def _usable(entry: _Entry | None) -> bool:
    return entry is not None and entry.value is not _MISSING and entry.change_generation == _change_generation


def _current(entry: _Entry | None, ttl: float, now: float) -> bool:
    return (
        entry is not None and _usable(entry) and entry.event_generation == _event_generation and now - entry.built_at < ttl
    )


def _servable(entry: _Entry | None, ttl: float) -> bool:
    if _current(entry, ttl, time.monotonic()):
        return True
    return _usable(entry) and _refresher_running and not _building.get()


async def _build_and_store(entry: _Entry, db: AsyncSession) -> Any:
    changes, events = _change_generation, _event_generation
    token = _building.set(True)
    try:
        value = await entry.build(db)
    finally:
        _building.reset(token)
    if changes == _change_generation:
        entry.value = value
        entry.change_generation = changes
        entry.event_generation = events
        entry.built_at = time.monotonic()
    return value


async def shared_result(key: tuple, db: AsyncSession, build: Build) -> Any:
    """The list for ``key``, ready-made if possible. Callers must not change it.

    ``build`` receives the session to read with: it outlives this request when
    the list is rebuilt in the background, so it must not hold on to ``db``.
    """

    ttl = _ttl()
    if ttl <= 0:
        return await build(db)
    full_key = _full_key(key)
    entry = _entries.get(full_key)
    if entry is not None:
        entry.read_at = time.monotonic()
    if _servable(entry, ttl):
        return entry.value
    # One build at a time per list: a request arriving meanwhile waits for it
    # and gets the same answer instead of starting its own.
    async with _lock(full_key):
        entry = _entries.get(full_key)
        if _servable(entry, ttl):
            return entry.value
        target = entry or _Entry(build=build, topic=str(key[0]), pinned=key in _pins)
        target.attempted_at = target.read_at = time.monotonic()
        value = await _build_and_store(target, db)
        # Only once it has built: a request that fails is not worth rebuilding.
        _entries.setdefault(full_key, target)
        return value


def _due(entry: _Entry, ttl: float, now: float) -> bool:
    if _current(entry, ttl, now):
        return False
    changed = entry.change_generation != _change_generation or entry.event_generation != _event_generation
    # Only its age is against it: rebuilding is pointless until someone reads it.
    if entry.value is not _MISSING and not changed and entry.read_at <= entry.built_at:
        return False
    gap = _ACTIVE_GAP_SECONDS if now - entry.read_at < _ACTIVE_READ_SECONDS else _IDLE_GAP_SECONDS
    return now - entry.attempted_at >= gap


async def _rebuild(full_key: tuple, entry: _Entry, ttl: float) -> None:
    async with _lock(full_key):
        now = time.monotonic()
        # A request may have built it while this waited for the lock.
        if _current(entry, ttl, now):
            return
        previous = entry.value
        entry.attempted_at = now
        try:
            async with db_session.async_session_factory() as db:
                value = await _build_and_store(entry, db)
        except Exception:
            logger.warning("could not rebuild the shared %s list; keeping the last one", entry.topic, exc_info=True)
            return
    if entry.value is value and previous is not _MISSING and value != previous:
        from app.services.events import publish_live_event

        publish_live_event(f"results.{entry.topic}", entity_type="results")


async def refresh_due_results() -> bool:
    """Rebuild the list most in need of it, if any. True if one was rebuilt."""

    ttl = _ttl()
    now = time.monotonic()
    for full_key, entry in list(_entries.items()):
        if not entry.pinned and now - entry.read_at > _FORGET_AFTER_SECONDS:
            del _entries[full_key]
    due = [(full_key, entry) for full_key, entry in _entries.items() if _due(entry, ttl, now)]
    if not due:
        return False
    full_key, entry = min(due, key=lambda item: (now - item[1].read_at >= _ACTIVE_READ_SECONDS, item[1].attempted_at))
    await _rebuild(full_key, entry, ttl)
    return True


def _add_pinned_entries() -> None:
    for key, build in _pins.items():
        _entries.setdefault(_full_key(key), _Entry(build=build, topic=str(key[0]), pinned=True))


async def run_shared_results_refresher(stop_event: asyncio.Event) -> None:
    """Keep the shared lists ready until ``stop_event``."""

    global _refresher_running
    if _ttl() <= 0:
        return
    _add_pinned_entries()
    _refresher_running = True
    try:
        while not stop_event.is_set():
            try:
                await refresh_due_results()
            except Exception:
                logger.warning("shared results refresh failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_TICK_SECONDS)
            except TimeoutError:
                pass
    finally:
        _refresher_running = False
