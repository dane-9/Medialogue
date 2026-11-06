"""Which releases in the library could be downloaded again after losing the server.

A release is protected when one of its torrents is archived and both that
torrent and its manifest are in the Google Drive copy. Each release is counted
once however many cross-seeds it has.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    ReleaseState,
    Season,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    Torrent,
    TorrentArchiveState,
)
from app.services.remote_backups import read_backup_settings

#: How each status reads, worst first.
STATUSES = ("no_torrent", "not_archived", "local_only", "protected")
GAP_LIMIT = 10000


async def archive_coverage(db: AsyncSession) -> dict[str, Any]:
    mirror = read_backup_settings().get("mirror") or {}

    def on_drive(info_hash: str) -> bool:
        key = info_hash.lower()
        return f"torrents/{key}.torrent" in mirror and f"manifests/{key}.json" in mirror

    releases: list[dict[str, Any]] = []
    for release_id, release_name, title, year, poster in await db.execute(
        select(MovieRelease.id, MovieRelease.raw_release_name, Movie.title, Movie.year, Movie.poster_ref)
        .join(Movie, Movie.id == MovieRelease.movie_id)
        .where(MovieRelease.release_state == ReleaseState.CURRENT)
    ):
        releases.append({"id": release_id, "media_type": "movies", "title": title, "year": year, "poster_ref": poster,
                         "season_number": None, "release_name": release_name})
    for release_id, release_name, title, year, poster, season_number in await db.execute(
        select(ShowRelease.id, ShowRelease.raw_release_name, Show.title, Show.year, Show.poster_ref, Season.season_number)
        .join(Show, Show.id == ShowRelease.show_id)
        .outerjoin(Season, Season.id == ShowRelease.season_id)
        .where(ShowRelease.release_state == ReleaseState.CURRENT)
    ):
        releases.append({"id": release_id, "media_type": "shows", "title": title, "year": year, "poster_ref": poster,
                         "season_number": season_number, "release_name": release_name})

    torrents: dict[UUID, list[tuple[str, TorrentArchiveState | None]]] = {}
    for release_id, info_hash, state in await db.execute(
        select(MovieReleaseTorrent.movie_release_id, Torrent.info_hash, Torrent.archive_state)
        .join(Torrent, Torrent.id == MovieReleaseTorrent.torrent_id)
    ):
        torrents.setdefault(release_id, []).append((info_hash, state))
    for release_id, info_hash, state in await db.execute(
        select(ShowReleaseTorrent.show_release_id, Torrent.info_hash, Torrent.archive_state)
        .join(Torrent, Torrent.id == ShowReleaseTorrent.torrent_id)
    ):
        torrents.setdefault(release_id, []).append((info_hash, state))

    counts = {status: 0 for status in STATUSES}
    gaps: list[dict[str, Any]] = []
    for release in releases:
        linked = torrents.get(release["id"], [])
        archived = [info_hash for info_hash, state in linked if state == TorrentArchiveState.ARCHIVED]
        if not linked:
            status = "no_torrent"
        elif not archived:
            status = "not_archived"
        elif not any(on_drive(info_hash) for info_hash in archived):
            status = "local_only"
        else:
            status = "protected"
        counts[status] += 1
        if status != "protected":
            gaps.append({**release, "id": str(release["id"]), "status": status})
    gaps.sort(key=lambda item: (STATUSES.index(item["status"]), (item["title"] or "").casefold(), item["season_number"] or 0))
    return {"total": len(releases), **counts, "gaps": gaps[:GAP_LIMIT], "gaps_truncated": len(gaps) > GAP_LIMIT}
