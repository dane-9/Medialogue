"""Torrent history for a title: every torrent its releases were ever tied to.

Movies and shows link releases to torrents the same way, so both detail pages
build this list here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.integration_config import get_integration_config_store
from app.models.domain import (
    MovieRelease,
    MovieReleaseTorrent,
    ShowRelease,
    ShowReleaseTorrent,
    Torrent,
    TorrentClientObservation,
)


async def movie_torrent_history(db: AsyncSession, movie_id: UUID) -> list[dict[str, object]]:
    rows = (
        await db.execute(
            select(MovieReleaseTorrent, MovieRelease, Torrent)
            .join(MovieRelease, MovieRelease.id == MovieReleaseTorrent.movie_release_id)
            .join(Torrent, Torrent.id == MovieReleaseTorrent.torrent_id)
            .where(MovieRelease.movie_id == movie_id)
            .order_by(MovieReleaseTorrent.created_at.desc())
        )
    ).all()
    return await _history(db, rows)


async def show_torrent_history(db: AsyncSession, show_id: UUID) -> list[dict[str, object]]:
    rows = (
        await db.execute(
            select(ShowReleaseTorrent, ShowRelease, Torrent)
            .join(ShowRelease, ShowRelease.id == ShowReleaseTorrent.show_release_id)
            .join(Torrent, Torrent.id == ShowReleaseTorrent.torrent_id)
            .where(ShowRelease.show_id == show_id)
            .order_by(ShowReleaseTorrent.created_at.desc())
        )
    ).all()
    return await _history(db, rows)


async def _history(db: AsyncSession, rows: Iterable[Sequence[Any]]) -> list[dict[str, object]]:
    """One entry per torrent, newest link first."""

    history: list[dict[str, object]] = []
    seen: set[UUID] = set()
    for association, release, torrent in rows:
        if torrent.id in seen:
            continue
        seen.add(torrent.id)
        observation = await db.scalar(
            select(TorrentClientObservation)
            .where(TorrentClientObservation.torrent_id == torrent.id)
            .order_by(TorrentClientObservation.first_seen_at.asc())
            .limit(1)
        )
        client = (
            get_integration_config_store().get_download_client(observation.download_client_id)
            if observation is not None
            else None
        )
        qbit_present = bool(
            await db.scalar(
                select(func.count())
                .select_from(TorrentClientObservation)
                .where(
                    TorrentClientObservation.torrent_id == torrent.id,
                    TorrentClientObservation.is_present.is_(True),
                )
            )
        )
        history.append(
            {
                "id": str(torrent.id),
                "torrent_name": torrent.name,
                "release_id": str(release.id),
                "release_name": release.raw_release_name,
                "info_hash": torrent.info_hash,
                "tracker": (torrent.tracker_summary or {}).get("tracker"),
                "archive_state": torrent.archive_state.value,
                "archive_path": torrent.archive_path,
                "manifest_path": torrent.manifest_path,
                "manifest_schema_version": torrent.manifest_schema_version,
                "association_type": association.association_type.value,
                "download_client_name": client.name if client else None,
                "qbit_present": qbit_present,
                "first_seen_at": torrent.first_seen_at.isoformat(),
                "completed_at": torrent.completed_at.isoformat() if torrent.completed_at else None,
            }
        )
    return history
