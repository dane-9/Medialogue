"""Request-volume behaviour of the qBittorrent integration."""

import asyncio
from types import SimpleNamespace

import httpx

from app.integrations.qbittorrent import QBittorrentClient, TorrentSyncState, close_shared_sessions
from app.services.tracker_checks import _tracker_is_active, _tracker_request_budget


def test_sync_torrents_merges_deltas_and_honours_removals_and_full_updates():
    responses = [
        {"rid": 1, "full_update": True, "torrents": {
            "a" * 40: {"name": "A", "progress": 0.5, "state": "downloading", "save_path": "/d"},
            "b" * 40: {"name": "B", "progress": 1, "state": "uploading", "save_path": "/d"},
        }},
        # Delta: only A's progress changed, B was removed.
        {"rid": 2, "torrents": {"a" * 40: {"progress": 0.75}}, "torrents_removed": ["b" * 40]},
        # qBittorrent restarted: full update replaces everything.
        {"rid": 1, "full_update": True, "torrents": {"c" * 40: {"name": "C", "progress": 1, "state": "uploading"}}},
    ]
    seen_rids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, text="Ok.")
        seen_rids.append(request.url.params["rid"])
        return httpx.Response(200, json=responses[len(seen_rids) - 1])

    async def scenario():
        client = QBittorrentClient("http://qbit", "u", "p", transport=httpx.MockTransport(handler))
        state = TorrentSyncState()
        first = await client.sync_torrents(state)
        assert {item.name for item in first} == {"A", "B"}
        second = await client.sync_torrents(state)
        assert [(item.name, item.progress, item.state) for item in second] == [("A", 0.75, "downloading")]
        third = await client.sync_torrents(state)
        assert [item.name for item in third] == ["C"]
        await client.close()

    asyncio.run(scenario())
    assert seen_rids == ["0", "1", "2"]


def test_adapters_for_one_client_share_a_session_and_login_once():
    async def scenario():
        first = QBittorrentClient("http://qbit-a:8080", "u", "p")
        second = QBittorrentClient("http://qbit-a:8080/", "u", "p")
        other = QBittorrentClient("http://qbit-a:8080", "u", "different")
        assert first._session is second._session
        assert other._session is not first._session
        await first.close()  # shared: must not close the pool for the others
        assert not second._client.is_closed
        await close_shared_sessions()

    asyncio.run(scenario())


def test_tracker_budget_checks_settled_torrents_far_less_often():
    # 5,770 seeding torrents polled every 30 s: one hour window, not five minutes.
    assert _tracker_request_budget(0, 5770, 30) == 49
    # Downloading torrents keep the 5 minute window.
    assert _tracker_request_budget(100, 0, 30) == 10
    # Never more than the per-poll ceiling.
    assert _tracker_request_budget(50000, 0, 30) == 200
    settled = SimpleNamespace(progress=1, state="uploading", tracker="https://t.example/announce")
    assert not _tracker_is_active(settled)
    assert _tracker_is_active(SimpleNamespace(progress=1, state="uploading", tracker=""))
    assert _tracker_is_active(SimpleNamespace(progress=0.4, state="downloading", tracker="x"))


def test_tracker_summary_compares_stored_and_fresh_success_times():
    """SQLite hands back stored times without a zone; a success recorded this
    poll has one. Comparing them raised on every poll of a known torrent."""

    from datetime import datetime, timezone

    from app.services.torrent_tracking import _aggregate

    stored = SimpleNamespace(active=True, health_state="working", terminal_confirmed=False,
                             last_success_at=datetime(2026, 1, 1, 12, 0))
    fresh = SimpleNamespace(active=True, health_state="working", terminal_confirmed=False,
                            last_success_at=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc))
    summary = _aggregate([stored, fresh], datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc))
    assert summary["state"] == "healthy"
    assert summary["last_success_at"] == "2026-01-02T12:00:00+00:00"
