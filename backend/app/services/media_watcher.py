"""Live watching of media folders, so changes on disk show up within a minute.

Scheduled scans only notice a folder deleted by hand, or a new one copied in,
hours later. The watcher follows filesystem events (inotify on Linux) under
every scanned media folder. When a top-level folder (one movie, one show) has
been quiet for SETTLE_SECONDS, that folder alone is checked: reconciled like a
scan would, and its known files verified with the usual missing grace. A folder
whose media went missing is checked again every RECHECK_SECONDS until the grace
is used up, so a deletion is confirmed in minutes without one event deciding it.

Events need the files to change on this machine; a library mounted from
elsewhere may report nothing. Scheduled scans therefore stay as the safety net,
and each media folder's watch state is reported (``watch_state``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.db import session as db_session
from app.models.domain import MediaDirectory, MediaFile, MediaType, StorageRoot

logger = logging.getLogger(__name__)

SETTLE_SECONDS = 60.0
RECHECK_SECONDS = 90.0
RETRY_SECONDS = 300.0
ROOTS_REFRESH_SECONDS = 60.0
TICK_SECONDS = 5.0

#: Media folder id -> "watching" or "unavailable". Absent: not watched.
WATCH_STATES: dict[UUID, str] = {}


def watch_state(root_id: UUID) -> str | None:
    return WATCH_STATES.get(root_id)


class FolderQueue:
    """Top-level folders waiting to settle: checked once quiet for long enough."""

    def __init__(self) -> None:
        self._due: dict[tuple[UUID, str], float] = {}

    def touch(self, root_id: UUID, folder: str, *, delay: float | None = None) -> None:
        self._due[(root_id, folder)] = time.monotonic() + (SETTLE_SECONDS if delay is None else delay)

    def take_due(self) -> list[tuple[UUID, str]]:
        now = time.monotonic()
        due = [key for key, at in self._due.items() if at <= now]
        for key in due:
            del self._due[key]
        return due

    def __len__(self) -> int:
        return len(self._due)


def top_level_folder(changed: str, roots: dict[UUID, Path]) -> tuple[UUID, str] | None:
    """The media folder and its top-level folder (one movie or show) a path lies in."""

    path = Path(changed)
    for root_id, base in sorted(roots.items(), key=lambda item: len(str(item[1])), reverse=True):
        try:
            parts = path.relative_to(base).parts
        except ValueError:
            continue
        # The media folder itself is not a movie or show. Anything below maps to
        # its top-level entry; a deleted folder can no longer be told from a
        # loose file, and checking a path nothing is known under is harmless.
        if not parts:
            return None
        return root_id, str(base / parts[0])
    return None


async def _watched_roots() -> dict[UUID, Path]:
    async with db_session.async_session_factory() as db:
        rows = (
            await db.scalars(
                select(StorageRoot).where(StorageRoot.enabled.is_(True), StorageRoot.last_scan_at.is_not(None))
            )
        ).all()
    roots: dict[UUID, Path] = {}
    for root in rows:
        base = Path(root.resolved_root_path)
        if await asyncio.to_thread(base.is_dir):
            roots[root.id] = await asyncio.to_thread(base.resolve)
    return roots


async def refresh_media_folder(root_id: UUID, folder: str) -> bool:
    """Reconcile one top-level folder and verify its known files.

    Returns True when something in it is waiting out the missing grace, so the
    caller checks it again later.
    """

    from app.integrations.filesystem import FilesystemObserver
    from app.services.library_scan import active_storage_root_scan_job
    from app.services.reconciliation import directories_within, reconcile_movie_directory
    from app.services.scheduled_media import _verify_root
    from app.services.shows import reconcile_show_directory

    async with db_session.async_session_factory() as db:
        root = await db.get(StorageRoot, root_id)
        if root is None or not root.enabled:
            return False
        if await active_storage_root_scan_job(db, root.id) is not None:
            # The running scan covers this folder; look again after it.
            return True
        base = Path(root.resolved_root_path)
        path = Path(folder)
        if await asyncio.to_thread(path.is_dir):
            try:
                observation = await asyncio.to_thread(
                    FilesystemObserver().inspect_directory, path, await asyncio.to_thread(base.resolve)
                )
                if root.media_type == MediaType.SHOWS:
                    await reconcile_show_directory(db, root, observation)
                else:
                    await reconcile_movie_directory(db, root, observation)
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("Watched folder could not be reconciled", extra={"path": folder})
        await _verify_root(db, root, within=folder)
        await db.commit()
        pending = await db.scalar(
            select(MediaDirectory.id)
            .outerjoin(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
            .where(
                MediaDirectory.storage_root_id == root.id,
                *directories_within(folder),
                (MediaDirectory.exists.is_(True) & MediaDirectory.missing_since.is_not(None))
                | (MediaFile.exists.is_(True) & MediaFile.missing_since.is_not(None)),
            )
            .limit(1)
        )
        return pending is not None


async def _process(queue: FolderQueue, stop: asyncio.Event) -> None:
    while not stop.is_set():
        for root_id, folder in queue.take_due():
            try:
                if await refresh_media_folder(root_id, folder):
                    queue.touch(root_id, folder, delay=RECHECK_SECONDS)
            except Exception:
                logger.exception("Watched folder check failed", extra={"path": folder})
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _watch(roots: dict[UUID, Path], queue: FolderQueue, stop: asyncio.Event) -> None:
    from watchfiles import awatch

    async for changes in awatch(*roots.values(), stop_event=stop, recursive=True, debounce=2000, yield_on_timeout=False):
        for _change, changed in changes:
            target = top_level_folder(changed, roots)
            if target is not None:
                queue.touch(*target)


async def run_media_watcher(stop_event: asyncio.Event) -> None:
    """Watch every scanned media folder until ``stop_event``; restarts when they change."""

    if not get_settings().watch_media_folders:
        return
    queue = FolderQueue()
    processor = asyncio.create_task(_process(queue, stop_event))
    try:
        while not stop_event.is_set():
            try:
                roots = await _watched_roots()
            except Exception:
                logger.exception("Media folder watcher could not read the media folders")
                roots = {}
            if not roots:
                WATCH_STATES.clear()
                await _sleep(stop_event, ROOTS_REFRESH_SECONDS)
                continue
            inner = asyncio.Event()
            watcher = asyncio.create_task(_watch(roots, queue, inner))
            WATCH_STATES.clear()
            WATCH_STATES.update({root_id: "watching" for root_id in roots})
            failed = False
            # Re-read the media folders now and then: one added, removed or
            # moved restarts the watch with the new set.
            while not stop_event.is_set():
                await _sleep(stop_event, ROOTS_REFRESH_SECONDS, until=watcher)
                if watcher.done():
                    failed = True
                    break
                try:
                    if await _watched_roots() != roots:
                        break
                except Exception:
                    continue
            inner.set()
            try:
                await asyncio.wait_for(watcher, timeout=5)
            except Exception as exc:
                if failed:
                    logger.warning("Media folder watching stopped; scheduled scans continue", extra={"error": str(exc)})
            if failed and not stop_event.is_set():
                # Inotify watch limit, a mount without events, a vanished path:
                # report it and try again later. Scheduled scans still run.
                WATCH_STATES.update({root_id: "unavailable" for root_id in roots})
                await _sleep(stop_event, RETRY_SECONDS)
    finally:
        WATCH_STATES.clear()
        processor.cancel()
        try:
            await processor
        except (asyncio.CancelledError, Exception):
            pass


async def _sleep(stop_event: asyncio.Event, seconds: float, *, until: asyncio.Task | None = None) -> None:
    waits = [asyncio.ensure_future(stop_event.wait())]
    if until is not None:
        waits.append(until)
    done, pending = await asyncio.wait(waits, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        if task is not until:
            task.cancel()
