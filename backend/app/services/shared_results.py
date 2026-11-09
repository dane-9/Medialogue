"""Expensive read results, shared between requests for a short while.

Building the Replace Media queue scores every affected title against every
Custom Format, and building a library list reads every title. Pages, sidebar
counts and live refreshes all ask for these, often within the same few seconds.
Any change made through the API clears them (see main.py), as does any recorded
event (see services/events.py); anything else shows up when they expire.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

_results: dict[tuple, tuple[float, int, Any]] = {}
_generation = 0
_locks: dict[tuple[int, tuple], asyncio.Lock] = {}


def invalidate_shared_results() -> None:
    global _generation
    _generation += 1
    _results.clear()


async def shared_result(key: tuple, compute):
    """``compute()``'s result, or a recent one. Callers must not change it."""

    from app.core.config import get_settings

    ttl = get_settings().replacement_results_seconds
    if ttl <= 0:
        return await compute()
    key = (get_settings().database_url, *key)
    lock = _locks.setdefault((id(asyncio.get_running_loop()), key), asyncio.Lock())
    # One computation at a time per result: a request arriving meanwhile waits
    # for it and gets the same answer instead of starting its own.
    async with lock:
        hit = _results.get(key)
        if hit is not None and hit[1] == _generation and time.monotonic() - hit[0] < ttl:
            return hit[2]
        generation = _generation
        value = await compute()
        if generation == _generation:
            _results[key] = (time.monotonic(), generation, value)
        return value
