import asyncio
import os
import tempfile
import shutil
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.base import Base
from app.db import session as db_session
from app.main import create_app
from app.integrations.tmdb import TMDBEpisodeMetadata, TMDBMovieMatch, TMDBSeasonMetadata, TMDBShowDetails, TMDBShowMatch


@pytest.fixture
def client():
    db_path = tempfile.mktemp(prefix="medialogue-api-", suffix=".db", dir=os.getcwd())
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(database_url=database_url, bootstrap_admin=True, config_dir=f"{db_path}.config", secret_key="test-secret-key-123456")
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


class FakeTMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def health(self):
        return {"status": "healthy"}

    async def search_movie(self, title: str, year: int | None = None):
        stable_id = 27205 if title.casefold() == "inception" else 329865 if title.casefold() == "arrival" else 900000
        return [TMDBMovieMatch(stable_id, title, title, year, None, None)]

    async def search_show(self, title: str, year: int | None = None):
        stable_id = 194764 if title.casefold() == "dollface" else 200000
        return [TMDBShowMatch(stable_id, title, title, year, f"Overview for {title}", None)]

    async def get_show(self, tmdb_id: int):
        return TMDBShowDetails(
            tmdb_id=tmdb_id,
            title="Dollface",
            original_title="Dollface",
            year=2019,
            overview="A test show.",
            poster_path=None,
            tvdb_id=361563,
            seasons=(TMDBSeasonMetadata(1, "Season 1", 2),),
        )

    async def get_season(self, tmdb_id: int, season_number: int):
        return [
            TMDBEpisodeMetadata(1001, season_number, 1, "Episode One", None, "One"),
            TMDBEpisodeMetadata(1002, season_number, 2, "Episode Two", None, "Two"),
        ]

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def fake_tmdb(monkeypatch):
    import app.services.tmdb as tmdb_service

    monkeypatch.setattr(tmdb_service, "TMDBClient", FakeTMDBClient)


def _configure_tmdb(client: TestClient, headers: dict[str, str]) -> None:
    response = client.put(
        "/api/v1/integrations/tmdb",
        headers=headers,
        json={"api_key": "test-key", "enabled": True},
    )
    assert response.status_code == 200, response.text


def _wait_job(client: TestClient, job_id: str, *, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    payload: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"job did not reach a terminal state within {timeout}s: {payload}")


def _scan(client: TestClient, headers: dict[str, str], root_id: str, *, timeout: float = 5.0) -> dict:
    response = client.post(f"/api/v1/storage-roots/{root_id}/scan", headers=headers)
    assert response.status_code == 202, response.text
    return _wait_job(client, response.json()["job_id"], timeout=timeout)


def test_login_session_and_csrf(client: TestClient) -> None:
    unauthenticated = client.get("/api/v1/auth/me")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    assert login.json()["default_password_warning"] is True

    storage_path = str(Path.cwd() / "media" / "movies")
    blocked = client.post("/api/v1/storage-roots", json={"name": "Movies", "path": storage_path, "media_type": "movies"})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "CSRF_INVALID"

    created = client.post(
        "/api/v1/storage-roots",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Movies", "path": storage_path, "media_type": "movies"},
    )
    assert created.status_code == 201


