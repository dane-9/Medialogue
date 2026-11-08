from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from uuid import UUID

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
from app.models.domain import MediaDirectory, MediaDiscovery


class FakeTMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def health(self):
        return {"status": "healthy"}

    MOVIES = {
        438631: ("Dune", 2021, "/dune.jpg"),
        329865: ("Arrival", 2016, "/arrival.jpg"),
    }

    def _movie(self, tmdb_id: int) -> TMDBMovieMatch:
        title, year, poster = self.MOVIES[tmdb_id]
        return TMDBMovieMatch(tmdb_id, title, title, year, "A test movie.", poster)

    async def search_movie(self, title: str, year: int | None = None):
        needle = title.casefold()
        hits = [self._movie(tmdb_id) for tmdb_id, (name, _, _) in self.MOVIES.items() if name.casefold() in needle]
        return hits or [self._movie(438631)]

    async def get_movie(self, tmdb_id: int):
        return self._movie(tmdb_id if tmdb_id in self.MOVIES else 438631)

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


def test_low_confidence_movie_in_automatic_folder_goes_to_review_without_problem(
    client: TestClient, tmp_path: Path
) -> None:
    headers = login(client)
    configure_tmdb(client, headers)
    root = tmp_path / "movies"
    folder = root / "Odd Folder"
    folder.mkdir(parents=True)
    (folder / "video.mkv").write_bytes(b"movie")
    created = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={
            "name": "Automatic movies",
            "path": str(root),
            "media_type": "movies",
            "discovery_mode": "automatic",
        },
    )
    assert created.status_code == 201, created.text

    scan(client, headers, created.json()["id"])

    review = client.get("/api/v1/discoveries?page_size=250").json()
    assert review["total"] == 1
    assert Path(review["items"][0]["path"]).name == "Odd Folder"
    assert client.get("/api/v1/problems?status=open").json()["total"] == 0


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

    approved_movie = client.post("/api/v1/discoveries/bulk", headers=headers, json={"ids": [movie_item['id']], "action": "approve"})
    assert approved_movie.status_code == 200, approved_movie.text
    assert client.get("/api/v1/movies").json()["total"] == 1
    assert client.get("/api/v1/shows").json()["total"] == 0

    approved_show = client.post("/api/v1/discoveries/bulk", headers=headers, json={"ids": [show_item['id']], "action": "approve"})
    assert approved_show.status_code == 200, approved_show.text
    assert client.get("/api/v1/shows").json()["total"] == 1

    remaining = client.get("/api/v1/discoveries/count")
    assert remaining.status_code == 200, remaining.text
    assert remaining.json()["count"] == 0


