"""How shared lists are served and kept ready. No database is involved."""

import asyncio

import pytest

from app.core.config import get_settings
from app.db import session as db_session
from app.services import events, shared_results as module


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Builder:
    def __init__(self, value):
        self.value = value
        self.calls = 0
        self.error = None

    async def __call__(self, _db):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value


@pytest.fixture
def published(monkeypatch):
    monkeypatch.setattr(get_settings(), "replacement_results_seconds", 30.0)
    monkeypatch.setattr(db_session, "async_session_factory", _Session)
    monkeypatch.setattr(module, "_refresher_running", True)
    monkeypatch.setattr(module, "_pins", {})
    module._entries.clear()
    names: list[str] = []
    monkeypatch.setattr(events, "publish_live_event", lambda name, **_: names.append(name))
    yield names
    module._entries.clear()


async def test_clients_share_one_copy(published):
    build = _Builder(["a"])
    assert await module.shared_result(("movies",), None, build) == ["a"]
    assert await module.shared_result(("movies",), None, build) == ["a"]
    assert build.calls == 1


async def test_a_background_change_serves_the_last_copy_then_announces_the_new_one(published):
    build = _Builder(["old"])
    await module.shared_result(("movies",), None, build)
    build.value = ["new"]
    module._entries[next(iter(module._entries))].attempted_at = -1e9
    module.mark_shared_results_stale()

    assert await module.shared_result(("movies",), None, build) == ["old"]
    assert build.calls == 1

    assert await module.refresh_due_results() is True
    assert published == ["results.movies"]
    assert await module.shared_result(("movies",), None, build) == ["new"]
    assert build.calls == 2


async def test_a_rebuild_that_changes_nothing_tells_nobody(published):
    build = _Builder(["same"])
    await module.shared_result(("movies",), None, build)
    module._entries[next(iter(module._entries))].attempted_at = -1e9
    module.mark_shared_results_stale()
    await module.refresh_due_results()
    assert build.calls == 2
    assert published == []


async def test_a_change_through_the_api_is_never_answered_with_the_old_copy(published):
    build = _Builder(["old"])
    await module.shared_result(("movies",), None, build)
    build.value = ["new"]
    module.invalidate_shared_results()
    assert await module.shared_result(("movies",), None, build) == ["new"]


async def test_a_burst_of_changes_rebuilds_once_per_gap(published):
    build = _Builder(["a"])
    await module.shared_result(("movies",), None, build)
    entry = next(iter(module._entries.values()))
    entry.attempted_at = -1e9
    module.mark_shared_results_stale()
    assert await module.refresh_due_results() is True
    module.mark_shared_results_stale()
    assert await module.refresh_due_results() is False
    assert build.calls == 2
    # The change is not lost: it is rebuilt once the gap has passed.
    entry.attempted_at = -1e9
    assert await module.refresh_due_results() is True
    assert build.calls == 3


async def test_a_list_nobody_reads_is_rebuilt_less_often(published):
    build = _Builder(["a"])
    await module.shared_result(("movies",), None, build)
    entry = next(iter(module._entries.values()))
    import time
    now = time.monotonic()
    entry.read_at = now - module._ACTIVE_READ_SECONDS - 1
    entry.attempted_at = now - module._ACTIVE_GAP_SECONDS - 1
    module.mark_shared_results_stale()
    assert await module.refresh_due_results() is False
    entry.attempted_at = now - module._IDLE_GAP_SECONDS - 1
    assert await module.refresh_due_results() is True


async def test_an_unread_list_is_not_rebuilt_only_for_its_age(published):
    build = _Builder(["a"])
    await module.shared_result(("movies",), None, build)
    entry = next(iter(module._entries.values()))
    entry.built_at = entry.read_at = entry.built_at - 60
    entry.attempted_at = -1e9
    assert await module.refresh_due_results() is False
    entry.read_at = entry.built_at + 1
    assert await module.refresh_due_results() is True


async def test_a_list_built_from_another_uses_a_current_copy(published):
    titles = _Builder(["old title"])

    async def upgrades(db):
        return await module.shared_result(("titles",), db, titles)

    await module.shared_result(("upgrades",), None, upgrades)
    titles.value = ["new title"]
    for entry in module._entries.values():
        entry.attempted_at = -1e9
    module.mark_shared_results_stale()
    # The upgrades list is rebuilt first, and must not inherit the stale titles.
    module._entries[next(key for key in module._entries if "upgrades" in key)].read_at += 1
    module._entries[next(key for key in module._entries if "titles" in key)].read_at -= 400
    await module.refresh_due_results()
    assert await module.shared_result(("upgrades",), None, upgrades) == ["new title"]


async def test_a_failed_rebuild_keeps_the_last_copy(published):
    build = _Builder(["good"])
    await module.shared_result(("movies",), None, build)
    build.error = RuntimeError("database is busy")
    module._entries[next(iter(module._entries))].attempted_at = -1e9
    module.mark_shared_results_stale()
    await module.refresh_due_results()
    assert await module.shared_result(("movies",), None, build) == ["good"]
    assert published == []


async def test_a_request_that_fails_leaves_nothing_behind(published):
    build = _Builder(["a"])
    build.error = ValueError("bad state")
    with pytest.raises(ValueError):
        await module.shared_result(("movies", "nonsense"), None, build)
    assert module._entries == {}


async def test_pinned_lists_are_ready_before_anyone_asks(published, monkeypatch):
    build = _Builder(["ready"])
    module.pin_shared_result(("movies",), build)
    monkeypatch.setattr(module, "_refresher_running", False)
    stop = asyncio.Event()
    task = asyncio.create_task(module.run_shared_results_refresher(stop))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if build.calls:
            break
    stop.set()
    await task
    assert build.calls == 1
    monkeypatch.setattr(module, "_refresher_running", True)
    assert await module.shared_result(("movies",), None, build) == ["ready"]
    assert build.calls == 1


def test_announcing_a_rebuilt_list_does_not_make_it_out_of_date():
    assert events._changes_library_state({"event": "results.movies"}) is False
    assert events._changes_library_state({"event": "torrent.removed"}) is True
