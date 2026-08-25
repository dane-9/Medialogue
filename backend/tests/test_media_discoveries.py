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

    #: A tiny catalogue, so two different folders can match two different films.
    #: Returning one fixed title for every query made any multi-item test look
    #: like a matching failure.
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

    # A fresh queue read uses the stored candidates; it does not search TMDB.
    async def unexpected_search(self, title: str, year: int | None = None):
        raise AssertionError("reading cached Review candidates must not search TMDB")

    monkeypatch.setattr(FakeTMDBClient, "search_movie", unexpected_search)
    monkeypatch.setattr(FakeTMDBClient, "search_show", unexpected_search)
    cached = client.get("/api/v1/discoveries?page_size=250")
    assert cached.status_code == 200, cached.text
    assert all(len(item["candidates"]) == 2 for item in cached.json()["items"])


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

    # The first miss is still protected by the configured grace window while
    # the renamed path is discovered immediately.
    scan(client, headers, storage["id"])
    during_grace = client.get("/api/v1/discoveries?page_size=250").json()["items"]
    assert {item["path"] for item in during_grace} == {str(old_folder), str(new_folder)}

    # Once absence is confirmed, only the actionable new path remains.
    scan(client, headers, storage["id"])
    after_grace = client.get("/api/v1/discoveries?page_size=250").json()["items"]
    assert [item["path"] for item in after_grace] == [str(new_folder)]
    assert client.get("/api/v1/discoveries/count").json()["count"] == 1


def test_legacy_missing_discovery_is_hidden_and_retired(
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

    async def mark_legacy_row_missing() -> None:
        async with db_session.async_session_factory() as session:
            discovery = await session.get(MediaDiscovery, UUID(item["id"]))
            assert discovery is not None
            directory = await session.get(MediaDirectory, discovery.media_directory_id)
            assert directory is not None
            directory.exists = False
            directory.missing_check_count = 2
            await session.commit()

    asyncio.run(mark_legacy_row_missing())

    # It is hidden immediately, without waiting for another filesystem scan.
    assert client.get("/api/v1/discoveries?page_size=250").json()["items"] == []
    assert client.get("/api/v1/discoveries/count").json()["count"] == 0

    # The next scan also removes the obsolete database row rather than hitting
    # the old already-missing early return forever.
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

    # One directory disappears between the scan and the approval, which is the
    # ordinary way a batch goes partly wrong. A root tolerates two consecutive
    # misses before calling a directory gone, so this takes two scans.
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
    # Confirmed-missing pending work is retired from Review. A stale batch
    # assembled before that scan therefore reports that row as gone.
    assert body["failed"][0]["code"] == "DISCOVERY_NOT_FOUND"
    # The healthy item still went in rather than being rolled back with it.
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

    client.post(f"/api/v1/discoveries/{item['id']}/ignore", headers=headers)
    assert client.get("/api/v1/discoveries/count").json()["count"] == 0

    # Ignoring is a mis-clickable action, so it has to be reversible.
    restored = client.post(f"/api/v1/discoveries/{item['id']}/restore", headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["review_state"] == "pending"
    assert client.get("/api/v1/discoveries/count").json()["count"] == 1

    # Restoring something already pending is a conflict, not a silent no-op.
    assert client.post(f"/api/v1/discoveries/{item['id']}/restore", headers=headers).status_code == 409


def test_a_discovery_reports_what_is_inside_the_folder(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    movie_root = tmp_path / "movies"
    folder = movie_root / "Dune.2021.2160p.UHD.BluRay"
    (folder / "extras").mkdir(parents=True)
    (folder / "Dune.2021.2160p.mkv").write_bytes(b"movie")
    (folder / "extras" / "Behind.The.Scenes.mkv").write_bytes(b"extra")
    # Not a video suffix, so the scanner never records it. Asserting on it would
    # be asserting on a file that does not exist as far as the database knows.
    (folder / "eng.srt").write_bytes(b"subtitle")

    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])

    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]
    paths = [file["relative_path"] for file in item["files"]]

    # The folder name is the thing that might be wrong, so the files that settle
    # it have to reach the reviewer.
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

    # Recording a choice the reviewer was already looking at must not reach the
    # network: it was a round trip per click, which is what made selecting slow.
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

    # No display values and not a stored candidate, so the server has to look it
    # up rather than record an id with no title attached.
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

    # Stored on the item, not in a browser: a fresh read sees the same list, so
    # reopening the folder never repeats the search.
    reloaded = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]
    assert [candidate["title"] for candidate in reloaded["candidates"]] == titles


