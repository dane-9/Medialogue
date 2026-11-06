"""Where a release on disk came from: its indexer, or failing that its tracker.

Medialogue keeps no direct link from a release to an indexer. When Medialogue
itself sent the torrent to the client, the indexer is known from that search
result or upgrade, matched by info hash. Otherwise the tracker the torrent
announces to is the best available answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (InteractiveSearchResult, MovieReleaseTorrent, ShowReleaseTorrent, Torrent,
                               UpgradeCandidate)


@dataclass(frozen=True, slots=True)
class ReleaseSource:
    indexer_name: str | None = None
    indexer_id: UUID | None = None
    tracker: str | None = None


def _tracker_host(torrent: Torrent) -> str | None:
    value = (torrent.tracker_summary or {}).get("tracker")
    if not value:
        return None
    value = str(value)
    parsed = urlsplit(value if "//" in value else f"//{value}")
    return parsed.hostname or value


async def source_of_torrents(db: AsyncSession, torrents: Iterable[Torrent]) -> ReleaseSource:
    """The source of the first of these torrents that can name one."""

    torrents = list(torrents)
    hashes = [torrent.info_hash.lower() for torrent in torrents if torrent.info_hash]
    if hashes:
        upgrade = (await db.execute(
            select(UpgradeCandidate.indexer_name, UpgradeCandidate.indexer_id)
            .where(func.lower(UpgradeCandidate.submitted_info_hash).in_(hashes), UpgradeCandidate.indexer_name.is_not(None))
            .limit(1)
        )).first()
        if upgrade:
            return ReleaseSource(indexer_name=upgrade[0], indexer_id=upgrade[1])
        result = (await db.execute(
            select(InteractiveSearchResult.indexer_name, InteractiveSearchResult.indexer_id)
            .where(func.lower(InteractiveSearchResult.submitted_info_hash).in_(hashes))
            .limit(1)
        )).first()
        if result:
            return ReleaseSource(indexer_name=result[0], indexer_id=result[1])
    tracker = next((host for host in (_tracker_host(torrent) for torrent in torrents) if host), None)
    return ReleaseSource(tracker=tracker)


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
