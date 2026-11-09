"""The per-tracker "Use for upgrades" setting."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.integration_config import IndexerConfig, IntegrationConfigStore
from app.db import session as db_session
from app.models.domain import MediaScope, MediaType, UpgradeCandidate, UpgradeSearchMark
from app.services import upgrade_candidates as module
from tests.test_release_feed import _check, _search, _seed_movies, _Tracker
from tests.test_upgrade_candidates import _create_indexer, _login, client  # noqa: F401


def test_a_config_file_from_before_the_setting_loads_with_it_on(tmp_path: Path) -> None:
    store = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    store.ensure_initialized()
    indexer_id = uuid4()
    store.save_indexer(IndexerConfig(id=indexer_id, name="TL", torznab_url="http://prowlarr/1", api_key="key", scope="movies"))
    path = tmp_path / "medialogue.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    del written["indexers"][0]["use_for_upgrades"]
    path.write_text(json.dumps(written), encoding="utf-8")

    reopened = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    assert reopened.get_indexer(indexer_id).use_for_upgrades is True


def test_the_setting_is_saved(tmp_path: Path) -> None:
    store = IntegrationConfigStore(tmp_path, "integration-config-test-secret-123456")
    store.ensure_initialized()
    indexer_id = uuid4()
    store.save_indexer(IndexerConfig(id=indexer_id, name="TL", torznab_url="http://prowlarr/1", api_key="key", scope="movies", use_for_upgrades=False))
    assert store.get_indexer(indexer_id).use_for_upgrades is False


def _set_upgrades(test_client, headers, indexer_id: str, value: bool) -> None:
    response = test_client.put(f"/api/v1/indexers/{indexer_id}", headers=headers, json={"use_for_upgrades": value})
    assert response.status_code == 200, response.text
    assert response.json()["use_for_upgrades"] is value


def _only_indexer(test_client) -> str:
    return test_client.get("/api/v1/indexers").json()["items"][0]["id"]


def _candidate(key: str, indexer_id: UUID | None, *, state: str | None = None, results: list | None = None) -> UpgradeCandidate:
    return UpgradeCandidate(target_key=key, media_type=MediaType.MOVIES, current_release_ids=[], current_release_name="Old", indexer_id=indexer_id,
                            indexer_name="TL", guid=key, title="New", execution_state=state, search_results=results or [])


def test_turning_it_off_removes_only_what_has_not_started(client) -> None:  # noqa: F811
    headers = _login(client)
    _create_indexer(client, headers)
    indexer_id = _only_indexer(client)
    tracker = UUID(indexer_id)
    other_results = [{"indexer_id": indexer_id, "title": "from TL"}, {"indexer_id": "other", "title": "from elsewhere"}]

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            db.add_all([
                _candidate("movie-release:idle", tracker),
                _candidate("movie-release:failed", tracker, state="failed"),
                _candidate("movie-release:running", tracker, state="downloading"),
                _candidate("replace:movie-release:lost", tracker),
                _candidate("movie-release:other", None, results=other_results),
                UpgradeSearchMark(target_key="movie-release:idle", searched_at=module.utcnow()),
                UpgradeSearchMark(target_key="movie-release:other", searched_at=module.utcnow()),
            ])
            await db.commit()

    async def state():
        async with db_session.async_session_factory() as db:
            rows = {row.target_key: row for row in (await db.scalars(select(UpgradeCandidate))).all()}
            marks = set(await db.scalars(select(UpgradeSearchMark.target_key)))
            return rows, marks

    asyncio.run(seed())
    _set_upgrades(client, headers, indexer_id, True)
    assert len(asyncio.run(state())[0]) == 5

    _set_upgrades(client, headers, indexer_id, False)
    rows, marks = asyncio.run(state())
    assert set(rows) == {"movie-release:running", "replace:movie-release:lost", "movie-release:other"}
    assert [entry["title"] for entry in rows["movie-release:other"].search_results] == ["from elsewhere"]
    assert marks == {"movie-release:other"}


def test_a_tracker_turned_off_is_left_out_of_upgrades(client) -> None:  # noqa: F811
    headers = _login(client)
    _create_indexer(client, headers)
    _set_upgrades(client, headers, _only_indexer(client), False)
    asyncio.run(_seed_movies("Upgrade Movie"))

    tracker = _Tracker()
    _search(tracker)
    _check(tracker)
    assert tracker.searched == []
    assert tracker.feed_requests == []


def test_replace_media_still_searches_it(monkeypatch) -> None:
    indexer = SimpleNamespace(id=uuid4(), enabled=True, enable_interactive_search=True, use_for_upgrades=False, scope=MediaScope.BOTH)
    queried: list[UUID] = []

    async def query(found, _target, *, client_factory):
        queried.append(found.id)
        return found.id, {"status": "completed", "results": []}

    async def evaluate(_db, *, target, answered):
        return [], [], []

    monkeypatch.setattr(module, "_query_indexer", query)
    monkeypatch.setattr(module, "evaluate_results", evaluate)

    def search(replacement: bool):
        target = SimpleNamespace(media_type=MediaType.MOVIES, replacement=replacement, search_target=None)
        return asyncio.run(module._search_target(None, target=target, indexers=[indexer], client_factory=None))

    search(False)
    assert queried == []
    search(True)
    assert queried == [indexer.id]
