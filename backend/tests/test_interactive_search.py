import asyncio
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import downloads as downloads_api
from app.api import indexers as indexers_api
from app.api import tmdb as tmdb_api
from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.qbittorrent import TorrentCategory
from app.integrations.tmdb import TMDBEpisodeMetadata, TMDBMovieMatch, TMDBSeasonMetadata, TMDBShowDetails
from app.integrations.torznab import SearchResult
from app.main import create_app
from app.models.domain import IdentityState, MediaProfileOverride, Movie, Season, Show


def _format_named(payload: dict, name: str) -> dict:
    """Pick one evaluated format by name.

    Built-in formats share the evaluation list, so position is meaningless.
    """

    for item in payload.get("formats", []):
        if item.get("custom_format_name") == name or item.get("name") == name:
            return item
    raise AssertionError(f"{name} not in {[i.get('custom_format_name') or i.get('name') for i in payload.get('formats', [])]}")


def _user_formats(payload: dict) -> list[dict]:
    """Only the formats a test created; built-ins are filtered out."""

    return [item for item in payload.get("items", []) if not item.get("builtin")]



@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
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
    results_by_season: dict[int, list[SearchResult]] = field(default_factory=dict)
    errors_by_url: dict[str, str] = field(default_factory=dict)
    health_title: str = "Prowlarr Test Indexer"
    fetched: list[str] = field(default_factory=list)
    expected_media_type: str = "movies"
    expected_tmdb_id: int = 27205
    expected_query_text: str = "Inception"

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

    async def search(self, query: str, *, media_type: str, tmdb_id=None, season=None, episode=None, categories=()):
        error = self.behavior.errors_by_url.get(self.url)
        if error:
            raise RuntimeError(error)
        assert media_type == self.behavior.expected_media_type
        assert tmdb_id == self.behavior.expected_tmdb_id
        assert self.behavior.expected_query_text in query
        if season is not None and season in self.behavior.results_by_season:
            return list(self.behavior.results_by_season[season])
        return list(self.behavior.results_by_url.get(self.url, []))

    async def fetch_torrent(self, download_url: str) -> bytes:
        self.behavior.fetched.append(download_url)
        return b"d4:infod4:name9:Inceptionee"

    async def close(self):
        return None


@dataclass
class FakeQBitBehavior:
    submissions: list[dict] = field(default_factory=list)
    categories: list[TorrentCategory] = field(default_factory=lambda: [
        TorrentCategory(name="movies", save_path="/downloads/movies", resolved_save_path="/downloads/movies")
    ])

    def factory(self, url: str, username: str, password: str):
        return FakeQBitClient(self, url, username, password)


class FakeQBitClient:
    def __init__(self, behavior: FakeQBitBehavior, url: str, username: str, password: str):
        self.behavior = behavior
        self.url = url
        self.username = username
        self.password = password

    async def add_torrent(self, torrent: bytes, *, filename="download.torrent", save_path=None, category=None, tags=(), **options):
        self.behavior.submissions.append(
            {
                "torrent": torrent,
                "filename": filename,
                "save_path": save_path,
                "category": category,
                "tags": tuple(tags),
                **options,
            }
        )

    async def add_url(self, url: str, *, save_path=None, category=None, tags=(), **options):
        self.behavior.submissions.append({"url": url, "save_path": save_path, "category": category, "tags": tuple(tags), **options})

    async def list_categories(self):
        return list(self.behavior.categories)

    async def close(self):
        return None




class FakeTMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def get_movie(self, tmdb_id: int) -> TMDBMovieMatch:
        assert tmdb_id == 27205
        return TMDBMovieMatch(
            tmdb_id=27205,
            title="Inception",
            original_title="Inception",
            year=2010,
            overview="A dream within a dream.",
            poster_path="/inception.jpg",
        )

    async def get_show(self, tmdb_id: int) -> TMDBShowDetails:
        assert tmdb_id == 95396
        return TMDBShowDetails(
            tmdb_id=95396,
            title="Severance",
            original_title="Severance",
            year=2022,
            overview="Employees split their work and home memories.",
            poster_path="/severance.jpg",
            tvdb_id=371980,
            seasons=(
                TMDBSeasonMetadata(season_number=0, title="Specials", episode_count=1, air_date=date(2022, 2, 1)),
                TMDBSeasonMetadata(season_number=1, title="Season 1", episode_count=9, air_date=date(2022, 2, 18)),
                TMDBSeasonMetadata(season_number=2, title="Season 2", episode_count=10, air_date=date(2025, 1, 17)),
            ),
        )

    async def get_season(self, tmdb_id: int, season_number: int) -> list[TMDBEpisodeMetadata]:
        assert tmdb_id == 95396
        counts = {0: 1, 1: 9, 2: 10}
        return [
            TMDBEpisodeMetadata(
                tmdb_id=95396000 + season_number * 100 + episode,
                season_number=season_number,
                episode_number=episode,
                title=f"Episode {episode}",
                air_date=date(2025 if season_number == 2 else 2022, 1, min(episode, 28)),
                overview=None,
            )
            for episode in range(1, counts[season_number] + 1)
        ]

    async def close(self):
        return None


def _fake_tmdb_factory(api_key: str):
    return FakeTMDBClient(api_key)


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
        assert "download_client_id" not in indexer
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
        assert custom_format_id in first["custom_format_snapshot"]["matched_format_ids"]
        match = _format_named(first["custom_format_snapshot"], "Hybrid REMUX")
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
        assert custom_format_id in same_first["custom_format_snapshot"]["matched_format_ids"]

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