def test_error_envelope_serializes_validation_context(client: TestClient) -> None:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    invalid = client.post(
        "/api/v1/storage-roots",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={"name": "Movies", "path": "relative", "media_type": "movies"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


def test_parser_endpoint_and_operations_are_always_available(client: TestClient) -> None:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    csrf = login.json()["csrf_token"]

    operations = client.get("/api/v1/operations")
    assert operations.status_code == 200
    assert operations.json() == {"enabled": True}

    parsed = client.post(
        "/api/v1/parser/test",
        json={"name": "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM"},
    )
    assert parsed.status_code == 200
    assert parsed.json()["quality"]["canonical"] == "2160p BluRay REMUX"
    assert parsed.json()["edition"] is None
    assert parsed.json()["attributes"]["hybrid"] is True

    enabled = client.put(
        "/api/v1/operations",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json() == {"enabled": True}


def test_movie_root_scan_is_idempotent_and_preserves_missing_history(client: TestClient) -> None:
    fixture_root = Path.cwd() / f"scan-fixture-{uuid.uuid4().hex}"
    release_name = "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM"
    release_dir = fixture_root / release_name
    release_dir.mkdir(parents=True)
    (release_dir / f"{release_name}.mkv").write_bytes(b"test")
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
        csrf = login.json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        _configure_tmdb(client, headers)
        client.put("/api/v1/operations", headers=headers, json={"enabled": True})
        created = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "Scan Movies", "path": str(fixture_root), "media_type": "movies"},
        )
        assert created.status_code == 201
        root_id = created.json()["id"]

        job = _scan(client, headers, root_id)
        assert job["status"] == "completed", job
        assert job["summary"]["matched"] == 1

        library = client.get("/api/v1/movies")
        assert library.status_code == 200
        assert library.json()["total"] == 1
        movie = library.json()["items"][0]
        assert movie["title"] == "Inception"
        assert movie["tmdb_id"] == 27205
        assert movie["resource_id"] == "27205"
        assert movie["current_quality"] == "2160p BluRay REMUX"
        assert movie["edition"] is None  # Hybrid is an attribute, never an edition.
        assert movie["state"] == "Present"

        second_job = _scan(client, headers, root_id)
        assert second_job["status"] == "completed", second_job
        assert client.get("/api/v1/movies").json()["total"] == 1

        shutil.rmtree(release_dir)
        _scan(client, headers, root_id)
        assert client.get("/api/v1/movies").json()["items"][0]["state"] == "Present"
        _scan(client, headers, root_id)
        missing = client.get("/api/v1/movies?state=missing").json()
        assert missing["total"] == 1
        detail = client.get(f"/api/v1/movies/{movie['id']}").json()
        assert detail["releases"][0]["state"] == "missing"
        assert detail["releases"][0]["directories"][0]["exists"] is False

        replacement_name = "Inception 2010 Directors Cut 2160p UHD BluRay REMUX HEVC TrueHD 7.1-GROUP"
        replacement_dir = fixture_root / replacement_name
        replacement_dir.mkdir()
        (replacement_dir / f"{replacement_name}.mkv").write_bytes(b"replacement")
        replacement_job = _scan(client, headers, root_id)
        assert replacement_job["status"] == "completed", replacement_job
        replaced = client.get(f"/api/v1/movies/{movie['id']}").json()
        assert replaced["state"] == "Present"
        assert {release["state"] for release in replaced["releases"]} == {"current", "replaced"}
        current = next(release for release in replaced["releases"] if release["state"] == "current")
        assert current["edition"] == "Director's Cut"
    finally:
        if fixture_root.exists():
            shutil.rmtree(fixture_root)


def test_scan_flags_two_present_same_edition_releases_as_duplicate(client: TestClient) -> None:
    fixture_root = Path.cwd() / f"duplicate-fixture-{uuid.uuid4().hex}"
    names = [
        "Arrival 2016 1080p BluRay REMUX AVC DTS-HD MA 5.1-GROUPA",
        "Arrival 2016 2160p UHD BluRay REMUX HEVC TrueHD 7.1-GROUPB",
    ]
    for name in names:
        directory = fixture_root / name
        directory.mkdir(parents=True)
        (directory / f"{name}.mkv").write_bytes(b"test")
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
        csrf = login.json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        _configure_tmdb(client, headers)
        client.put("/api/v1/operations", headers=headers, json={"enabled": True})
        root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "Duplicates", "path": str(fixture_root), "media_type": "movies"},
        ).json()
        job = _scan(client, headers, root["id"])
        assert job["status"] == "completed", job
        assert job["summary"]["duplicates"] == 1
        movie = client.get("/api/v1/movies").json()["items"][0]
        assert movie["state"] == "Duplicate"
        assert movie["release_count"] == 2
        problems = client.get("/api/v1/problems?reason=DUPLICATE_PHYSICAL_RELEASE").json()
        assert problems["total"] == 1
    finally:
        if fixture_root.exists():
            shutil.rmtree(fixture_root)


def test_new_scan_requires_tmdb_identity_before_automatic_add(client: TestClient) -> None:
    fixture_root = Path.cwd() / f"tmdb-required-{uuid.uuid4().hex}"
    release_name = "Inception 2010 1080p BluRay REMUX AVC DTS-HD MA 5.1-GROUP"
    release_dir = fixture_root / release_name
    release_dir.mkdir(parents=True)
    (release_dir / f"{release_name}.mkv").write_bytes(b"test")
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        client.put("/api/v1/operations", headers=headers, json={"enabled": True})
        root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "TMDB required", "path": str(fixture_root), "media_type": "movies"},
        ).json()
        job = _scan(client, headers, root["id"])
        assert job["status"] == "completed", job
        assert client.get("/api/v1/movies").json()["total"] == 0
        problems = client.get("/api/v1/problems?reason=TMDB_MATCH_REQUIRED").json()
        assert problems["total"] == 1
    finally:
        if fixture_root.exists():
            shutil.rmtree(fixture_root)


