from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import pytest

from app.db.base import Base
from app.models.domain import (
    DownloadClient,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
)
from app.schemas.torrent_status import MediaTorrentStatusResponse
from app.services.media_torrent_status import aggregate_torrent_status, release_torrent_statuses


@pytest.mark.asyncio
async def test_release_and_title_torrent_status_keep_tracking_and_backup_separate():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as db:
        movie = Movie(title="Example", sort_title="example")
        protected_release = MovieRelease(movie=movie, raw_release_name="Example 2026 2160p-GRP")
        untracked_release = MovieRelease(movie=movie, raw_release_name="Example 2026 1080p-GRP")
        client = DownloadClient()
        torrent = Torrent(
            info_hash="a" * 40,
            name="Example 2026 2160p-GRP",
            archive_state=TorrentArchiveState.ARCHIVED,
            manifest_schema_version=2,
            tracker_summary={"state": "healthy"},
        )
        db.add_all([movie, protected_release, untracked_release, client, torrent])
        await db.flush()
        db.add_all(
            [
                MovieReleaseTorrent(movie_release_id=protected_release.id, torrent_id=torrent.id),
                TorrentClientObservation(
                    torrent_id=torrent.id,
                    download_client_id=client.id,
                    is_present=True,
                ),
            ]
        )
        await db.flush()

        statuses = await release_torrent_statuses(
            db,
            [protected_release.id, untracked_release.id],
            media_type="movies",
        )
        assert statuses[protected_release.id].state == "backed_up"
        assert statuses[protected_release.id].present_torrent_count == 1
        assert statuses[untracked_release.id].state == "untracked"

        title = aggregate_torrent_status(statuses.values())
        assert title.state == "partial"
        assert title.release_count == 2
        assert title.tracked_release_count == 1
        assert title.backed_up_release_count == 1

    await engine.dispose()


def test_unregistered_or_failed_archive_requires_attention():
    status = aggregate_torrent_status(
        [
            MediaTorrentStatusResponse(
                state="partial",
                release_count=1,
                tracked_release_count=1,
                torrent_count=1,
                present_torrent_count=1,
                failed_archive_count=1,
                tracker_state="unregistered",
            )
        ]
    )
    assert status.state == "attention"
