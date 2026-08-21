import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import downloads as downloads_api
from app.api import indexers as indexers_api
from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.torznab import SearchResult
from app.main import create_app
from app.models.domain import IdentityState, Movie


@pytest.fixture
def client():
    db_path = tempfile.mktemp(prefix="medialogue-search-", suffix=".db", dir=os.getcwd())
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


def _login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _seed_movie() -> str:
    async def seed() -> str:
        async with db_session.async_session_factory() as db:
            movie = Movie(
                title="Inception",
                sort_title="Inception",
                year=2010,
                tmdb_id=27205,
                identity_state=IdentityState.MATCHED,
            )
            db.add(movie)
            await db.commit()
            return str(movie.tmdb_id)

    return asyncio.run(seed())


@dataclass
class FakeTorznabBehavior:
    results_by_url: dict[str, list[SearchResult]] = field(default_factory=dict)
    errors_by_url: dict[str, str] = field(default_factory=dict)
    health_title: str = "Prowlarr Test Indexer"
    fetched: list[str] = field(default_factory=list)

    def factory(self, url: str, api_key: str, *, timeout: float = 15.0):
        return FakeTorznabClient(self, url, api_key, timeout)


class FakeTorznabClient:
    def __init__(self, behavior: FakeTorznabBehavior, url: str, api_key: str, timeout: float):
        self.behavior = behavior
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def health(self):
        error = self.behavior.errors_by_url.get(self.url)
        if error:
            raise RuntimeError(error)
        return {"status": "healthy", "title": self.behavior.health_title}

    async def search(self, query: str, *, media_type: str, tmdb_id=None, season=None, episode=None):
        error = self.behavior.errors_by_url.get(self.url)
        if error:
            raise RuntimeError(error)
        assert media_type == "movies"
        assert tmdb_id == 27205
        assert "Inception" in query
        return list(self.behavior.results_by_url.get(self.url, []))

    async def fetch_torrent(self, download_url: str) -> bytes:
        self.behavior.fetched.append(download_url)
        return b"d4:infod4:name9:Inceptionee"

    async def close(self):
        return None


@dataclass
class FakeQBitBehavior:
    submissions: list[dict] = field(default_factory=list)

    def factory(self, url: str, username: str, password: str):
        return FakeQBitClient(self, url, username, password)


class FakeQBitClient:
    def __init__(self, behavior: FakeQBitBehavior, url: str, username: str, password: str):
        self.behavior = behavior
        self.url = url
        self.username = username
        self.password = password

    async def add_torrent(self, torrent: bytes, *, filename="download.torrent", save_path=None, category=None, tags=()):
        self.behavior.submissions.append(
            {
                "torrent": torrent,
                "filename": filename,
                "save_path": save_path,
                "category": category,
                "tags": tuple(tags),
            }
        )

    async def add_url(self, url: str, *, save_path=None, category=None, tags=()):
        self.behavior.submissions.append({"url": url, "save_path": save_path, "category": category, "tags": tuple(tags)})

    async def close(self):
        return None


