"""Switching a show to a TMDB episode group renumbers it without losing episodes.

The Dexter's Laboratory case: TMDB's default structure says one long season,
while the ordering people actually name their files after has four. An episode
group holds the same episodes with a different arrangement, so switching must
renumber the existing rows rather than rebuild the show.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.tmdb import (
    TMDBEpisodeGroup,
    TMDBEpisodeGroupSummary,
    TMDBEpisodeMetadata,
    TMDBSeasonMetadata,
    TMDBShowDetails,
    TMDBShowMatch,
)
from app.main import create_app

SHOW_ID = 1958
# Eight episodes, identified by TMDB id. The default structure calls them one
# season of eight; the group calls them two seasons of four.
EPISODE_IDS = [9001, 9002, 9003, 9004, 9005, 9006, 9007, 9008]


class FakeTMDB:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def health(self):
        return {"status": "healthy"}

    async def close(self):
        return None

    async def search_show(self, title: str, year: int | None = None):
        return [TMDBShowMatch(SHOW_ID, "Dexters Laboratory", "Dexters Laboratory", 1996, "A test show.", None)]

    async def get_show(self, tmdb_id: int):
        return TMDBShowDetails(
            tmdb_id=SHOW_ID,
            title="Dexters Laboratory",
            original_title="Dexters Laboratory",
            year=1996,
            overview="A test show.",
            poster_path=None,
            tvdb_id=None,
            seasons=(TMDBSeasonMetadata(season_number=1, title="The Complete Series", episode_count=8),),
        )

    async def get_season(self, tmdb_id: int, season_number: int):
        if season_number != 1:
            return []
        return [
            TMDBEpisodeMetadata(
                tmdb_id=EPISODE_IDS[index],
                season_number=1,
                episode_number=index + 1,
                title=f"Episode {index + 1}",
                air_date=None,
            )
            for index in range(len(EPISODE_IDS))
        ]

    async def list_episode_groups(self, tmdb_id: int):
        return [
            TMDBEpisodeGroupSummary(
                id="group-production",
                name="TV (Production)",
                type=6,
                group_count=2,
                episode_count=8,
                description="Production order.",
                network=None,
            )
        ]

    async def get_episode_group(self, group_id: str):
        assert group_id == "group-production"
        seasons = []
        for season_number in (1, 2):
            offset = (season_number - 1) * 4
            seasons.append((
                season_number,
                f"Season {season_number}",
                tuple(
                    TMDBEpisodeMetadata(
                        tmdb_id=EPISODE_IDS[offset + index],
                        season_number=season_number,
                        episode_number=index + 1,
                        title=f"Episode {offset + index + 1}",
                        air_date=None,
                    )
                    for index in range(4)
                ),
            ))
        return TMDBEpisodeGroup(id=group_id, name="TV (Production)", type=6, seasons=tuple(seasons))


@pytest.fixture()
def client(monkeypatch):
    import app.api.tmdb as tmdb_api
    import app.services.tmdb as tmdb_service

    monkeypatch.setattr(tmdb_service, "TMDBClient", FakeTMDB)
    monkeypatch.setattr(tmdb_api, "TMDBClient", FakeTMDB)

    db_path = tempfile.mktemp(prefix="medialogue-orderings-", suffix=".db", dir=os.getcwd())
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(
        database_url=database_url,
        bootstrap_admin=True,
        config_dir=f"{db_path}.config",
        secret_key="test-secret-key-123456",
    )
    engine = create_async_engine(database_url)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    with TestClient(create_app(settings)) as test_client:
        yield test_client
    asyncio.run(db_session.engine.dispose())
    shutil.rmtree(f"{db_path}.config", ignore_errors=True)
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass


def _login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200, response.text
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
    return headers


def test_switching_ordering_renumbers_the_same_episodes(client: TestClient) -> None:
    headers = _login(client)
    created = client.post("/api/v1/shows", headers=headers, json={"tmdb_id": SHOW_ID, "monitored": True})
    assert created.status_code == 201, created.text

    detail = client.get(f"/api/v1/shows/{SHOW_ID}").json()
    assert len(detail["seasons"]) == 1, "default structure is one season"
    assert detail["seasons"][0]["episode_count"] == 8

    listed = client.get(f"/api/v1/shows/{SHOW_ID}/episode-orderings")
    assert listed.status_code == 200, listed.text
    options = listed.json()
    assert options[0]["id"] is None and options[0]["selected"] is True
    production = next(item for item in options if item["name"] == "TV (Production)")
    assert production["type_label"] == "Production"
    assert production["season_count"] == 2

    switched = client.patch(
        f"/api/v1/shows/{SHOW_ID}",
        headers=headers,
        json={"tmdb_episode_group_id": production["id"], "expected_revision": detail["revision"]},
    )
    assert switched.status_code == 200, switched.text

    after = client.get(f"/api/v1/shows/{SHOW_ID}").json()
    assert len(after["seasons"]) == 2, after["seasons"]
    assert [season["episode_count"] for season in after["seasons"]] == [4, 4]
    # The same eight episodes, renumbered — not sixteen.
    total = sum(season["episode_count"] for season in after["seasons"])
    assert total == len(EPISODE_IDS)

    reselected = client.get(f"/api/v1/shows/{SHOW_ID}/episode-orderings").json()
    assert next(item for item in reselected if item["id"] == production["id"])["selected"] is True
    assert next(item for item in reselected if item["id"] is None)["selected"] is False


def test_switching_back_restores_the_default_structure(client: TestClient) -> None:
    headers = _login(client)
    client.post("/api/v1/shows", headers=headers, json={"tmdb_id": SHOW_ID, "monitored": True})

    detail = client.get(f"/api/v1/shows/{SHOW_ID}").json()
    client.patch(
        f"/api/v1/shows/{SHOW_ID}",
        headers=headers,
        json={"tmdb_episode_group_id": "group-production", "expected_revision": detail["revision"]},
    )
    grouped = client.get(f"/api/v1/shows/{SHOW_ID}").json()
    assert len(grouped["seasons"]) == 2

    back = client.patch(
        f"/api/v1/shows/{SHOW_ID}",
        headers=headers,
        json={"tmdb_episode_group_id": "", "expected_revision": grouped["revision"]},
    )
    assert back.status_code == 200, back.text
    restored = client.get(f"/api/v1/shows/{SHOW_ID}").json()
    assert restored["tmdb_episode_group_id"] is None
    assert len(restored["seasons"]) == 1
    assert restored["seasons"][0]["episode_count"] == 8
