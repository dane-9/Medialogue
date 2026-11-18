"""Movies and seasons grabbed from a search whose download is not linked to them yet.

A grab sends the torrent to qBittorrent at once, but the movie only learns of
it once a poll has seen the torrent and reconciliation has linked it as an
incoming release. Until then the grab itself says a download is on its way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (InteractiveSearchResult, MovieRelease, MovieReleaseTorrent, ShowRelease,
                               ShowReleaseTorrent, Torrent, TorrentClientObservation)

#: A grab qBittorrent never reported, or that never got linked, stops counting
#: after this long rather than saying "downloading" forever.
PENDING_FOR = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class PendingGrab:
    result: InteractiveSearchResult
    torrent: Torrent | None
    #: The client's latest word on the torrent; None until a poll has seen it.
    observation: TorrentClientObservation | None

    @property
    def client_name(self) -> str:
        return str((self.result.selection_snapshot or {}).get("download_client_name") or "qBittorrent")


async def pending_movie_grabs(db: AsyncSession, movie_ids: list[UUID] | None = None) -> dict[UUID, list[PendingGrab]]:
    """Grabs not yet linked to their movie, newest first, by movie.

    ``movie_ids`` None: every movie. A grab whose torrent was removed from
    qBittorrent before it was linked is over, so it is left out.
    """

    return await _pending_grabs(
        db, "movie", movie_ids,
        lambda torrent_ids: select(MovieReleaseTorrent.torrent_id, MovieRelease.movie_id)
        .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
        .where(MovieReleaseTorrent.torrent_id.in_(torrent_ids)),
    )


async def pending_season_grabs(db: AsyncSession, season_ids: list[UUID]) -> dict[UUID, list[PendingGrab]]:
    """Season grabs not yet linked to a release of their season, newest first, by season."""

    return await _pending_grabs(
        db, "season", season_ids,
        lambda torrent_ids: select(ShowReleaseTorrent.torrent_id, ShowRelease.season_id)
        .join(ShowRelease, ShowRelease.id == ShowReleaseTorrent.show_release_id)
        .where(ShowReleaseTorrent.torrent_id.in_(torrent_ids)),
    )


async def _pending_grabs(db: AsyncSession, entity_type: str, target_ids: list[UUID] | None, links) -> dict[UUID, list[PendingGrab]]:
    if target_ids is not None and not target_ids:
        return {}
    statement = (
        select(InteractiveSearchResult)
        .where(
            InteractiveSearchResult.target_entity_type == entity_type,
            InteractiveSearchResult.target_entity_id.is_not(None),
            InteractiveSearchResult.selected_at.is_not(None),
            InteractiveSearchResult.selected_at >= datetime.now(timezone.utc) - PENDING_FOR,
        )
        .order_by(InteractiveSearchResult.selected_at.desc())
    )
    if target_ids is not None:
        statement = statement.where(InteractiveSearchResult.target_entity_id.in_(target_ids))
    results = list((await db.scalars(statement)).all())
    if not results:
        return {}
    hashes = {value.lower() for result in results if (value := result.submitted_info_hash)}
    torrents = {
        torrent.info_hash.lower(): torrent
        for torrent in (await db.scalars(select(Torrent).where(func.lower(Torrent.info_hash).in_(hashes)))).all()
    } if hashes else {}
    linked: set[tuple[UUID, UUID]] = set()
    observations: dict[UUID, TorrentClientObservation] = {}
    present_seen: set[UUID] = set()
    if torrents:
        torrent_ids = [torrent.id for torrent in torrents.values()]
        linked = {
            (torrent_id, target_id)
            for torrent_id, target_id in (await db.execute(links(torrent_ids))).all()
        }
        # One observation per client: the latest client still holding the
        # torrent, else the latest that dropped it.
        for observation in (await db.scalars(
            select(TorrentClientObservation)
            .where(TorrentClientObservation.torrent_id.in_(torrent_ids))
            .order_by(TorrentClientObservation.last_seen_at)
        )).all():
            if observation.is_present or observation.torrent_id not in present_seen:
                observations[observation.torrent_id] = observation
            if observation.is_present:
                present_seen.add(observation.torrent_id)
    found: dict[UUID, list[PendingGrab]] = {}
    seen: set[tuple[UUID, str]] = set()
    for result in results:
        target_id = result.target_entity_id
        assert target_id is not None
        value = (result.submitted_info_hash or "").lower()
        torrent = torrents.get(value) if value else None
        if value:
            # One grab per torrent: a release grabbed twice shows once.
            if (target_id, value) in seen:
                continue
            seen.add((target_id, value))
        if torrent is not None and (torrent.id, target_id) in linked:
            continue
        observation = observations.get(torrent.id) if torrent is not None else None
        if observation is not None and not observation.is_present:
            continue
        found.setdefault(target_id, []).append(PendingGrab(result, torrent, observation))
    return found