def _create_indexer(client: TestClient, headers: dict[str, str], *, name: str, url: str, scope: str = "both") -> dict:
    response = client.post(
        "/api/v1/indexers",
        headers=headers,
        json={
            "name": name,
            "torznab_url": url,
            "api_key": f"secret-{name}",
            "scope": scope,
            "enabled": True,
            "timeout_seconds": 15,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_indexer_crud_redacts_api_key_and_tests_connection(client: TestClient) -> None:
    headers = _login(client)
    behavior = FakeTorznabBehavior()
    client.app.dependency_overrides[indexers_api.get_torznab_client_factory] = lambda: behavior.factory
    try:
        indexer = _create_indexer(client, headers, name="Movies + TV", url="http://prowlarr.test/1/api")
        assert indexer["scope"] == "both"
        assert indexer["api_key_configured"] is True
        assert "api_key" not in indexer
        assert "secret-Movies + TV" not in str(indexer)

        tested = client.post(f"/api/v1/indexers/{indexer['id']}/test", headers=headers)
        assert tested.status_code == 200, tested.text
        assert tested.json()["status"] == "healthy"
        assert tested.json()["title"] == behavior.health_title

        listing = client.get("/api/v1/indexers").json()["items"]
        assert listing[0]["health"] == "healthy"
        assert listing[0]["api_key_configured"] is True

        updated = client.patch(
            f"/api/v1/indexers/{indexer['id']}",
            headers=headers,
            json={"name": "Renamed", "api_key": "", "expected_revision": indexer["revision"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "Renamed"
        assert updated.json()["api_key_configured"] is True
    finally:
        client.app.dependency_overrides.clear()


def test_movie_search_fans_out_preserves_partial_results_and_parses_release_names(client: TestClient) -> None:
    headers = _login(client)
    movie_resource = _seed_movie()
    good = "http://prowlarr.test/1/api"
    failed = "http://prowlarr.test/2/api"
    _create_indexer(client, headers, name="Healthy", url=good, scope="movies")
    _create_indexer(client, headers, name="Broken", url=failed, scope="both")
    custom_format = client.post(
        "/api/v1/custom-formats",
        headers=headers,
        json={
            "name": "Hybrid REMUX",
            "media_scope": "movies",
            "conditions": [
                {"type": "quality_modifier", "value": "REMUX", "required": True},
                {"type": "release_attribute", "value": "Hybrid", "required": True},
            ],
        },
    )
    assert custom_format.status_code == 201, custom_format.text
    custom_format_id = custom_format.json()["id"]
    behavior = FakeTorznabBehavior(
        results_by_url={
            good: [
                SearchResult(
                    guid="result-1",
                    title="Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM",
                    download_url="http://prowlarr.test/download/1",
                    size=67_100_000_000,
                    seeders=42,
                    published="Wed, 19 Aug 2026 12:00:00 +0000",
                ),
                SearchResult(
                    guid="result-2",
                    title="Inception 2010 1080p BluRay REMUX AVC DTS-HD MA 5.1-GROUP",
                    download_url="http://prowlarr.test/download/2",
                    size=31_000_000_000,
                    seeders=15,
                    published="Wed, 19 Aug 2026 10:00:00 +0000",
                ),
            ]
        },
        errors_by_url={failed: "indexer offline"},
    )
    client.app.dependency_overrides[indexers_api.get_torznab_client_factory] = lambda: behavior.factory
    try:
        started = client.post(f"/api/v1/movies/{movie_resource}/interactive-search", headers=headers)
        assert started.status_code == 202, started.text
        job = client.get(f"/api/v1/search-jobs/{started.json()['job_id']}")
        assert job.status_code == 200, job.text
        payload = job.json()
        assert payload["status"] == "completed"
        assert payload["result_total"] == 2
        states = {item["name"]: item["status"] for item in payload["indexers"]}
        assert states == {"Healthy": "completed", "Broken": "failed"}
        first = next(item for item in payload["results"] if item["title"].startswith("Inception 2010 Hybrid"))
        assert first["quality"] == "2160p BluRay REMUX"
        assert first["edition"] is None
        assert first["release_group"] == "LM"
        # Part 12 scores every result under the target's frozen profile
        # snapshot. Without an assigned profile, matching formats contribute 0.
        assert first["custom_format_score"] == 0
        assert first["custom_format_snapshot"]["score_evaluated"] is True
        assert first["quality_profile_id"] is None
        assert first["custom_format_snapshot"]["matched_format_ids"] == [custom_format_id]
        match = first["custom_format_snapshot"]["formats"][0]
        assert match["matched"] is True
        assert "score" not in match
        assert "configured_score" not in match
        assert len(match["conditions"]) == 2
        assert first["size"] == 67_100_000_000
        assert "download_url" not in first

        # Editing a Custom Format later must not rewrite search-time evidence.
        current = client.get(f"/api/v1/custom-formats/{custom_format_id}").json()
        edited = client.patch(
            f"/api/v1/custom-formats/{custom_format_id}",
            headers=headers,
            json={
                "conditions": [{"type": "release_attribute", "value": "REPACK", "required": True}],
                "expected_revision": current["revision"],
            },
        )
        assert edited.status_code == 200, edited.text
        unchanged = client.get(f"/api/v1/search-jobs/{started.json()['job_id']}").json()["results"]
        same_first = next(item for item in unchanged if item["id"] == first["id"])
        assert same_first["custom_format_snapshot"]["matched_format_ids"] == [custom_format_id]

        health = client.get("/api/v1/integrations/health").json()["indexers"]
        assert health["status"] == "degraded"
        assert health["healthy"] == 1
    finally:
        client.app.dependency_overrides.clear()


def test_selected_search_result_submits_to_eligible_qbit_and_is_idempotent(client: TestClient) -> None:
    headers = _login(client)
    movie_resource = _seed_movie()
    indexer_url = "http://prowlarr.test/1/api"
    _create_indexer(client, headers, name="Healthy", url=indexer_url, scope="movies")
    behavior = FakeTorznabBehavior(
        results_by_url={
            indexer_url: [
                SearchResult(
                    guid="result-1",
                    title="Inception 2010 2160p UHD BluRay REMUX HEVC TrueHD 7.1-LM",
                    download_url="http://prowlarr.test/download/1",
                    size=60_000_000_000,
                    seeders=33,
                    published=None,
                )
            ]
        }
    )
    qbit = FakeQBitBehavior()
    client.app.dependency_overrides[indexers_api.get_torznab_client_factory] = lambda: behavior.factory
    client.app.dependency_overrides[downloads_api.get_qbit_client_factory] = lambda: qbit.factory
    try:
        qbit_client = client.post(
            "/api/v1/download-clients",
            headers=headers,
            json={
                "name": "qbit-movies",
                "url": "http://qbit.test:8080",
                "username": "media",
                "password": "secret",
                "scope": "movies",
                "category": "movies",
                "tags": ["managed"],
                "enabled": True,
            },
        ).json()
        show_client = client.post(
            "/api/v1/download-clients",
            headers=headers,
            json={
                "name": "qbit-shows",
                "url": "http://qbit-shows.test:8080",
                "username": "media",
                "password": "secret",
                "scope": "shows",
                "enabled": True,
            },
        ).json()
        started = client.post(f"/api/v1/movies/{movie_resource}/interactive-search", headers=headers).json()
        result = client.get(f"/api/v1/search-jobs/{started['job_id']}").json()["results"][0]

        client.put("/api/v1/operations", headers=headers, json={"enabled": True})
        wrong = client.post(
            f"/api/v1/search-results/{result['id']}/download",
            headers=headers,
            json={"download_client_id": show_client["id"]},
        )
        assert wrong.status_code == 409, wrong.text
        assert wrong.json()["error"]["code"] == "DOWNLOAD_CLIENT_SCOPE_MISMATCH"

        submitted = client.post(
            f"/api/v1/search-results/{result['id']}/download",
            headers=headers,
            json={"download_client_id": qbit_client["id"]},
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["status"] == "submitted"
        assert len(qbit.submissions) == 1
        assert qbit.submissions[0]["category"] == "movies"
        assert qbit.submissions[0]["tags"] == ("managed",)
        assert qbit.submissions[0]["save_path"] is None, "Search must not relocate downloads; qBit/client defaults decide the save path."
        assert behavior.fetched == ["http://prowlarr.test/download/1"]

        repeat = client.post(
            f"/api/v1/search-results/{result['id']}/download",
            headers=headers,
            json={"download_client_id": qbit_client["id"]},
        )
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["status"] == "already_submitted"
        assert len(qbit.submissions) == 1

        persisted = client.get(f"/api/v1/search-jobs/{started['job_id']}").json()["results"][0]
        assert persisted["selected_at"] is not None
        assert persisted["selected_download_client_id"] == qbit_client["id"]
    finally:
        client.app.dependency_overrides.clear()