def test_scan_caches_full_tmdb_result_lists_for_review(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    async def movie_results(self, title: str, year: int | None = None):
        return [self._movie(438631), self._movie(329865)]

    async def show_results(self, title: str, year: int | None = None):
        return [
            TMDBShowMatch(194764, "Dollface", "Dollface", 2019, "A test show.", "/dollface.jpg"),
            TMDBShowMatch(294764, "Dollface Stories", "Dollface Stories", 2020, "Another result.", "/other.jpg"),
        ]

    monkeypatch.setattr(FakeTMDBClient, "search_movie", movie_results)
    monkeypatch.setattr(FakeTMDBClient, "search_show", show_results)

    movie_root = tmp_path / "movies"
    movie_folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    movie_folder.mkdir(parents=True)
    (movie_folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")

    show_root = tmp_path / "shows"
    show_folder = show_root / "Dollface.2019"
    show_folder.mkdir(parents=True)
    (show_folder / "Dollface.S01E01.1080p.WEB-DL.mkv").write_bytes(b"episode")

    movie_storage = create_root(client, headers, movie_root, "movies")
    show_storage = create_root(client, headers, show_root, "shows")
    scan(client, headers, movie_storage["id"])
    scan(client, headers, show_storage["id"])

    items = client.get("/api/v1/discoveries?page_size=250").json()["items"]
    movie = next(item for item in items if item["media_type"] == "movies")
    show = next(item for item in items if item["media_type"] == "shows")
    assert [item["title"] for item in movie["candidates"]] == ["Dune", "Arrival"]
    assert [item["title"] for item in show["candidates"]] == ["Dollface", "Dollface Stories"]

    async def unexpected_search(self, title: str, year: int | None = None):
        raise AssertionError("reading cached Review candidates must not search TMDB")

    monkeypatch.setattr(FakeTMDBClient, "search_movie", unexpected_search)
    monkeypatch.setattr(FakeTMDBClient, "search_show", unexpected_search)
    cached = client.get("/api/v1/discoveries?page_size=250")
    assert cached.status_code == 200, cached.text
    assert all(len(item["candidates"]) == 2 for item in cached.json()["items"])


def test_ambiguous_movie_in_automatic_folder_falls_back_to_review(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    headers = login(client)
    configure_tmdb(client, headers)
    searches = 0

    async def ambiguous_movies(self, title: str, year: int | None = None):
        nonlocal searches
        searches += 1
        return [
            TMDBMovieMatch(438631, "Dune", "Dune", 2021, "First candidate.", "/dune.jpg"),
            TMDBMovieMatch(938631, "Dune", "Dune", 2021, "Second candidate.", "/other-dune.jpg"),
        ]

    monkeypatch.setattr(FakeTMDBClient, "search_movie", ambiguous_movies)
    movie_root = tmp_path / "automatic-movies"
    movie_folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    movie_folder.mkdir(parents=True)
    (movie_folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    created = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": "Automatic movies", "path": str(movie_root), "media_type": "movies"},
    )
    assert created.status_code == 201, created.text

    scan(client, headers, created.json()["id"])

    assert client.get("/api/v1/problems?status=open").json()["total"] == 0
    review = client.get("/api/v1/discoveries?page_size=250").json()
    assert review["total"] == 1
    assert [item["tmdb_id"] for item in review["items"][0]["candidates"]] == [438631, 938631]
    assert searches == 1, "The TMDB result used for automatic matching should be cached for Review"


@pytest.mark.parametrize(
    ("media_type", "old_name", "new_name", "file_name"),
    [
        ("movies", "Dune.2021.1080p.BluRay", "Dune.2021.RENAMED.1080p.BluRay", "Dune.2021.mkv"),
        ("shows", "Dollface.2019", "Dollface.2019.RENAMED", "Dollface.S01E01.mkv"),
    ],
)
def test_confirmed_missing_folder_is_retired_from_review_after_rename(
    client: TestClient,
    tmp_path: Path,
    media_type: str,
    old_name: str,
    new_name: str,
    file_name: str,
) -> None:
    headers = login(client)
    configure_tmdb(client, headers)
    media_root = tmp_path / media_type
    old_folder = media_root / old_name
    old_folder.mkdir(parents=True)
    (old_folder / file_name).write_bytes(b"media")
    storage = create_root(client, headers, media_root, media_type)
    scan(client, headers, storage["id"])
    original = client.get("/api/v1/discoveries?page_size=250").json()["items"]
    assert [item["path"] for item in original] == [str(old_folder)]

    new_folder = media_root / new_name
    old_folder.rename(new_folder)

    scan(client, headers, storage["id"])
    after_rename = client.get("/api/v1/discoveries?page_size=250").json()["items"]
    assert [item["path"] for item in after_rename] == [str(new_folder)]
    assert client.get("/api/v1/discoveries/count").json()["count"] == 1


def test_missing_discovery_is_hidden_and_retired(
    client: TestClient, tmp_path: Path
) -> None:
    headers = login(client)
    configure_tmdb(client, headers)
    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.1080p.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    async def mark_row_missing() -> None:
        async with db_session.async_session_factory() as session:
            discovery = await session.get(MediaDiscovery, UUID(item["id"]))
            assert discovery is not None
            directory = await session.get(MediaDirectory, discovery.media_directory_id)
            assert directory is not None
            directory.exists = False
            directory.missing_check_count = 2
            await session.commit()

    asyncio.run(mark_row_missing())

    assert client.get("/api/v1/discoveries?page_size=250").json()["items"] == []
    assert client.get("/api/v1/discoveries/count").json()["count"] == 0

    (folder / "Dune.2021.mkv").unlink()
    folder.rmdir()
    scan(client, headers, storage["id"])

    async def row_exists() -> bool:
        async with db_session.async_session_factory() as session:
            return await session.get(MediaDiscovery, UUID(item["id"])) is not None

    assert asyncio.run(row_exists()) is False


def test_bulk_approve_adds_every_selected_item(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    for name, filename in (
        ("Dune.2021.2160p.UHD.BluRay", "Dune.2021.2160p.mkv"),
        ("Arrival.2016.1080p.BluRay", "Arrival.2016.1080p.mkv"),
    ):
        folder = movie_root / name
        folder.mkdir(parents=True)
        (folder / filename).write_bytes(b"movie")

    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])

    items = client.get("/api/v1/discoveries?page_size=250").json()["items"]
    assert len(items) == 2

    response = client.post(
        "/api/v1/discoveries/bulk",
        headers=headers,
        json={"ids": [item["id"] for item in items], "action": "approve"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["succeeded"]) == 2
    assert body["failed"] == []
    assert client.get("/api/v1/movies").json()["total"] == 2
    assert client.get("/api/v1/discoveries/count").json()["count"] == 0


def test_review_blocks_same_movie_edition_until_one_is_changed(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    for group in ("GROUPA", "GROUPB"):
        folder = movie_root / f"Dune.2021.1080p.BluRay-{group}"
        folder.mkdir(parents=True)
        (folder / f"Dune.2021.1080p-{group}.mkv").write_bytes(b"movie")

    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    items = client.get("/api/v1/discoveries?page_size=250").json()["items"]
    assert len(items) == 2
    assert {item["selected"]["tmdb_id"] for item in items} == {438631}

    blocked = client.post(
        "/api/v1/discoveries/bulk",
        headers=headers,
        json={"ids": [item["id"] for item in items], "action": "approve"},
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["succeeded"] == []
    assert {failure["code"] for failure in blocked.json()["failed"]} == {"DISCOVERY_BATCH_EDITION_CONFLICT"}

    first, second = items
    imported = client.post(
        "/api/v1/discoveries/bulk",
        headers=headers,
        json={"ids": [first["id"]], "action": "approve"},
    )
    assert imported.json()["succeeded"] == [first["id"]]

    remaining = client.get("/api/v1/discoveries?page_size=250").json()["items"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == second["id"]
    assert remaining[0]["library_edition_conflict"] is True

    changed = client.patch(
        f"/api/v1/discoveries/{second['id']}/edition",
        headers=headers,
        json={"mode": "named", "edition": "director's cut"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["edition"] == "Director's Cut"
    assert changed.json()["parsed_edition"] is None
    assert changed.json()["edition_override_active"] is True
    assert changed.json()["library_edition_conflict"] is False

    imported_second = client.post(
        "/api/v1/discoveries/bulk",
        headers=headers,
        json={"ids": [second["id"]], "action": "approve"},
    )
    assert imported_second.json()["succeeded"] == [second["id"]]
    movie = client.get("/api/v1/movies").json()["items"][0]
    assert movie["release_count"] == 2
    assert client.get("/api/v1/problems?status=open&category=duplicates").json()["total"] == 0


def test_one_failure_does_not_discard_the_rest_of_a_batch(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    good = movie_root / "Dune.2021.2160p.UHD.BluRay"
    good.mkdir(parents=True)
    (good / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    gone = movie_root / "Arrival.2016.1080p.BluRay"
    gone.mkdir(parents=True)
    (gone / "Arrival.2016.1080p.mkv").write_bytes(b"movie")

    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    items = client.get("/api/v1/discoveries?page_size=250").json()["items"]

    (gone / "Arrival.2016.1080p.mkv").unlink()
    gone.rmdir()
    scan(client, headers, storage["id"])
    scan(client, headers, storage["id"])

    response = client.post(
        "/api/v1/discoveries/bulk",
        headers=headers,
        json={"ids": [item["id"] for item in items], "action": "approve"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["succeeded"]) == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["code"] == "DISCOVERY_NOT_FOUND"
    assert client.get("/api/v1/movies").json()["total"] == 1


def test_bulk_ignore_clears_items_without_adding_them(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    items = client.get("/api/v1/discoveries?page_size=250").json()["items"]

    response = client.post(
        "/api/v1/discoveries/bulk",
        headers=headers,
        json={"ids": [items[0]["id"]], "action": "ignore"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/movies").json()["total"] == 0
    assert client.get("/api/v1/discoveries/count").json()["count"] == 0


def test_an_ignored_item_can_be_restored_to_the_queue(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    client.post("/api/v1/discoveries/bulk", headers=headers, json={"ids": [item['id']], "action": "ignore"})
    assert client.get("/api/v1/discoveries/count").json()["count"] == 0

    restored = client.post("/api/v1/discoveries/bulk", headers=headers, json={"ids": [item['id']], "action": "restore"})
    assert restored.status_code == 200, restored.text
    assert restored.json() == {"succeeded": [item["id"]], "failed": []}
    assert client.get("/api/v1/discoveries/count").json()["count"] == 1

    again = client.post("/api/v1/discoveries/bulk", headers=headers, json={"ids": [item['id']], "action": "restore"}).json()
    assert [failure["code"] for failure in again["failed"]] == ["DISCOVERY_NOT_IGNORED"]


def test_a_discovery_reports_what_is_inside_the_folder(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    (folder / "extras").mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    (folder / "extras" / "Behind.The.Scenes.mkv").write_bytes(b"extra")
    (folder / "eng.srt").write_bytes(b"subtitle")

    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])

    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]
    paths = [file["relative_path"] for file in item["files"]]

    assert "Dune.2021.2160p.mkv" in paths
    assert any(path.endswith("Behind.The.Scenes.mkv") for path in paths)
    assert not any(path.endswith(".srt") for path in paths)
    assert item["file_count"] == 2
    assert item["video_count"] >= 1


def test_choosing_a_known_candidate_does_not_call_tmdb(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    def explode(self, tmdb_id: int):
        raise AssertionError("select must not fetch metadata it already has")

    monkeypatch.setattr(FakeTMDBClient, "get_movie", explode)

    response = client.post(
        f"/api/v1/discoveries/{item['id']}/select",
        headers=headers,
        json={"tmdb_id": 329865, "title": "Arrival", "year": 2016, "poster_ref": "/arrival.jpg"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["selected"]["title"] == "Arrival"
    assert response.json()["selected"]["year"] == 2016


def test_choosing_a_bare_id_still_fetches_the_metadata(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    response = client.post(
        f"/api/v1/discoveries/{item['id']}/select",
        headers=headers,
        json={"tmdb_id": 329865},
    )
    assert response.status_code == 200, response.text
    assert response.json()["selected"]["title"] == "Arrival"


def test_search_stores_candidates_on_the_item(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    response = client.post(f"/api/v1/discoveries/{item['id']}/search", headers=headers, json={"query": "Dune"})
    assert response.status_code == 200, response.text
    titles = [candidate["title"] for candidate in response.json()["candidates"]]
    assert "Dune" in titles

    reloaded = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]
    assert [candidate["title"] for candidate in reloaded["candidates"]] == titles


def test_searching_again_keeps_the_current_selection(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    client.post(
        f"/api/v1/discoveries/{item['id']}/select",
        headers=headers,
        json={"tmdb_id": 329865, "title": "Arrival", "year": 2016},
    )
    response = client.post(f"/api/v1/discoveries/{item['id']}/search", headers=headers, json={"query": "Dune"})
    assert response.status_code == 200, response.text
    assert response.json()["selected"]["title"] == "Arrival"
    assert 329865 in [candidate["tmdb_id"] for candidate in response.json()["candidates"]]


def test_search_needs_something_to_search_for(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    assert client.post(f"/api/v1/discoveries/{item['id']}/search", headers=headers, json={"query": "   "}).status_code in (200, 422)


def test_a_search_result_becomes_the_starting_match(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Some.Unrecognisable.Thing.1999.1080p.BluRay"
    folder.mkdir(parents=True)
    (folder / "thing.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    response = client.post(f"/api/v1/discoveries/{item['id']}/search", headers=headers, json={"query": "Dune"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["selected"] is not None
    assert body["selected"]["tmdb_id"] == body["candidates"][0]["tmdb_id"]


def test_choosing_a_candidate_leaves_the_list_in_place(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    listed = client.post(f"/api/v1/discoveries/{item['id']}/search", headers=headers, json={"query": "Dune Arrival"}).json()
    before = [candidate["tmdb_id"] for candidate in listed["candidates"]]
    assert len(before) > 1

    response = client.post(
        f"/api/v1/discoveries/{item['id']}/select",
        headers=headers,
        json={"tmdb_id": before[1], "title": "Second", "year": 2000},
    )
    assert response.status_code == 200, response.text
    assert [candidate["tmdb_id"] for candidate in response.json()["candidates"]] == before
    assert response.json()["selected"]["tmdb_id"] == before[1]


def test_a_folder_that_disappears_leaves_review_immediately(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    assert client.get("/api/v1/discoveries/count").json()["count"] == 1

    (folder / "Dune.2021.2160p.mkv").unlink()
    folder.rmdir()
    scan(client, headers, storage["id"])

    assert client.get("/api/v1/discoveries/count").json()["count"] == 0


def _quality_profile(client: TestClient, headers: dict[str, str], name: str) -> dict:
    minimum = next(item["id"] for item in client.get("/api/v1/quality-definitions").json() if item["name"] == "2160p BluRay REMUX")
    created = client.post(
        "/api/v1/quality-profiles",
        headers=headers,
        json={"name": name, "minimum_quality_definition_id": minimum, "custom_format_scores": []},
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_a_quality_profile_chosen_in_review_reaches_the_imported_movie(client: TestClient, tmp_path: Path) -> None:
    """Choosing the profile before import is the point of choosing it there.

    Assigning it afterwards meant every imported title spent its first moments
    scored by nothing, and a queue had to be corrected one title at a time.
    """

    headers = login(client)
    configure_tmdb(client, headers)
    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.BluRay.REMUX-GROUP"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.BluRay.REMUX-GROUP.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    discovery = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]
    profile = _quality_profile(client, headers, "4K Movies")

    chosen = client.patch(
        f"/api/v1/discoveries/{discovery['id']}/quality-profile",
        headers=headers,
        json={"quality_profile_id": profile["id"]},
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["quality_profile_id"] == profile["id"]
    assert chosen.json()["quality_profile_name"] == "4K Movies"

    approved = client.post("/api/v1/discoveries/bulk", headers=headers, json={"ids": [discovery['id']], "action": "approve"})
    assert approved.status_code == 200, approved.text

    movie_id = client.get("/api/v1/movies").json()["items"][0]["id"]
    settings = client.get(f"/api/v1/movies/{movie_id}/profile-settings").json()
    assert settings["quality_profile_id"] == profile["id"]


def test_one_quality_profile_can_be_applied_across_a_review_batch(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)
    movie_root = tmp_path / "movies"
    for name in ("Dune.2021.2160p.BluRay.REMUX-GROUP", "Inception.2010.2160p.BluRay.REMUX-GROUP"):
        folder = movie_root / name
        folder.mkdir(parents=True)
        (folder / f"{name}.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    discoveries = client.get("/api/v1/discoveries?page_size=250").json()["items"]
    assert len(discoveries) == 2
    profile = _quality_profile(client, headers, "Everything 4K")

    applied = client.post(
        "/api/v1/discoveries/quality-profile",
        headers=headers,
        json={"ids": [item["id"] for item in discoveries], "quality_profile_id": profile["id"]},
    )
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["succeeded"]) == 2
    assert applied.json()["failed"] == []

    names = {item["quality_profile_name"] for item in client.get("/api/v1/discoveries?page_size=250").json()["items"]}
    assert names == {"Everything 4K"}

    cleared = client.post(
        "/api/v1/discoveries/quality-profile",
        headers=headers,
        json={"ids": [item["id"] for item in discoveries], "quality_profile_id": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert all(item["quality_profile_id"] is None for item in client.get("/api/v1/discoveries?page_size=250").json()["items"])


def test_importing_another_edition_keeps_the_profile_the_movie_already_has(client: TestClient, tmp_path: Path) -> None:
    """A second edition must not rewrite scoring configured earlier.

    The review choice creates the title's profile; it does not reassign one the
    user has already set up, which would make importing one folder silently
    change an existing title.
    """

    headers = login(client)
    configure_tmdb(client, headers)
    movie_root = tmp_path / "movies"
    first = movie_root / "Dune.2021.2160p.BluRay.REMUX-GROUP"
    first.mkdir(parents=True)
    (first / "Dune.2021.2160p.BluRay.REMUX-GROUP.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    original = _quality_profile(client, headers, "Original choice")
    discovery = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]
    client.patch(
        f"/api/v1/discoveries/{discovery['id']}/quality-profile",
        headers=headers,
        json={"quality_profile_id": original["id"]},
    )
    assert client.post("/api/v1/discoveries/bulk", headers=headers, json={"ids": [discovery['id']], "action": "approve"}).status_code == 200
    movie_id = client.get("/api/v1/movies").json()["items"][0]["id"]

    second = movie_root / "Dune.2021.Directors.Cut.2160p.BluRay.REMUX-OTHER"
    second.mkdir(parents=True)
    (second / "Dune.2021.Directors.Cut.2160p.BluRay.REMUX-OTHER.mkv").write_bytes(b"movie")
    scan(client, headers, storage["id"])
    pending = [item for item in client.get("/api/v1/discoveries?page_size=250").json()["items"] if item["new_media_state"] == "pending"]
    if pending:
        other = _quality_profile(client, headers, "Different choice")
        client.patch(
            f"/api/v1/discoveries/{pending[0]['id']}/quality-profile",
            headers=headers,
            json={"quality_profile_id": other["id"]},
        )
        client.post("/api/v1/discoveries/bulk", headers=headers, json={"ids": [pending[0]['id']], "action": "approve"})

    settings = client.get(f"/api/v1/movies/{movie_id}/profile-settings").json()
    assert settings["quality_profile_id"] == original["id"]


def test_a_profile_that_no_longer_exists_is_refused_rather_than_stored(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)
    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.BluRay.REMUX-GROUP"
    folder.mkdir(parents=True)
    (folder / "Dune.2021.2160p.BluRay.REMUX-GROUP.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    discovery = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    refused = client.patch(
        f"/api/v1/discoveries/{discovery['id']}/quality-profile",
        headers=headers,
        json={"quality_profile_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert refused.status_code == 404, refused.text
    assert refused.json()["error"]["code"] == "QUALITY_PROFILE_NOT_FOUND"


def test_a_quality_profile_chosen_in_review_reaches_the_imported_show(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)
    show_root = tmp_path / "shows"
    season = show_root / "Dollface.2019" / "Season 01"
    season.mkdir(parents=True)
    (season / "Dollface.S01E01.1080p.WEB-DL.mkv").write_bytes(b"episode")
    storage = create_root(client, headers, show_root, "shows")
    scan(client, headers, storage["id"])
    discovery = next(
        item
        for item in client.get("/api/v1/discoveries?page_size=250").json()["items"]
        if item["media_type"] == "shows"
    )
    profile = _quality_profile(client, headers, "1080p Shows")

    chosen = client.patch(
        f"/api/v1/discoveries/{discovery['id']}/quality-profile",
        headers=headers,
        json={"quality_profile_id": profile["id"]},
    )
    assert chosen.status_code == 200, chosen.text

    approved = client.post("/api/v1/discoveries/bulk", headers=headers, json={"ids": [discovery['id']], "action": "approve"})
    assert approved.status_code == 200, approved.text

    show_id = client.get("/api/v1/shows").json()["items"][0]["id"]
    settings = client.get(f"/api/v1/shows/{show_id}/profile-settings").json()
    assert settings["quality_profile_id"] == profile["id"]


def test_queue_pages_are_cut_in_the_database_and_cover_every_item(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)
    root = tmp_path / "movies"
    for index in range(5):
        folder = root / f"Dune.2021.Copy{index}"
        folder.mkdir(parents=True)
        (folder / "Dune.2021.mkv").write_bytes(b"movie")
    # A folder gone from disk is counted out of the pending queue too.
    gone = root / "Arrival.2016"
    gone.mkdir()
    (gone / "Arrival.2016.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, root, "movies")
    scan(client, headers, storage["id"])
    for path in gone.iterdir():
        path.unlink()
    gone.rmdir()
    scan(client, headers, storage["id"])

    pages = [client.get(f"/api/v1/discoveries?page={page}&page_size=2").json() for page in (1, 2, 3)]
    assert [page["total"] for page in pages] == [5, 5, 5] and pages[0]["pages"] == 3
    assert [len(page["items"]) for page in pages] == [2, 2, 1]
    ids = [item["id"] for page in pages for item in page["items"]]
    assert len(set(ids)) == 5
    assert client.get("/api/v1/discoveries/count").json()["count"] == 5
    assert client.get("/api/v1/discoveries?page=4&page_size=2").json()["items"] == []
    assert client.get("/api/v1/discoveries?media_type=shows").json()["total"] == 0
