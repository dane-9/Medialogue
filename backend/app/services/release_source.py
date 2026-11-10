"""Where a release on disk came from: its indexer, or failing that its tracker.

Medialogue keeps no direct link from a release to an indexer. When Medialogue
itself sent the torrent to the client, the indexer is known from that search
result or upgrade, matched by info hash. Otherwise the tracker the torrent
announces to names the indexer when that tracker is known to belong to one, and
is the best available answer on its own when not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.integration_config import get_integration_config_store
from app.models.domain import (IndexerTrackerHost, InteractiveSearchResult, MovieReleaseTorrent, ShowReleaseTorrent,
                               Torrent, UpgradeCandidate)


@dataclass(frozen=True, slots=True)
class ReleaseSource:
    indexer_name: str | None = None
    indexer_id: UUID | None = None
    tracker: str | None = None


def torrent_tracker_host(torrent: Torrent) -> str | None:
    value = (torrent.tracker_summary or {}).get("tracker")
    if not value:
        return None
    value = str(value)
    parsed = urlsplit(value if "//" in value else f"//{value}")
    return parsed.hostname or value


async def indexers_by_tracker_host(db: AsyncSession) -> dict[str, ReleaseSource]:
    """The indexer behind each tracker host, for hosts only one indexer claims."""

    rows = (await db.execute(select(IndexerTrackerHost.host, IndexerTrackerHost.indexer_id))).all()
    if not rows:
        return {}
    names = {config.id: config.name for config in get_integration_config_store().list_indexers()}
    claimed: dict[str, set[UUID]] = {}
    for host, indexer_id in rows:
        if indexer_id in names:
            claimed.setdefault(host, set()).add(indexer_id)
    return {
        host: ReleaseSource(indexer_name=names[indexer_id], indexer_id=indexer_id, tracker=host)
        for host, (indexer_id, *others) in claimed.items()
        if not others
    }


#: Rows per IN (...) query, under SQLite's bound-parameter limit.
CHUNK = 500


def _hashes(torrents: Iterable[Torrent]) -> list[str]:
    return [torrent.info_hash.lower() for torrent in torrents if torrent.info_hash]


@dataclass(frozen=True, slots=True)
class SourceLookup:
    """Which indexer sent each info hash: by an upgrade, or by a search."""

    upgrades: dict[str, ReleaseSource]
    searches: dict[str, ReleaseSource]
    trackers: dict[str, ReleaseSource] = field(default_factory=dict)

    def source(self, torrents: Iterable[Torrent]) -> ReleaseSource:
        """The source of the first of these torrents that can name one.

        An upgrade's indexer wins over a search's, either over the indexer known
        to own the tracker, and that over the bare tracker.
        """

        torrents = list(torrents)
        hashes = _hashes(torrents)
        for table in (self.upgrades, self.searches):
            found = next((table[value] for value in hashes if value in table), None)
            if found is not None:
                return found
        hosts = [host for host in map(torrent_tracker_host, torrents) if host]
        owned = next((self.trackers[host] for host in hosts if host in self.trackers), None)
        return owned or ReleaseSource(tracker=hosts[0] if hosts else None)


async def source_lookup(db: AsyncSession, torrents: Iterable[Torrent]) -> SourceLookup:
    """Everything ``SourceLookup.source`` needs for these torrents, in a few queries."""

    hashes = sorted(set(_hashes(torrents)))
    upgrades: dict[str, ReleaseSource] = {}
    searches: dict[str, ReleaseSource] = {}
    for start in range(0, len(hashes), CHUNK):
        chunk = hashes[start:start + CHUNK]
        for value, name, indexer_id in (await db.execute(
            select(func.lower(UpgradeCandidate.submitted_info_hash), UpgradeCandidate.indexer_name, UpgradeCandidate.indexer_id)
            .where(func.lower(UpgradeCandidate.submitted_info_hash).in_(chunk), UpgradeCandidate.indexer_name.is_not(None))
        )).all():
            upgrades.setdefault(value, ReleaseSource(indexer_name=name, indexer_id=indexer_id))
        for value, name, indexer_id in (await db.execute(
            select(func.lower(InteractiveSearchResult.submitted_info_hash), InteractiveSearchResult.indexer_name,
                   InteractiveSearchResult.indexer_id)
            .where(func.lower(InteractiveSearchResult.submitted_info_hash).in_(chunk))
        )).all():
            searches.setdefault(value, ReleaseSource(indexer_name=name, indexer_id=indexer_id))
    return SourceLookup(upgrades=upgrades, searches=searches, trackers=await indexers_by_tracker_host(db))


async def source_of_torrents(db: AsyncSession, torrents: Iterable[Torrent]) -> ReleaseSource:
    """The source of the first of these torrents that can name one."""

    torrents = list(torrents)
    return (await source_lookup(db, torrents)).source(torrents)


async def source_of_releases(db: AsyncSession, *, movie_release_ids: Iterable[UUID] = (),
                             show_release_ids: Iterable[UUID] = ()) -> ReleaseSource:
    """The source of releases, through the torrents attached to them."""

    torrents: list[Torrent] = []
    movie_release_ids, show_release_ids = list(movie_release_ids), list(show_release_ids)
    if movie_release_ids:
        torrents += list(await db.scalars(
            select(Torrent).join(MovieReleaseTorrent, MovieReleaseTorrent.torrent_id == Torrent.id)
            .where(MovieReleaseTorrent.movie_release_id.in_(movie_release_ids))
        ))
    if show_release_ids:
        torrents += list(await db.scalars(
            select(Torrent).join(ShowReleaseTorrent, ShowReleaseTorrent.torrent_id == Torrent.id)
            .where(ShowReleaseTorrent.show_release_id.in_(show_release_ids))
        ))
    return await source_of_torrents(db, torrents)
