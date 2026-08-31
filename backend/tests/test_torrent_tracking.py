from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.integrations.qbittorrent import TorrentTrackerSnapshot
from app.models.domain import Torrent, TorrentTrackerObservation
from app.services.torrent_tracking import persist_tracker_health, tracker_endpoint_display


def _tracker(url: str, *, status: int, message: str = "") -> TorrentTrackerSnapshot:
    return TorrentTrackerSnapshot(
        url=url,
        status=status,
        tier=0,
        peers=None,
        seeds=None,
        leeches=None,
        downloaded=None,
        message=message,
    )


@pytest.mark.asyncio
async def test_tracker_health_redacts_passkeys_and_requires_repeated_terminal_failure():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client_id = uuid4()
    private_url = "https://tracker.example/announce/secret-passkey?token=private"
    checked_at = datetime.now(timezone.utc)

    async with sessions() as db:
        torrent = Torrent(info_hash="a" * 40, name="Example")
        db.add(torrent)
        await db.flush()

        first = await persist_tracker_health(
            db,
            torrent,
            client_id,
            (_tracker(private_url, status=4, message=f"Unregistered torrent: {private_url}"),),
            checked_at=checked_at,
        )
        assert first["state"] == "unavailable"

        second = await persist_tracker_health(
            db,
            torrent,
            client_id,
            (_tracker(private_url, status=4, message=f"Unregistered torrent: {private_url}"),),
            checked_at=checked_at,
        )
        assert second["state"] == "unregistered"
        row = await db.scalar(select(TorrentTrackerObservation))
        assert row is not None
        assert row.endpoint_display == "https://tracker.example"
        assert "secret-passkey" not in (row.message or "")
        assert "token=private" not in (row.message or "")
        assert row.terminal_confirmed is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_working_tracker_keeps_mixed_tracker_set_degraded_and_ignores_pseudo_trackers():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as db:
        torrent = Torrent(info_hash="b" * 40, name="Example")
        db.add(torrent)
        await db.flush()
        summary = await persist_tracker_health(
            db,
            torrent,
            uuid4(),
            (
                _tracker("** [DHT] **", status=0),
                _tracker("https://working.example/announce", status=2),
                _tracker("https://failed.example/announce", status=4, message="Error1"),
            ),
            checked_at=datetime.now(timezone.utc),
        )
        assert summary["state"] == "degraded"
        assert summary["total"] == 2
        assert len((await db.scalars(select(TorrentTrackerObservation))).all()) == 2

    await engine.dispose()


def test_tracker_display_never_includes_path_or_query():
    assert tracker_endpoint_display("https://tracker.example:443/a/passkey?token=x") == "https://tracker.example:443"
