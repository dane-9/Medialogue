"""Saving a profile or Custom Format does not wait for the whole library."""

import asyncio

from sqlalchemy import event

from app.db import session as db_session
from app.services import quality_profiles as module
from tests.test_upgrade_candidates import _login, client  # noqa: F401


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def commit(self):
        return None


def _fake_rescore(monkeypatch, work):
    monkeypatch.setattr(module.db_session, "async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr(module, "refresh_all_release_scores", work)
    monkeypatch.setattr(module, "publish_live_event", lambda *_args, **_kwargs: None)


def test_saves_during_a_rescore_share_one_more_run(monkeypatch) -> None:
    runs = 0
    release = asyncio.Event()

    async def work(_db):
        nonlocal runs
        runs += 1
        await release.wait()

    _fake_rescore(monkeypatch, work)

    async def scenario():
        first = asyncio.create_task(module.rescore_library_after_save())
        await asyncio.sleep(0)
        await module.rescore_library_after_save()
        await module.rescore_library_after_save()
        release.set()
        await first

    asyncio.run(scenario())
    assert runs == 2
    assert module._rescore_running is False


def test_a_failed_rescore_does_not_stop_the_next(monkeypatch) -> None:
    runs = 0

    async def work(_db):
        nonlocal runs
        runs += 1
        raise RuntimeError("boom")

    _fake_rescore(monkeypatch, work)
    asyncio.run(module.rescore_library_after_save())
    asyncio.run(module.rescore_library_after_save())
    assert runs == 2


def test_the_custom_format_list_does_not_query_per_format(client) -> None:  # noqa: F811
    _login(client)
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(db_session.engine.sync_engine, "before_cursor_execute", record)
    try:
        response = client.get("/api/v1/custom-formats", params={"page_size": 250})
    finally:
        event.remove(db_session.engine.sync_engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) > 50
    assert len([item for item in statements if "profile_custom_format_scores" in item]) == 1
    assert any(item["used_by_profiles"] > 0 for item in items)
