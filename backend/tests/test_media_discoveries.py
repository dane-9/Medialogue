from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.tmdb import (
    TMDBEpisodeMetadata,
    TMDBMovieMatch,
    TMDBSeasonMetadata,
    TMDBShowDetails,
    TMDBShowMatch,
)
from app.main import create_app


class FakeTMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def health(self):
        return {"status": "healthy"}

    async def search_movie(self, title: str, year: int | None = None):
        return [TMDBMovieMatch(438631, "Dune", "Dune", 2021, "A test movie.", "/dune.jpg")]

    async def get_movie(self, tmdb_id: int):
        return TMDBMovieMatch(438631, "Dune", "Dune", 2021, "A test movie.", "/dune.jpg")

    async def get_movie_alternative_titles(self, tmdb_id: int):
        return ()

    async def search_show(self, title: str, year: int | None = None):
        return [TMDBShowMatch(194764, "Dollface", "Dollface", 2019, "A test show.", "/dollface.jpg")]

    async def get_show(self, tmdb_id: int):
        return TMDBShowDetails(
            tmdb_id=194764,
            title="Dollface",
            original_title="Dollface",
            year=2019,
            overview="A test show.",
            poster_path="/dollface.jpg",
            tvdb_id=361563,
            seasons=(TMDBSeasonMetadata(1, "Season 1", 1),),
        )

    async def get_season(self, tmdb_id: int, season_number: int):
        return [TMDBEpisodeMetadata(1001, season_number, 1, "Episode One", None, "One")]

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def fake_tmdb(monkeypatch):
    import app.api.tmdb as tmdb_api
    import app.services.tmdb as tmdb_service

    monkeypatch.setattr(tmdb_api, "TMDBClient", FakeTMDBClient)
    monkeypatch.setattr(tmdb_service, "TMDBClient", FakeTMDBClient)


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
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
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(db_session.engine.dispose())
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def configure_tmdb(client: TestClient, headers: dict[str, str]) -> None:
    response = client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
    assert response.status_code == 200, response.text


def create_root(client: TestClient, headers: dict[str, str], path: Path, media_type: str) -> dict:
    response = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={
            "name": f"Review {media_type}",
            "path": str(path),
            "media_type": media_type,
            "discovery_mode": "review",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def scan(client: TestClient, headers: dict[str, str], root_id: str) -> dict:
    response = client.post(f"/api/v1/storage-roots/{root_id}/scan", headers=headers)
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{response.json()['job_id']}")
        assert job.status_code == 200, job.text
        payload = job.json()
        if payload["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            assert payload["status"] == "completed", payload
            return payload
        time.sleep(0.02)
    raise AssertionError("scan did not complete")


def test_review_stages_and_approves_movies_and_shows(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    movie_dir = movie_root / "Dune.2021.2160p.UHD.BluRay"
    movie_dir.mkdir(parents=True)
    (movie_dir / "Dune.2021.2160p.mkv").write_bytes(b"movie")

    show_root = tmp_path / "shows"
    show_dir = show_root / "Dollface.2019"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    (season_dir / "Dollface.S01E01.1080p.WEB-DL.mkv").write_bytes(b"episode")

    movie_storage = create_root(client, headers, movie_root, "movies")
    show_storage = create_root(client, headers, show_root, "shows")
    scan(client, headers, movie_storage["id"])
    scan(client, headers, show_storage["id"])

    assert client.get("/api/v1/movies").json()["total"] == 0
    assert client.get("/api/v1/shows").json()["total"] == 0

    queue = client.get("/api/v1/discoveries?page_size=250")
    assert queue.status_code == 200, queue.text
    items = queue.json()["items"]
    assert {item["media_type"] for item in items} == {"movies", "shows"}
    assert all(item["selected"] is not None for item in items)

    movie_item = next(item for item in items if item["media_type"] == "movies")
    show_item = next(item for item in items if item["media_type"] == "shows")

    approved_movie = client.post(f"/api/v1/discoveries/{movie_item['id']}/approve", headers=headers)
    assert approved_movie.status_code == 200, approved_movie.text
    assert client.get("/api/v1/movies").json()["total"] == 1
    assert client.get("/api/v1/shows").json()["total"] == 0

    approved_show = client.post(f"/api/v1/discoveries/{show_item['id']}/approve", headers=headers)
    assert approved_show.status_code == 200, approved_show.text
    assert client.get("/api/v1/shows").json()["total"] == 1

    remaining = client.get("/api/v1/discoveries/count")
    assert remaining.status_code == 200, remaining.text
    assert remaining.json()["count"] == 0
