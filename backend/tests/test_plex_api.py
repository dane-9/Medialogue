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
        response = client.post(
            f"/api/v1/movies/{movie['id']}/actions/recheck-plex",
            headers=headers,
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json() == {
        "movie_id": movie["id"],
        "state": "matched",
        "checked_releases": 1,
        "matched_releases": 1,
        "conflict_releases": 0,
    }
    assert behavior.seen_paths
    detail = client.get(f"/api/v1/movies/{movie['id']}")
    assert detail.status_code == 200
    assert detail.json()["plex_state"] == "matched"
    assert any(event["type"] == "plex.matched" for event in detail.json()["recent_events"])


def test_plex_recheck_exact_path_identity_conflict_opens_problem(client: TestClient, movie_context) -> None:
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
        response = client.post(
            f"/api/v1/movies/{movie['id']}/actions/recheck-plex",
            headers=headers,
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "conflict"
    assert response.json()["conflict_releases"] == 1
    detail = client.get(f"/api/v1/movies/{movie['id']}").json()
    assert detail["plex_state"] == "conflict"
    assert detail["problem_count"] == 1
    problems = client.get("/api/v1/problems?reason=PLEX_IDENTITY_MISMATCH")
    assert problems.status_code == 200
    assert problems.json()["total"] == 1
    assert problems.json()["items"][0]["status"] == "open"


def test_plex_identity_conflict_resolves_on_agreement_and_reopens_on_later_disagreement(
    client: TestClient, movie_context
) -> None:
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
        first = client.post(
            f"/api/v1/movies/{movie['id']}/actions/recheck-plex",
            headers=headers,
        )
        assert first.status_code == 200, first.text
        assert first.json()["state"] == "conflict"
        first_detail = client.get(f"/api/v1/movies/{movie['id']}").json()
        assert first_detail["plex_state"] == "conflict"
        assert first_detail["problem_count"] == 1

        # A later exact-path agreement must update the observation and resolve
        # the identity problem without changing the local release attachment.
        behavior.exact_match = PlexMediaMatch(
            rating_key="plex-correct",
            title="Inception",
            year=2010,
            edition=None,
            file_path="/plex/movies/Inception/movie.mkv",
        )
        second = client.post(
            f"/api/v1/movies/{movie['id']}/actions/recheck-plex",
            headers=headers,
        )
        assert second.status_code == 200, second.text
        assert second.json()["state"] == "matched"
        second_detail = client.get(f"/api/v1/movies/{movie['id']}").json()
        assert second_detail["plex_state"] == "matched"
        assert second_detail["problem_count"] == 0
        resolved = client.get(
            "/api/v1/problems?reason=PLEX_IDENTITY_MISMATCH&status=resolved"
        )
        assert resolved.status_code == 200
        assert resolved.json()["total"] == 1
        assert resolved.json()["items"][0]["status"] == "resolved"

        # If Plex later reports a different identity for the same exact path,
        # the matched observation must become a conflict again and a new open
        # problem must be created.
        behavior.exact_match = PlexMediaMatch(
            rating_key="plex-wrong-again",
            title="Another Wrong Movie",
            year=1999,
            edition=None,
            file_path="/plex/movies/Inception/movie.mkv",
        )
        third = client.post(
            f"/api/v1/movies/{movie['id']}/actions/recheck-plex",
            headers=headers,
        )
        assert third.status_code == 200, third.text
        assert third.json()["state"] == "conflict"
        third_detail = client.get(f"/api/v1/movies/{movie['id']}").json()
        assert third_detail["plex_state"] == "conflict"
        assert third_detail["problem_count"] == 1
        open_problems = client.get(
            "/api/v1/problems?reason=PLEX_IDENTITY_MISMATCH&status=open"
        )
        assert open_problems.status_code == 200
        assert open_problems.json()["total"] == 1
        assert open_problems.json()["items"][0]["status"] == "open"
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("title_matches", "expected_state", "expected_matched"),
    [
        (
            [PlexTitleMatch(rating_key="plex-11", title="Inception", year=2010, edition=None)],
            "matched",
            1,
        ),
        ([], "pending", 0),
    ],
)
def test_plex_recheck_falls_back_to_title_year_or_pending(
    client: TestClient,
    movie_context,
    title_matches: list[PlexTitleMatch],
    expected_state: str,
    expected_matched: int,
) -> None:
    client, headers, movie = movie_context
    _configure_plex(client, headers)
    behavior = FakePlexBehavior(title_matches=title_matches)
    client.app.dependency_overrides[plex_api.get_plex_client_factory] = lambda: behavior.factory
    try:
        response = client.post(
            f"/api/v1/movies/{movie['id']}/actions/recheck-plex",
            headers=headers,
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["state"] == expected_state
    assert response.json()["matched_releases"] == expected_matched
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
        response = client.post(
            f"/api/v1/movies/{movie['id']}/actions/recheck-plex",
            headers=headers,
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "unavailable"
    assert response.json()["checked_releases"] == 1
    assert response.json()["matched_releases"] == 0
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
        response = client.post(f"/api/v1/shows/{show['resource_id']}/actions/recheck-plex", headers=headers)
    finally:
        client.app.dependency_overrides.clear()
        shutil.rmtree(fixture_root, ignore_errors=True)
    assert response.status_code == 200, response.text
    assert response.json()["show_id"] == show["id"]
    assert response.json()["state"] == "matched"
    assert response.json()["matched_releases"] == 1
    assert "movie_id" not in response.json()