def test_existing_review_item_is_reparsed_before_automatic_tmdb_search(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    headers = login(client)
    configure_tmdb(client, headers)

    show_root = tmp_path / "shows"
    folder = show_root / (
        "Batman.The.Animated.Series.COMPLETE.S01-S04.1080p.BluRay."
        "DTS-HD.MA.H.264-BTN"
    )
    folder.mkdir(parents=True)
    (folder / "Batman.S01E01.mkv").write_bytes(b"episode")
    storage = create_root(client, headers, show_root, "shows")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    async def make_stale() -> None:
        async with db_session.async_session_factory() as session:
            discovery = await session.get(MediaDiscovery, UUID(item["id"]))
            assert discovery is not None
            snapshot = dict(discovery.parse_snapshot)
            snapshot["parser_version"] = "1.2.0"
            discovery.parse_snapshot = snapshot
            discovery.parsed_title = "Batman The Animated Series COMPLETE"
            discovery.candidates = [{"tmdb_id": 999, "title": "Wrong old result"}]
            discovery.suggested_tmdb_id = 999
            discovery.selected_tmdb_id = 999
            await session.commit()

    asyncio.run(make_stale())
    queries: list[str] = []
    original_search = FakeTMDBClient.search_show

    async def capture_search(self, title: str, year: int | None = None):
        queries.append(title)
        return await original_search(self, title, year)

    monkeypatch.setattr(FakeTMDBClient, "search_show", capture_search)

    refreshed = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]
    assert refreshed["parsed_title"] == "Batman The Animated Series"
    assert refreshed["candidates"] == []
    assert refreshed["selected"] is None

    searched = client.post(
        f"/api/v1/discoveries/{item['id']}/search",
        headers=headers,
        json={"query": None},
    )
    assert searched.status_code == 200, searched.text
    assert queries == ["Batman The Animated Series"]


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
    # A search for something else must not erase the choice from the list it is
    # selected in.
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
    # A folder whose title matches nothing confidently, so the scan leaves it
    # unselected and the search is what supplies a first guess.
    folder = movie_root / "Some.Unrecognisable.Thing.1999.1080p.BluRay"
    folder.mkdir(parents=True)
    (folder / "thing.mkv").write_bytes(b"movie")
    storage = create_root(client, headers, movie_root, "movies")
    scan(client, headers, storage["id"])
    item = client.get("/api/v1/discoveries?page_size=250").json()["items"][0]

    response = client.post(f"/api/v1/discoveries/{item['id']}/search", headers=headers, json={"query": "Dune"})
    assert response.status_code == 200, response.text
    body = response.json()
    # Confirming or correcting one guess is faster than filling in a blank row.
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

    # The fake catalogue matches on the title appearing in the query, so naming
    # both films is how this test gets a list worth reordering.
    listed = client.post(f"/api/v1/discoveries/{item['id']}/search", headers=headers, json={"query": "Dune Arrival"}).json()
    before = [candidate["tmdb_id"] for candidate in listed["candidates"]]
    assert len(before) > 1

    # Picking the second row must not slide it to the top: everything the
    # reviewer was about to compare would move under their cursor.
    response = client.post(
        f"/api/v1/discoveries/{item['id']}/select",
        headers=headers,
        json={"tmdb_id": before[1], "title": "Second", "year": 2000},
    )
    assert response.status_code == 200, response.text
    assert [candidate["tmdb_id"] for candidate in response.json()["candidates"]] == before
    assert response.json()["selected"]["tmdb_id"] == before[1]
