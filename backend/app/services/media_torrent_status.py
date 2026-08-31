"""Derived qBittorrent/recovery state for media releases and titles."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import (
    MovieReleaseTorrent,
    ShowReleaseTorrent,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
)
from app.schemas.torrent_status import MediaTorrentStatusResponse
from app.services.torrent_archive import MANIFEST_SCHEMA_VERSION


_TRACKER_PRIORITY = {
    "unknown": 0,
    "healthy": 1,
    "degraded": 2,
    "unavailable": 3,
    "unregistered": 4,
}


def _tracker_state(torrents: Iterable[Torrent]) -> str:
    states = [str((torrent.tracker_summary or {}).get("state") or "unknown") for torrent in torrents]
    states = [state if state in _TRACKER_PRIORITY else "unknown" for state in states]
    return max(states, key=lambda state: _TRACKER_PRIORITY[state], default="unknown")


def aggregate_torrent_status(
    release_statuses: Iterable[MediaTorrentStatusResponse],
) -> MediaTorrentStatusResponse:
    statuses = list(release_statuses)
    if not statuses:
        return MediaTorrentStatusResponse()
    release_count = len(statuses)
    tracked_release_count = sum(item.present_torrent_count > 0 for item in statuses)
    backed_up_release_count = sum(
        item.torrent_count > 0 and item.archived_torrent_count == item.torrent_count
        for item in statuses
    )
    torrent_count = sum(item.torrent_count for item in statuses)
    present_count = sum(item.present_torrent_count for item in statuses)
    archived_count = sum(item.archived_torrent_count for item in statuses)
    failed_count = sum(item.failed_archive_count for item in statuses)
    tracker_state = max(
        (item.tracker_state for item in statuses),
        key=lambda state: _TRACKER_PRIORITY[state],
        default="unknown",
    )
    if failed_count or tracker_state == "unregistered":
        state = "attention"
    elif tracked_release_count == release_count and backed_up_release_count == release_count:
        state = "backed_up"
    elif backed_up_release_count == release_count and tracked_release_count == 0:
        state = "archived"
    elif tracked_release_count == release_count and backed_up_release_count == 0:
        state = "tracked"
    elif tracked_release_count == 0 and backed_up_release_count == 0:
        state = "untracked"
    else:
        state = "partial"
    return MediaTorrentStatusResponse(
        state=state,
        release_count=release_count,
        tracked_release_count=tracked_release_count,
        backed_up_release_count=backed_up_release_count,
        torrent_count=torrent_count,
        present_torrent_count=present_count,
        archived_torrent_count=archived_count,
        failed_archive_count=failed_count,
        tracker_state=tracker_state,
    )


async def release_torrent_statuses(
    db: AsyncSession,
    release_ids: Iterable[UUID],
    *,
    media_type: str,
) -> dict[UUID, MediaTorrentStatusResponse]:
    unique_ids = set(release_ids)
    if not unique_ids:
        return {}
    link_model = MovieReleaseTorrent if media_type == "movies" else ShowReleaseTorrent
    release_column = (
        MovieReleaseTorrent.movie_release_id
        if media_type == "movies"
        else ShowReleaseTorrent.show_release_id
    )
    rows = (
        await db.execute(
            select(release_column, Torrent)
            .join(Torrent, Torrent.id == link_model.torrent_id)
            .where(release_column.in_(unique_ids))
        )
    ).all()
    torrent_ids = {torrent.id for _, torrent in rows}
    present_ids: set[UUID] = set()
    if torrent_ids:
        present_ids = set(
            (
                await db.scalars(
                    select(TorrentClientObservation.torrent_id)
                    .where(
                        TorrentClientObservation.torrent_id.in_(torrent_ids),
                        TorrentClientObservation.is_present.is_(True),
                    )
                    .group_by(TorrentClientObservation.torrent_id)
                    .having(func.count() > 0)
                )
            ).all()
        )
    torrents_by_release: dict[UUID, list[Torrent]] = {release_id: [] for release_id in unique_ids}
    for release_id, torrent in rows:
        torrents_by_release[release_id].append(torrent)
    result: dict[UUID, MediaTorrentStatusResponse] = {}
    for release_id, torrents in torrents_by_release.items():
        archived = [
            torrent
            for torrent in torrents
            if torrent.archive_state == TorrentArchiveState.ARCHIVED
            and torrent.manifest_schema_version == MANIFEST_SCHEMA_VERSION
        ]
        present_count = sum(torrent.id in present_ids for torrent in torrents)
        failed_count = sum(torrent.archive_state == TorrentArchiveState.FAILED for torrent in torrents)
        tracker_state = _tracker_state(torrents)
        if failed_count or tracker_state == "unregistered":
            state = "attention"
        elif torrents and present_count and len(archived) == len(torrents):
            state = "backed_up"
        elif torrents and not present_count and len(archived) == len(torrents):
            state = "archived"
        elif torrents and present_count and not archived:
            state = "tracked"
        elif torrents:
            state = "partial"
        else:
            state = "untracked"
        result[release_id] = MediaTorrentStatusResponse(
            state=state,
            release_count=1,
            tracked_release_count=int(present_count > 0),
            backed_up_release_count=int(bool(torrents) and len(archived) == len(torrents)),
            torrent_count=len(torrents),
            present_torrent_count=present_count,
            archived_torrent_count=len(archived),
            failed_archive_count=failed_count,
            tracker_state=tracker_state,
        )
    return result