def test_show_root_scan_tracks_episode_presence_independently(client: TestClient) -> None:
    fixture_root = Path.cwd() / f"show-scan-fixture-{uuid.uuid4().hex}"
    show_dir = fixture_root / "Dollface 2019"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    episode1 = season_dir / "Dollface S01E01 2160p DSNP WEB-DL DD+ 5.1 DV HDR H.265-HONE.mkv"
    episode2 = season_dir / "Dollface S01E02 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE.mkv"
    episode1.write_bytes(b"episode-one")
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        _configure_tmdb(client, headers)
        client.put("/api/v1/operations", headers=headers, json={"enabled": True})
        root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "Scan Shows", "path": str(fixture_root), "media_type": "shows", "missing_grace_checks": 2},
        )
        assert root.status_code == 201, root.text

        job = _scan(client, headers, root.json()["id"])
        assert job["status"] == "completed", job
        assert job["summary"]["matched"] == 1

        library = client.get("/api/v1/shows")
        assert library.status_code == 200, library.text
        assert library.json()["total"] == 1
        show = library.json()["items"][0]
        assert show["title"] == "Dollface"
        assert show["tmdb_id"] == 194764
        assert show["tvdb_id"] == 361563
        assert show["episode_count"] == 2
        assert show["episodes_present"] == 1
        assert show["episodes_missing"] == 1
        assert show["state"] == "Missing"

        detail = client.get(f"/api/v1/shows/{show['resource_id']}").json()
        assert len(detail["seasons"]) == 1
        episodes = detail["seasons"][0]["episodes"]
        assert [(item["episode_number"], item["presence_state"]) for item in episodes] == [(1, "present"), (2, "missing")]
        first = episodes[0]
        assert first["quality"] == "2160p WEB-DL"
        assert first["media"][0]["path"].endswith(episode1.name)

        episode2.write_bytes(b"episode-two")
        _scan(client, headers, root.json()["id"])
        complete = client.get(f"/api/v1/shows/{show['resource_id']}").json()
        assert complete["state"] == "Present"
        assert complete["episodes_present"] == 2
        assert complete["episodes_missing"] == 0

        # Individual-file disappearance uses the same configured grace count.
        episode1.unlink()
        _scan(client, headers, root.json()["id"])
        grace = client.get(f"/api/v1/shows/{show['resource_id']}").json()
        grace_e1 = next(item for item in grace["seasons"][0]["episodes"] if item["episode_number"] == 1)
        assert grace_e1["presence_state"] == "present"
        _scan(client, headers, root.json()["id"])
        missing = client.get(f"/api/v1/shows/{show['resource_id']}").json()
        missing_e1 = next(item for item in missing["seasons"][0]["episodes"] if item["episode_number"] == 1)
        assert missing_e1["presence_state"] == "missing"
        assert missing["state"] == "Missing"

        # Episode monitoring is independently editable and revision protected.
        episode_row = next(item for item in missing["seasons"][0]["episodes"] if item["episode_number"] == 2)
        updated = client.patch(
            f"/api/v1/episodes/{episode_row['id']}",
            headers=headers,
            json={"monitored": False, "expected_revision": episode_row["revision"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["monitored"] is False
        stale = client.patch(
            f"/api/v1/episodes/{episode_row['id']}",
            headers=headers,
            json={"monitored": True, "expected_revision": episode_row["revision"]},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
    finally:
        if fixture_root.exists():
            shutil.rmtree(fixture_root)


def test_show_scan_maps_multi_episode_and_flags_episode_less_video(client: TestClient) -> None:
    fixture_root = Path.cwd() / f"show-pack-fixture-{uuid.uuid4().hex}"
    show_dir = fixture_root / "Dollface 2019"
    show_dir.mkdir(parents=True)
    (show_dir / "Dollface S01 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE.mkv").write_bytes(b"pack")
    (show_dir / "Dollface S01E01E02 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE.mkv").write_bytes(b"multi")
    try:
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        _configure_tmdb(client, headers)
        client.put("/api/v1/operations", headers=headers, json={"enabled": True})
        root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "Part14 Mapping", "path": str(fixture_root), "media_type": "shows"},
        ).json()
        job = _scan(client, headers, root["id"])
        assert job["status"] == "completed", job
        # Part 14 maps the valid E01E02 file immediately while isolating the
        # episode-less S01 video as an unresolved member instead of rejecting
        # the whole directory.
        assert job["summary"]["matched"] == 1
        assert client.get("/api/v1/problems?reason=SEASON_PACK_MAPPING_PENDING").json()["total"] == 0
        assert client.get("/api/v1/problems?reason=MULTI_EPISODE_MAPPING_PENDING").json()["total"] == 0
        assert client.get("/api/v1/problems?reason=EPISODE_MAPPING_UNRESOLVED").json()["total"] == 1
        show = client.get("/api/v1/shows").json()["items"][0]
        assert show["episodes_present"] == 2
        assert show["episodes_missing"] == 0
    finally:
        if fixture_root.exists():
            shutil.rmtree(fixture_root)


def test_show_can_be_added_from_tmdb_and_seeds_missing_episode_inventory(client: TestClient) -> None:
    from app.api import shows as shows_api

    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    _configure_tmdb(client, headers)
    client.app.dependency_overrides[shows_api.get_tmdb_client_factory] = lambda: FakeTMDBClient
    try:
        lookup = client.get("/api/v1/shows/lookup", params={"query": "Dollface"})
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()[0]["tmdb_id"] == 194764

        created = client.post("/api/v1/shows", headers=headers, json={"tmdb_id": 194764, "monitored": True})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["tmdb_id"] == 194764
        assert body["tvdb_id"] == 361563
        assert body["season_count"] == 1
        assert body["episode_count"] == 2
        assert body["episodes_present"] == 0
        assert body["episodes_missing"] == 2
        assert body["state"] == "Missing"

        detail = client.get("/api/v1/shows/194764")
        assert detail.status_code == 200, detail.text
        assert [episode["presence_state"] for episode in detail.json()["seasons"][0]["episodes"]] == ["missing", "missing"]
    finally:
        client.app.dependency_overrides.clear()
