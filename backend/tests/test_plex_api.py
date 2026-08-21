import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import plex as plex_api
from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.plex import PlexMediaMatch, PlexTitleMatch
from app.integrations.tmdb import TMDBEpisodeMetadata, TMDBMovieMatch, TMDBSeasonMetadata, TMDBShowDetails, TMDBShowMatch
from app.main import create_app


@pytest.fixture
def client():
    """Give Plex endpoint tests their own isolated SQLite database."""

    db_path = tempfile.mktemp(prefix="medialogue-plex-", suffix=".db", dir=os.getcwd())
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(
        database_url=database_url,
        config_dir=f"{db_path}.config",
        bootstrap_admin=True,
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


class FakeTMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def health(self):
        return {"status": "healthy"}

    async def search_movie(self, title: str, year: int | None = None):
        return [TMDBMovieMatch(27205, title, title, year, None, None)]

    async def search_show(self, title: str, year: int | None = None):
        return [TMDBShowMatch(194764, title, title, year, "A test show.", None)]

    async def get_show(self, tmdb_id: int):
        return TMDBShowDetails(
            tmdb_id=tmdb_id, title="Dollface", original_title="Dollface", year=2019,
            overview="A test show.", poster_path=None, tvdb_id=361563,
            seasons=(TMDBSeasonMetadata(1, "Season 1", 1),),
        )

    async def get_season(self, tmdb_id: int, season_number: int):
        return [TMDBEpisodeMetadata(1001, season_number, 1, "Episode One", None, "One")]

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


class FakePlexClient:
    """Small read-only fake used through the Plex client dependency override."""

    def __init__(self, behavior: "FakePlexBehavior", url: str, token: str):
        self.behavior = behavior
        self.url = url
        self.token = token
        behavior.instances.append(self)

    async def health(self) -> dict[str, str]:
        if self.behavior.health_error:
            raise RuntimeError(self.behavior.health_error)
        return {"status": "healthy", "machine_identifier": self.behavior.machine_identifier}

    async def find_exact_path(self, file_path: str) -> PlexMediaMatch | None:
        self.behavior.seen_paths.append(file_path)
        return self.behavior.exact_match

    async def search_title_year(self, title: str, year: int | None) -> list[PlexTitleMatch]:
        self.behavior.searches.append((title, year))
        return list(self.behavior.title_matches)

    async def close(self) -> None:
        return None


class FakePlexBehavior:
    def __init__(
        self,
        *,
        exact_match: PlexMediaMatch | None = None,
        title_matches: list[PlexTitleMatch] | None = None,
        health_error: str | None = None,
        machine_identifier: str = "fake-machine",
    ):
        self.exact_match = exact_match
        self.title_matches = title_matches or []
        self.health_error = health_error
        self.machine_identifier = machine_identifier
        self.instances: list[FakePlexClient] = []
        self.seen_paths: list[str] = []
        self.searches: list[tuple[str, int | None]] = []

    def factory(self, url: str, token: str) -> FakePlexClient:
        return FakePlexClient(self, url, token)


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


def _scan(client: TestClient, headers: dict[str, str], root_id: str) -> dict:
    response = client.post(f"/api/v1/storage-roots/{root_id}/scan", headers=headers)
    assert response.status_code == 202, response.text
    return _wait_job(client, response.json()["job_id"])


def _recheck_movie(client: TestClient, headers: dict[str, str], movie_id: str) -> dict:
    response = client.post(
        f"/api/v1/movies/{movie_id}/actions/recheck-plex",
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return _wait_job(client, response.json()["job_id"])


def _recheck_show(client: TestClient, headers: dict[str, str], show_id: str) -> dict:
    response = client.post(
        f"/api/v1/shows/{show_id}/actions/recheck-plex",
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return _wait_job(client, response.json()["job_id"])


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "adminadmin"},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _configure_plex(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.put(
        "/api/v1/integrations/plex",
        headers=headers,
        json={
            "url": "http://plex.test:32400",
            "token": "secret-plex-token",
            "enabled": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def movie_context(client: TestClient):
    fixture_root = Path.cwd() / f"plex-fixture-{os.urandom(8).hex()}"
    release_name = "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM"
    release_dir = fixture_root / release_name
    release_dir.mkdir(parents=True)
    (release_dir / f"{release_name}.mkv").write_bytes(b"plex-test")

    headers = _login(client)
    _configure_tmdb(client, headers)
    enabled = client.put(
        "/api/v1/operations",
        headers=headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    root = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={
            "name": "Plex Movies",
            "path": str(fixture_root),
            "media_type": "movies",
        },
    )
    assert root.status_code == 201, root.text
    job = _scan(client, headers, root.json()["id"])
    assert job["status"] == "completed", job
    movie = client.get("/api/v1/movies").json()["items"][0]

    try:
        yield client, headers, movie
    finally:
        if fixture_root.exists():
            shutil.rmtree(fixture_root)


def test_plex_configuration_persists_without_returning_token(client: TestClient) -> None:
    headers = _login(client)

    initial = client.get("/api/v1/integrations/plex", headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["configured"] is False

    saved = _configure_plex(client, headers)
    assert saved["configured"] is True
    assert saved["token_configured"] is True
    assert "secret-plex-token" not in saved
    assert "token" not in saved

    fetched = client.get("/api/v1/integrations/plex", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["url"].startswith("http://plex.test:32400")
    assert fetched.json()["token_configured"] is True
    assert "secret-plex-token" not in fetched.text


def test_plex_connection_test_uses_read_only_client_override(client: TestClient) -> None:
    headers = _login(client)
    behavior = FakePlexBehavior(machine_identifier="machine-test")
    client.app.dependency_overrides[plex_api.get_plex_client_factory] = lambda: behavior.factory
    try:
        response = client.post(
            "/api/v1/integrations/plex/test",
            headers=headers,
            json={"url": "http://plex.test:32400", "token": "test-token"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "healthy"
    assert response.json()["machine_identifier"] == "machine-test"
    assert response.json()["latency_ms"] is not None
    assert len(behavior.instances) == 1
    assert behavior.instances[0].token == "test-token"


def test_plex_recheck_exact_path_match(client: TestClient, movie_context) -> None:
    client, headers, movie = movie_context
    _configure_plex(client, headers)
    behavior = FakePlexBehavior(
        exact_match=PlexMediaMatch(
            rating_key="plex-10",
            title="Inception",
            year=2010,
            edition=None,
            file_path="/plex/movies/Inception/movie.mkv",
        )
    )
    client.app.dependency_overrides[plex_api.get_plex_client_factory] = lambda: behavior.factory
    try:
        job = _recheck_movie(client, headers, movie["id"])
    finally:
        client.app.dependency_overrides.clear()

    assert job["status"] == "completed"
    assert job["summary"]["state"] == "matched"
    assert job["summary"]["matched_releases"] == 1
    assert behavior.seen_paths
    detail = client.get(f"/api/v1/movies/{movie['id']}")
    assert detail.status_code == 200
    assert detail.json()["plex_state"] == "matched"
    assert any(event["type"] == "plex.matched" for event in detail.json()["recent_events"])


def test_plex_recheck_exact_path_ignores_plex_title_and_year_metadata(client: TestClient, movie_context) -> None:
    client, headers, movie = movie_context
    _configure_plex(client, headers)
    behavior = FakePlexBehavior(
        exact_match=PlexMediaMatch(
            rating_key="plex-wrong",
            title="The Wrong Movie",
            year=2001,
            edition=None,
            file_path="/plex/movies/Inception/movie.mkv",
        )
    )
    client.app.dependency_overrides[plex_api.get_plex_client_factory] = lambda: behavior.factory
    try:
        job = _recheck_movie(client, headers, movie["id"])
    finally:
        client.app.dependency_overrides.clear()

    assert job["status"] == "completed"
    assert job["summary"]["state"] == "matched"
    assert job["summary"]["matched_releases"] == 1
    assert job["summary"]["conflict_releases"] == 0
    detail = client.get(f"/api/v1/movies/{movie['id']}").json()
    assert detail["plex_state"] == "matched"
    assert detail["problem_count"] == 0
    problems = client.get("/api/v1/problems?reason=PLEX_IDENTITY_MISMATCH&status=open")
    assert problems.status_code == 200
    assert problems.json()["total"] == 0


def test_plex_exact_path_stays_matched_when_plex_metadata_changes(
    client: TestClient, movie_context
) -> None:
    client, headers, movie = movie_context
    _configure_plex(client, headers)
    behavior = FakePlexBehavior(
        exact_match=PlexMediaMatch(
            rating_key="plex-first",
            title="Completely Different Plex Title",
            year=1970,
            edition=None,
            file_path="/plex/movies/Inception/movie.mkv",
        )
    )
    client.app.dependency_overrides[plex_api.get_plex_client_factory] = lambda: behavior.factory
    try:
        first = _recheck_movie(client, headers, movie["id"])
        assert first["summary"]["state"] == "matched"

        behavior.exact_match = PlexMediaMatch(
            rating_key="plex-second",
            title="Yet Another Plex Name",
            year=1999,
            edition=None,
            file_path="/plex/movies/Inception/movie.mkv",
        )
        second = _recheck_movie(client, headers, movie["id"])
        assert second["summary"]["state"] == "matched"
        detail = client.get(f"/api/v1/movies/{movie['id']}").json()
        assert detail["plex_state"] == "matched"
        assert detail["problem_count"] == 0
        open_problems = client.get(
            "/api/v1/problems?reason=PLEX_IDENTITY_MISMATCH&status=open"
        )
        assert open_problems.status_code == 200
        assert open_problems.json()["total"] == 0
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("title_matches", "expected_state", "expected_matched", "expected_not_found", "expected_multiple"),
    [
        (
            [PlexTitleMatch(rating_key="plex-11", title="Inception", year=2010, edition=None)],
            "matched",
            1,
            0,
            0,
        ),
        ([], "not_found", 0, 1, 0),
        (
            [
                PlexTitleMatch(rating_key="plex-11", title="Inception", year=2010, edition=None),
                PlexTitleMatch(rating_key="plex-12", title="Inception", year=2010, edition="IMAX"),
            ],
            "multiple_versions",
            0,
            0,
            1,
        ),
    ],
)
def test_plex_recheck_falls_back_to_title_year_with_explicit_completed_states(
    client: TestClient,
    movie_context,
    title_matches: list[PlexTitleMatch],
    expected_state: str,
    expected_matched: int,
    expected_not_found: int,
    expected_multiple: int,
) -> None:
    client, headers, movie = movie_context
    _configure_plex(client, headers)
    behavior = FakePlexBehavior(title_matches=title_matches)
    client.app.dependency_overrides[plex_api.get_plex_client_factory] = lambda: behavior.factory
    try:
        job = _recheck_movie(client, headers, movie["id"])
    finally:
        client.app.dependency_overrides.clear()

    assert job["status"] == "completed"
    assert job["summary"]["state"] == expected_state
    assert job["summary"]["matched_releases"] == expected_matched
    assert job["summary"]["not_found_releases"] == expected_not_found
    assert job["summary"]["multiple_version_releases"] == expected_multiple
    assert behavior.searches == [("Inception", 2010)]
    assert client.get(f"/api/v1/movies/{movie['id']}").json()["plex_state"] == expected_state


def test_plex_recheck_marks_observation_unavailable_when_health_fails(
    client: TestClient, movie_context
) -> None:
    client, headers, movie = movie_context
    _configure_plex(client, headers)
    behavior = FakePlexBehavior(health_error="Plex is offline")
    client.app.dependency_overrides[plex_api.get_plex_client_factory] = lambda: behavior.factory
    try:
        job = _recheck_movie(client, headers, movie["id"])
    finally:
        client.app.dependency_overrides.clear()

    assert job["status"] == "completed"
    assert job["summary"]["state"] == "unavailable"
    assert job["summary"]["checked_releases"] == 1
    assert job["summary"]["matched_releases"] == 0
    assert client.get(f"/api/v1/movies/{movie['id']}").json()["plex_state"] == "unavailable"

    health = client.get("/api/v1/integrations/plex", headers=headers)
    assert health.status_code == 200
    assert health.json()["health"] == "unavailable"
    assert health.json()["last_error"] == "Plex is offline"


def test_initial_scan_persists_plex_exact_path_verification(client: TestClient, monkeypatch) -> None:
    fixture_root = Path.cwd() / f"plex-auto-verify-{os.urandom(8).hex()}"
    release_name = "Inception 2010 2160p UHD BluRay REMUX HEVC TrueHD 7.1-GROUP"
    release_dir = fixture_root / release_name
    release_dir.mkdir(parents=True)
    media_path = release_dir / f"{release_name}.mkv"
    media_path.write_bytes(b"plex-auto")

    headers = _login(client)
    _configure_tmdb(client, headers)
    _configure_plex(client, headers)
    behavior = FakePlexBehavior(
        exact_match=PlexMediaMatch(
            rating_key="plex-auto",
            title="Inception",
            year=2010,
            edition=None,
            file_path=str(media_path),
        )
    )
    import app.services.reconciliation as reconciliation_service

    monkeypatch.setattr(reconciliation_service, "PlexClient", behavior.factory)
    try:
        client.put("/api/v1/operations", headers=headers, json={"enabled": True})
        root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "Plex auto", "path": str(fixture_root), "media_type": "movies"},
        ).json()
        job = _scan(client, headers, root["id"])
        assert job["status"] == "completed", job
        movie = client.get("/api/v1/movies").json()["items"][0]
        assert movie["plex_state"] == "matched"
        detail = client.get(f"/api/v1/movies/{movie['resource_id']}").json()
        assert any(event["type"] == "plex.verified" for event in detail["recent_events"])
    finally:
        if fixture_root.exists():
            shutil.rmtree(fixture_root)


def test_plex_recheck_show_exact_episode_path_match(client: TestClient) -> None:
    fixture_root = Path.cwd() / f"plex-show-fixture-{os.urandom(8).hex()}"
    show_dir = fixture_root / "Dollface 2019" / "Season 01"
    show_dir.mkdir(parents=True)
    episode = show_dir / "Dollface S01E01 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE.mkv"
    episode.write_bytes(b"plex-show-test")
    headers = _login(client)
    _configure_tmdb(client, headers)
    client.put("/api/v1/operations", headers=headers, json={"enabled": True})
    root = client.post(
        "/api/v1/storage-roots", headers=headers,
        json={"name": "Plex Shows", "path": str(fixture_root), "media_type": "shows"},
    )
    assert root.status_code == 201, root.text
    job = _scan(client, headers, root.json()["id"])
    assert job["status"] == "completed", job
    show = client.get("/api/v1/shows").json()["items"][0]
    _configure_plex(client, headers)
    behavior = FakePlexBehavior(
        exact_match=PlexMediaMatch(
            rating_key="plex-show-1", title="Episode One", year=2019, edition=None,
            file_path=str(episode), show_title="Dollface", season_number=1, episode_number=1,
        )
    )
    client.app.dependency_overrides[plex_api.get_plex_client_factory] = lambda: behavior.factory
    try:
        job = _recheck_show(client, headers, show["resource_id"])
    finally:
        client.app.dependency_overrides.clear()
        shutil.rmtree(fixture_root, ignore_errors=True)
    assert job["status"] == "completed"
    assert job["summary"]["show_id"] == show["id"]
    assert job["summary"]["state"] == "matched"
    assert job["summary"]["matched_releases"] == 1
    assert "movie_id" not in job["summary"]


def test_plex_recheck_show_ignores_show_title_metadata_when_episode_numbers_match(client: TestClient) -> None:
    fixture_root = Path.cwd() / f"plex-show-title-fixture-{os.urandom(8).hex()}"
    show_dir = fixture_root / "Dollface 2019" / "Season 01"
    show_dir.mkdir(parents=True)
    episode = show_dir / "Dollface S01E01 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE.mkv"
    episode.write_bytes(b"plex-show-title-test")
    headers = _login(client)
    _configure_tmdb(client, headers)
    client.put("/api/v1/operations", headers=headers, json={"enabled": True})
    root_response = client.post(
        "/api/v1/storage-roots", headers=headers,
        json={"name": "Plex Shows title advisory", "path": str(fixture_root), "media_type": "shows"},
    )
    assert root_response.status_code == 201, root_response.text
    job = _scan(client, headers, root_response.json()["id"])
    assert job["status"] == "completed", job
    show = client.get("/api/v1/shows").json()["items"][0]
    _configure_plex(client, headers)
    behavior = FakePlexBehavior(
        exact_match=PlexMediaMatch(
            rating_key="plex-show-title-different", title="Episode One", year=2019, edition=None,
            file_path=str(episode), show_title="A Completely Different Plex Show Name",
            season_number=1, episode_number=1,
        )
    )
    client.app.dependency_overrides[plex_api.get_plex_client_factory] = lambda: behavior.factory
    try:
        job = _recheck_show(client, headers, show["resource_id"])
    finally:
        client.app.dependency_overrides.clear()
        shutil.rmtree(fixture_root, ignore_errors=True)
    assert job["status"] == "completed"
    assert job["summary"]["state"] == "matched"
    assert job["summary"]["conflict_releases"] == 0
    problems = client.get("/api/v1/problems?reason=PLEX_IDENTITY_MISMATCH&status=open")
    assert problems.status_code == 200
    assert problems.json()["total"] == 0


def test_plex_library_snapshot_indexes_movies_and_episodes_without_scanning() -> None:
    import httpx
    from app.integrations.plex import PlexClient

    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, request.url.query.decode()))
        if request.url.path == "/library/sections":
            return httpx.Response(200, content=b'<MediaContainer><Directory key="1" type="movie"/><Directory key="2" type="show"/></MediaContainer>')
        if request.url.path == "/library/sections/1/all":
            return httpx.Response(200, content=b'''<MediaContainer><Video ratingKey="m1" title="Inception" year="2010"><Media><Part file="/movies/Inception/movie.mkv"/></Media></Video></MediaContainer>''')
        if request.url.path == "/library/sections/2/all":
            return httpx.Response(200, content=b'''<MediaContainer><Video ratingKey="e1" title="Episode One" grandparentTitle="Dollface" year="2019" parentIndex="1" index="1"><Media><Part file="/shows/Dollface/S01E01.mkv"/></Media></Video></MediaContainer>''')
        return httpx.Response(404)

    async def scenario():
        client = PlexClient("http://plex.test:32400", "token", transport=httpx.MockTransport(handler))
        try:
            snapshot = await client.library_snapshot()
        finally:
            await client.close()
        return snapshot

    snapshot = asyncio.run(scenario())
    movie = snapshot.find_exact_path("/movies/Inception/movie.mkv")
    episode = snapshot.find_exact_path("/shows/Dollface/S01E01.mkv")
    assert movie is not None and movie.title == "Inception" and movie.show_title is None
    assert episode is not None and episode.show_title == "Dollface"
    assert episode.season_number == 1 and episode.episode_number == 1
    assert [item.title for item in snapshot.search_title_year("Inception", 2010)] == ["Inception"]
    assert requests == [
        ("/library/sections", ""),
        ("/library/sections/1/all", "type=1"),
        ("/library/sections/2/all", "type=4"),
    ]
