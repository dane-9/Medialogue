"""Learning which tracker hosts belong to which indexer.

A torrent Medialogue did not grab itself can only be traced by the tracker it
announces to, and nothing says which indexer that tracker is. So the first time
an indexer answers, one of its .torrent files is read for its announce host.

The file is never given to qBittorrent, so nothing announces to the tracker. A
magnet link naming its trackers is preferred, since that needs no download at
all. Otherwise the smallest listed release is used: its .torrent is the
shortest, and every torrent on a tracker announces to the same place.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.integrations.qbittorrent import magnet_announce_hosts, torrent_announce_hosts
from app.integrations.torznab import SearchResult, TorznabClient
from app.models.domain import IndexerTrackerHost, utcnow
from app.services.integration_state import ConfiguredIndexer
from app.services.quality_profiles import rescore_library_after_save

logger = logging.getLogger(__name__)

#: A sample that failed is not worth retrying on every search.
RETRY_AFTER_SECONDS = 24 * 60 * 60
_last_tried: dict[UUID, float] = {}


async def remember_tracker_hosts(db: AsyncSession, indexer_id: UUID | None, hosts: Sequence[str]) -> bool:
    """Record hosts for an indexer in the caller's transaction. True if any were new."""

    if indexer_id is None or not hosts:
        return False
    now = utcnow()
    outcome = await db.execute(
        sqlite_insert(IndexerTrackerHost)
        .values([{"indexer_id": indexer_id, "host": host, "learned_at": now} for host in hosts])
        .on_conflict_do_nothing()
    )
    return bool(outcome.rowcount)


def sample_release(results: Iterable[SearchResult]) -> SearchResult | None:
    """The release to read an indexer's tracker from."""

    usable = [result for result in results if result.download_url]
    magnet = next(
        (result for result in usable if result.download_url.casefold().startswith("magnet:") and magnet_announce_hosts(result.download_url)),
        None,
    )
    if magnet is not None:
        return magnet
    files = [result for result in usable if not result.download_url.casefold().startswith("magnet:")]
    return min(files, key=lambda result: result.size if result.size is not None else float("inf"), default=None)


async def _read_hosts(indexer: ConfiguredIndexer, release: SearchResult, client_factory: Any) -> tuple[str, ...]:
    if release.download_url.casefold().startswith("magnet:"):
        return magnet_announce_hosts(release.download_url)
    client = client_factory(indexer.torznab_url, indexer.api_key or "", timeout=float(indexer.timeout_seconds))
    try:
        return torrent_announce_hosts(await client.fetch_torrent(release.download_url))
    finally:
        await client.close()


async def sample_new_indexers(
    answered: Iterable[tuple[ConfiguredIndexer, Sequence[SearchResult]]],
    *,
    client_factory: Any = TorznabClient,
) -> None:
    """Learn the tracker of each indexer not yet known, from results already in hand.

    Runs after the work that fetched those results has finished, and can never
    fail it.
    """

    try:
        async with db_session.async_session_factory() as db:
            known = set(await db.scalars(select(IndexerTrackerHost.indexer_id).distinct()))

        now = monotonic()
        wanted: dict[UUID, tuple[ConfiguredIndexer, list[SearchResult]]] = {}
        for indexer, results in answered:
            if indexer.id not in known and now - _last_tried.get(indexer.id, -RETRY_AFTER_SECONDS) >= RETRY_AFTER_SECONDS:
                wanted.setdefault(indexer.id, (indexer, []))[1].extend(results)

        samples = [(indexer, release) for indexer, results in wanted.values() if (release := sample_release(results)) is not None]
        for indexer, _release in samples:
            _last_tried[indexer.id] = now
        hosts = await asyncio.gather(
            *[_read_hosts(indexer, release, client_factory) for indexer, release in samples],
            return_exceptions=True,
        )

        learned = False
        async with db_session.async_session_factory() as db:
            for (indexer, _release), found in zip(samples, hosts):
                if isinstance(found, BaseException):
                    logger.warning(f"Could not read the tracker of {indexer.name}: {found}", extra={"indexer": indexer.name})
                    continue
                learned = await remember_tracker_hosts(db, indexer.id, found) or learned
            await db.commit()
    except Exception:
        logger.warning("could not learn indexer tracker hosts", exc_info=True)
        return
    if learned:
        # Custom Formats can test the indexer, which existing releases now have.
        await rescore_library_after_save()