def test_unattached_movie_search_commits_only_after_explicit_qbit_download(client: TestClient) -> None:
    headers = _login(client)
    configured = client.put(
        "/api/v1/integrations/tmdb",
        headers=headers,
        json={"api_key": "test-key", "enabled": True},
    )
    assert configured.status_code == 200, configured.text

    profile = client.post(
        "/api/v1/quality-profiles",
        headers=headers,
        json={"name": "Manual Movie Search"},
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["id"]

    indexer_url = "http://prowlarr.test/manual/api"
    _create_indexer(client, headers, name="Manual", url=indexer_url, scope="movies")
    search_behavior = FakeTorznabBehavior(
        results_by_url={
            indexer_url: [
                SearchResult(
                    guid="manual-result",
                    title="Inception 2010 2160p UHD BluRay REMUX HEVC TrueHD 7.1-LM",
                    download_url="http://prowlarr.test/download/manual",
                    size=60_000_000_000,
                    seeders=44,
                    published=None,
                )
            ]
        }
    )
    qbit_behavior = FakeQBitBehavior(
        categories=[
            TorrentCategory(
                name="movies",
                save_path="/downloads/movies",
                resolved_save_path="/downloads/movies",
            ),
            TorrentCategory(
                name="movies-4k",
                save_path="/downloads/movies-4k",
                resolved_save_path="/downloads/movies-4k",
            ),
        ]
    )
    client.app.dependency_overrides[indexers_api.get_torznab_client_factory] = lambda: search_behavior.factory
    client.app.dependency_overrides[downloads_api.get_qbit_client_factory] = lambda: qbit_behavior.factory
    client.app.dependency_overrides[tmdb_api.get_tmdb_client_factory] = lambda: _fake_tmdb_factory
    try:
        qbit_client = client.post(
            "/api/v1/download-clients",
            headers=headers,
            json={
                "name": "Movies",
                "url": "http://qbit.test:8080",
                "username": "media",
                "password": "secret",
                "scope": "movies",
                "category": "movies",
                "enabled": True,
            },
        )
        assert qbit_client.status_code == 201, qbit_client.text
        qbit_client_id = qbit_client.json()["id"]

        categories = client.get(f"/api/v1/download-clients/{qbit_client_id}/categories")
        assert categories.status_code == 200, categories.text
        assert categories.json() == [
            {
                "name": "movies",
                "save_path": "/downloads/movies",
                "resolved_save_path": "/downloads/movies",
                "is_default": True,
            },
            {
                "name": "movies-4k",
                "save_path": "/downloads/movies-4k",
                "resolved_save_path": "/downloads/movies-4k",
                "is_default": False,
            },
        ]

        started = client.post(
            "/api/v1/interactive-search/movies",
            headers=headers,
            json={"tmdb_id": 27205, "quality_profile_id": profile_id},
        )
        assert started.status_code == 202, started.text
        job_id = started.json()["job_id"]
        job = client.get(f"/api/v1/search-jobs/{job_id}")
        assert job.status_code == 200, job.text
        payload = job.json()
        assert payload["target"]["entity_type"] == "tmdb_movie"
        assert payload["target"]["entity_id"] is None
        assert payload["target"]["tmdb_id"] == 27205
        assert payload["target"]["quality_profile_id"] == profile_id
        assert payload["results"][0]["quality_profile_id"] == profile_id

        async def movie_before_download():
            async with db_session.async_session_factory() as db:
                return await db.scalar(select(Movie).where(Movie.tmdb_id == 27205))

        # Searching is evidence only; it must not create a library Movie.
        assert asyncio.run(movie_before_download()) is None

        result_id = payload["results"][0]["id"]
        submitted = client.post(
            f"/api/v1/search-results/{result_id}/download",
            headers=headers,
            json={"download_client_id": qbit_client_id, "category": "movies-4k"},
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["status"] == "submitted"
        assert submitted.json()["category"] == "movies-4k"
        assert submitted.json()["movie_id"]
        assert len(qbit_behavior.submissions) == 1
        assert qbit_behavior.submissions[0]["category"] == "movies-4k"
        assert qbit_behavior.submissions[0]["save_path"] is None

        async def committed_state():
            async with db_session.async_session_factory() as db:
                movie = await db.scalar(select(Movie).where(Movie.tmdb_id == 27205))
                assert movie is not None
                assignment = await db.scalar(
                    select(MediaProfileOverride).where(MediaProfileOverride.movie_id == movie.id)
                )
                return movie, assignment

        movie, assignment = asyncio.run(committed_state())
        assert str(movie.id) == submitted.json()["movie_id"]
        assert movie.title == "Inception"
        assert movie.overview == "A dream within a dream."
        assert assignment is not None
        assert str(assignment.quality_profile_id) == profile_id

        repeated = client.post(
            f"/api/v1/search-results/{result_id}/download",
            headers=headers,
            json={"download_client_id": qbit_client_id, "category": "movies-4k"},
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["status"] == "already_submitted"
        assert repeated.json()["movie_id"] == submitted.json()["movie_id"]
        assert len(qbit_behavior.submissions) == 1
    finally:
        client.app.dependency_overrides.clear()


def test_unattached_show_season_tabs_only_return_packs_and_commit_one_show(client: TestClient) -> None:
    headers = _login(client)
    configured = client.put(
        "/api/v1/integrations/tmdb",
        headers=headers,
        json={"api_key": "test-key", "enabled": True},
    )
    assert configured.status_code == 200, configured.text

    profile = client.post(
        "/api/v1/quality-profiles",
        headers=headers,
        json={"name": "Manual Show Search"},
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["id"]

    indexer_url = "http://prowlarr.test/shows/api"
    _create_indexer(client, headers, name="Shows", url=indexer_url, scope="shows")
    behavior = FakeTorznabBehavior(
        expected_media_type="shows",
        expected_tmdb_id=95396,
        expected_query_text="Severance",
        results_by_season={
            1: [
                SearchResult(
                    guid="s1-pack",
                    title="Severance S01 2160p ATVP WEB-DL DDP5.1 H.265-GROUP",
                    download_url="http://prowlarr.test/download/s1-pack",
                    size=45_000_000_000,
                    seeders=50,
                    published=None,
                ),
                SearchResult(
                    guid="s1-episode",
                    title="Severance S01E01 2160p ATVP WEB-DL DDP5.1 H.265-GROUP",
                    download_url="http://prowlarr.test/download/s1e1",
                    size=5_000_000_000,
                    seeders=100,
                    published=None,
                ),
                SearchResult(
                    guid="multi-season",
                    title="Severance S01-S02 2160p ATVP WEB-DL DDP5.1 H.265-GROUP",
                    download_url="http://prowlarr.test/download/s1-s2",
                    size=90_000_000_000,
                    seeders=70,
                    published=None,
                ),
            ],
            2: [
                SearchResult(
                    guid="s2-pack",
                    title="Severance S02 2160p ATVP WEB-DL DDP5.1 H.265-GROUP",
                    download_url="http://prowlarr.test/download/s2-pack",
                    size=48_000_000_000,
                    seeders=60,
                    published=None,
                ),
            ],
        },
    )
    qbit_behavior = FakeQBitBehavior(
        categories=[TorrentCategory(name="tv", save_path="/downloads/tv", resolved_save_path="/downloads/tv")]
    )
    client.app.dependency_overrides[indexers_api.get_torznab_client_factory] = lambda: behavior.factory
    client.app.dependency_overrides[downloads_api.get_qbit_client_factory] = lambda: qbit_behavior.factory
    client.app.dependency_overrides[tmdb_api.get_tmdb_client_factory] = lambda: _fake_tmdb_factory
    try:
        qbit_client = client.post(
            "/api/v1/download-clients",
            headers=headers,
            json={
                "name": "TV",
                "url": "http://qbit.test:8080",
                "username": "media",
                "password": "secret",
                "scope": "shows",
                "category": "tv",
                "enabled": True,
            },
        )
        assert qbit_client.status_code == 201, qbit_client.text
        qbit_client_id = qbit_client.json()["id"]

        preview = client.get("/api/v1/interactive-search/shows/95396/preview")
        assert preview.status_code == 200, preview.text
        assert [row["season_number"] for row in preview.json()["seasons"]] == [0, 1, 2]

        s1 = client.post(
            "/api/v1/interactive-search/shows/seasons",
            headers=headers,
            json={"tmdb_id": 95396, "quality_profile_id": profile_id, "season_number": 1},
        )
        assert s1.status_code == 202, s1.text
        s1_job = client.get(f"/api/v1/search-jobs/{s1.json()['job_id']}")
        assert s1_job.status_code == 200, s1_job.text
        assert s1_job.json()["target"]["entity_type"] == "tmdb_show_season"
        assert s1_job.json()["target"]["season"] == 1
        # Single episodes and multi-season packs are deliberately filtered out.
        assert [row["title"] for row in s1_job.json()["results"]] == [
            "Severance S01 2160p ATVP WEB-DL DDP5.1 H.265-GROUP"
        ]

        s2 = client.post(
            "/api/v1/interactive-search/shows/seasons",
            headers=headers,
            json={"tmdb_id": 95396, "quality_profile_id": profile_id, "season_number": 2},
        )
        assert s2.status_code == 202, s2.text
        s2_job = client.get(f"/api/v1/search-jobs/{s2.json()['job_id']}")
        assert s2_job.status_code == 200, s2_job.text

        async def show_before_download():
            async with db_session.async_session_factory() as db:
                return await db.scalar(select(Show).where(Show.tmdb_id == 95396))

        assert asyncio.run(show_before_download()) is None

        first = client.post(
            f"/api/v1/search-results/{s1_job.json()['results'][0]['id']}/download",
            headers=headers,
            json={"download_client_id": qbit_client_id, "category": "tv"},
        )
        assert first.status_code == 200, first.text
        show_id = first.json()["show_id"]
        assert show_id
        assert first.json()["season_id"]

        second = client.post(
            f"/api/v1/search-results/{s2_job.json()['results'][0]['id']}/download",
            headers=headers,
            json={"download_client_id": qbit_client_id, "category": "tv"},
        )
        assert second.status_code == 200, second.text
        assert second.json()["show_id"] == show_id
        assert second.json()["season_id"] != first.json()["season_id"]
        assert [submission["category"] for submission in qbit_behavior.submissions] == ["tv", "tv"]

        async def committed_state():
            async with db_session.async_session_factory() as db:
                shows = (await db.scalars(select(Show).where(Show.tmdb_id == 95396))).all()
                assignment = await db.scalar(select(MediaProfileOverride).where(MediaProfileOverride.show_id == shows[0].id))
                seasons = (await db.scalars(select(Season).where(Season.show_id == shows[0].id))).all()
                return shows, assignment, seasons

        shows, assignment, seasons = asyncio.run(committed_state())
        assert len(shows) == 1
        assert str(shows[0].id) == show_id
        assert assignment is not None
        assert str(assignment.quality_profile_id) == profile_id
        assert {season.season_number for season in seasons} == {0, 1, 2}
    finally:
        client.app.dependency_overrides.clear()
