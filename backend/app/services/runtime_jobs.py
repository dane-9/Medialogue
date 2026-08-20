"""In-process runtime task registry for persistent Job rows.

The database Job row is the durable source of truth.  This registry only owns
live asyncio Tasks so an API request can launch work without tying it to
Starlette's request-scoped BackgroundTasks and so cancellation can stop a live
worker as well as update the persisted state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

logger = logging.getLogger(__name__)

_runtime_tasks: dict[UUID, asyncio.Task[None]] = {}


def runtime_job_running(job_id: UUID) -> bool:
    task = _runtime_tasks.get(job_id)
    return bool(task is not None and not task.done())


def launch_runtime_job(job_id: UUID, factory: Callable[[], Awaitable[None]]) -> bool:
    """Launch one runtime worker for a persisted Job.

    Returns False when the same Job already has a live task.  The task is kept
    in a strong-reference registry until completion so it cannot disappear
    merely because the originating HTTP request has finished.
    """

    current = _runtime_tasks.get(job_id)
    if current is not None and not current.done():
        return False

    async def runner() -> None:
        await factory()

    task = asyncio.create_task(runner(), name=f"medialogue-job-{job_id}")
    _runtime_tasks[job_id] = task

    def done_callback(done: asyncio.Task[None]) -> None:
        _runtime_tasks.pop(job_id, None)
        if done.cancelled():
            return
        try:
            error = done.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.error("runtime job crashed", exc_info=(type(error), error, error.__traceback__), extra={"job_id": str(job_id)})

    task.add_done_callback(done_callback)
    return True


def cancel_runtime_job(job_id: UUID) -> bool:
    """Cancel the live asyncio worker, if one exists."""

    task = _runtime_tasks.get(job_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def cancel_all_runtime_jobs() -> None:
    """Best-effort shutdown cancellation; durable rows are fixed on startup."""

    for task in list(_runtime_tasks.values()):
        if not task.done():
            task.cancel()
