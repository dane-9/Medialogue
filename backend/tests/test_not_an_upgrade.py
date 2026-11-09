"""Rejecting one release for one title from Upgrade Media."""

import asyncio
from uuid import UUID

from sqlalchemy import select

from app.db import session as db_session
from app.models.domain import ReleaseBlocklist, UpgradeCandidate
from tests.test_upgrade_execution import _create_integrations, _login, _seed_movie_upgrade, client  # noqa: F401


def _result(indexer, *, guid, title, score, quality="2160p WEB-DL"):
    return {
        "id": guid, "guid": guid, "indexer_id": indexer["id"], "indexer_name": indexer["name"], "eligible": True,
        "title": title, "download_url": f"http://x/{guid}", "quality": quality, "custom_format_score": score,
        "snapshot": {"current": {"custom_format_score": 0}, "candidate": {"edition": None}},
    }


async def _set_results(candidate_id, results) -> None:
    async with db_session.async_session_factory() as db:
        row = await db.get(UpgradeCandidate, candidate_id)
        row.search_results = results
        row.guid, row.title = results[0]["guid"], results[0]["title"]
        await db.commit()


async def _state(candidate_id):
    async with db_session.async_session_factory() as db:
        row = await db.get(UpgradeCandidate, candidate_id)
        entries = (await db.scalars(select(ReleaseBlocklist))).all()
        return row, entries


def test_the_next_best_release_is_offered_and_every_listing_is_blocked(client) -> None:  # noqa: F811
    test_client, tmp_path = client
    headers = _login(test_client)
    qbit, indexer = _create_integrations(test_client, headers)
    candidate_id, movie_id, *_ = asyncio.run(_seed_movie_upgrade(tmp_path, qbit, indexer))
    asyncio.run(_set_results(candidate_id, [
        _result(indexer, guid="a", title="Upgrade.Movie.2024.2160p.WEB-DL-GRP", score=25),
        _result({**indexer, "id": str(UUID(int=7)), "name": "Other"}, guid="b", title="Upgrade Movie 2024 2160p WEB-DL-GRP.mkv", score=25),
        _result(indexer, guid="c", title="Upgrade.Movie.2024.2160p.WEB-DL-NEXT", score=20),
    ]))

    response = test_client.post(f"/api/v1/upgrade-candidates/{candidate_id}/not-an-upgrade", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"next_title": "Upgrade.Movie.2024.2160p.WEB-DL-NEXT"}
    row, entries = asyncio.run(_state(candidate_id))
    assert row.guid == "c" and row.custom_format_score == 20
    assert {entry.indexer_name for entry in entries} == {indexer["name"], "Other"}
    assert {entry.reason for entry in entries} == {"Not an upgrade"}
    assert {entry.target_entity_id for entry in entries} == {movie_id}
    assert [item["eligible"] for item in row.search_results] == [False, False, True]


def test_the_title_leaves_the_list_when_nothing_else_qualifies(client) -> None:  # noqa: F811
    test_client, tmp_path = client
    headers = _login(test_client)
    qbit, indexer = _create_integrations(test_client, headers)
    candidate_id, *_ = asyncio.run(_seed_movie_upgrade(tmp_path, qbit, indexer))
    asyncio.run(_set_results(candidate_id, [_result(indexer, guid="a", title="Upgrade.Movie.2024.2160p.WEB-DL-GRP", score=25)]))

    response = test_client.post(f"/api/v1/upgrade-candidates/{candidate_id}/not-an-upgrade", headers=headers)

    assert response.json() == {"next_title": None}
    assert asyncio.run(_state(candidate_id))[0] is None


def test_an_upgrade_already_under_way_cannot_be_rejected(client) -> None:  # noqa: F811
    test_client, tmp_path = client
    headers = _login(test_client)
    qbit, indexer = _create_integrations(test_client, headers)
    candidate_id, *_ = asyncio.run(_seed_movie_upgrade(tmp_path, qbit, indexer))

    async def start():
        async with db_session.async_session_factory() as db:
            (await db.get(UpgradeCandidate, candidate_id)).execution_state = "downloading"
            await db.commit()

    asyncio.run(start())
    response = test_client.post(f"/api/v1/upgrade-candidates/{candidate_id}/not-an-upgrade", headers=headers)
    assert response.status_code == 409
