import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import indexers as indexers_api
from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.torznab import SearchResult
from app.main import create_app
from app.integrations.filesystem import DirectoryObservation
from app.models.domain import (
    AccessMode,
    Episode,
    IdentityState,
    InteractiveSearchResult,
    Job,
    JobStatus,
    MediaType,
    Movie,
    MovieRelease,
    ReleaseState,
    Season,
    Show,
    StorageRoot,
    Torrent,
)
from app.services.reconciliation import reconcile_movie_directory


@pytest.fixture
def client():
    db_path = tempfile.mktemp(prefix="medialogue-profiles-", suffix=".db", dir=os.getcwd())
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


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def seed_movie(*, with_release: bool = False) -> str:
    async def seed() -> str:
        async with db_session.async_session_factory() as db:
            movie = Movie(title="Inception", sort_title="Inception", year=2010, tmdb_id=27205, identity_state=IdentityState.MATCHED)
            db.add(movie)
            await db.flush()
            if with_release:
                db.add(MovieRelease(movie_id=movie.id, raw_release_name="Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC TrueHD 7.1-LM", release_state=ReleaseState.CURRENT))
            await db.commit()
            return "27205"
    return asyncio.run(seed())


def create_cf(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post("/api/v1/custom-formats", headers=headers, json={
        "name": "Hybrid",
        "media_scope": "movies",
        "conditions": [{"type": "release_attribute", "value": "Hybrid", "required": True}],
    })
    assert response.status_code == 201, response.text
    return response.json()


def quality_id(client: TestClient, name: str) -> str:
    rows = client.get("/api/v1/quality-definitions").json()
    return next(item["id"] for item in rows if item["name"] == name)


@dataclass
class Behavior:
    results: list[SearchResult] = field(default_factory=list)

    def factory(self, url: str, api_key: str, *, timeout: float = 15):
        return FakeTorznab(self)


class FakeTorznab:
    def __init__(self, behavior: Behavior):
        self.behavior = behavior

    async def search(self, *args, **kwargs):
        return list(self.behavior.results)

    async def close(self):
        return None


def create_indexer(client: TestClient, headers: dict[str, str]):
    response = client.post("/api/v1/indexers", headers=headers, json={
        "name": "PTP", "torznab_url": "http://indexer.test/api", "api_key": "secret", "scope": "movies", "enabled": True
    })
    assert response.status_code == 201, response.text


def test_quality_profile_crud_assignment_and_override_replaces_score(client: TestClient):
    headers = login(client)
    movie = seed_movie(with_release=True)
    cf = create_cf(client, headers)
    minimum = quality_id(client, "2160p BluRay REMUX")

    created = client.post("/api/v1/quality-profiles", headers=headers, json={
        "name": "4K Movies",
        "minimum_quality_definition_id": minimum,
        "custom_format_scores": [{"custom_format_id": cf["id"], "score": 120}],
    })
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["custom_format_scores"][0]["score"] == 120
    assert profile["assigned_titles"] == 0
    assert profile["revision"] == 1

    assigned = client.put(f"/api/v1/movies/{movie}/profile-settings", headers=headers, json={
        "quality_profile_id": profile["id"],
        "custom_format_score_overrides": {cf["id"]: -25},
        "expected_revision": 0,
    })
    assert assigned.status_code == 200, assigned.text
    settings = assigned.json()
    assert settings["quality_profile_name"] == "4K Movies"
    score = settings["custom_format_scores"][0]
    assert score["profile_score"] == 120
    assert score["override_score"] == -25
    assert score["effective_score"] == -25

    # Assignment changes immediately re-evaluate stored releases under current rules.
    async def current_score():
        async with db_session.async_session_factory() as db:
            release = await db.scalar(select(MovieRelease))
            return release.current_custom_format_score, release.parse_snapshot.get("current_score_snapshot")
    current, snapshot = asyncio.run(current_score())
    assert current == -25
    assert snapshot["total_score"] == -25

    stale = client.put(f"/api/v1/movies/{movie}/profile-settings", headers=headers, json={
        "quality_profile_id": profile["id"], "expected_revision": 0
    })
    assert stale.status_code == 409


def test_interactive_search_scores_warns_below_minimum_and_preserves_snapshot(client: TestClient):
    headers = login(client)
    movie = seed_movie()
    cf = create_cf(client, headers)
    minimum = quality_id(client, "2160p BluRay REMUX")
    profile = client.post("/api/v1/quality-profiles", headers=headers, json={
        "name": "Reference",
        "minimum_quality_definition_id": minimum,
        "custom_format_scores": [{"custom_format_id": cf["id"], "score": 500}],
    }).json()
    client.put(f"/api/v1/movies/{movie}/profile-settings", headers=headers, json={
        "quality_profile_id": profile["id"], "custom_format_score_overrides": {cf["id"]: 750}
    })
    create_indexer(client, headers)
    behavior = Behavior(results=[SearchResult(
        guid="one",
        title="Inception 2010 Hybrid 1080p BluRay REMUX AVC DTS-HD MA 5.1-LM",
        download_url="magnet:?xt=urn:btih:abc",
        size=10_000,
        seeders=5,
        published=None,
    )])
    client.app.dependency_overrides[indexers_api.get_torznab_client_factory] = lambda: behavior.factory
    try:
        started = client.post(f"/api/v1/movies/{movie}/interactive-search", headers=headers)
        assert started.status_code == 202, started.text
        payload = client.get(f"/api/v1/search-jobs/{started.json()['job_id']}").json()
        result = payload["results"][0]
        assert result["quality_profile_name"] == "Reference"
        assert result["custom_format_score"] == 750
        assert result["minimum_quality"] == "2160p BluRay REMUX"
        assert result["minimum_quality_met"] is False
        assert any("Below minimum quality" in item for item in result["warnings"])
        breakdown = result["custom_format_snapshot"]["formats"][0]
        assert breakdown["matched"] is True
        assert breakdown["profile_score"] == 500
        assert breakdown["override_score"] == 750
        assert breakdown["effective_score"] == 750
        assert breakdown["contribution"] == 750

        # Editing the profile later cannot rewrite immutable search-time evidence.
        changed = client.patch(f"/api/v1/quality-profiles/{profile['id']}", headers=headers, json={
            "custom_format_scores": [{"custom_format_id": cf["id"], "score": -1000}],
            "expected_revision": profile["revision"],
        })
        assert changed.status_code == 200, changed.text
        same = client.get(f"/api/v1/search-jobs/{started.json()['job_id']}").json()["results"][0]
        assert same["custom_format_score"] == 750
        assert same["custom_format_snapshot"]["formats"][0]["effective_score"] == 750
    finally:
        client.app.dependency_overrides.clear()


def test_zero_and_negative_scores_are_valid_and_quality_definitions_are_read_only(client: TestClient):
    headers = login(client)
    first = create_cf(client, headers)
    second = client.post("/api/v1/custom-formats", headers=headers, json={
        "name": "NoGroup", "media_scope": "movies", "conditions": [{"type": "release_group", "pattern": "^NoGroup$"}]
    }).json()
    profile = client.post("/api/v1/quality-profiles", headers=headers, json={
        "name": "Signed",
        "custom_format_scores": [
            {"custom_format_id": first["id"], "score": 0},
            {"custom_format_id": second["id"], "score": -100000},
        ],
    })
    assert profile.status_code == 201, profile.text
    values = {row["custom_format_name"]: row["score"] for row in profile.json()["custom_format_scores"]}
    assert values == {"Hybrid": 0, "NoGroup": -100000}
    assert client.post("/api/v1/quality-definitions", headers=headers, json={"name": "CAM"}).status_code == 405


def test_custom_format_edit_recomputes_current_score_without_rewriting_history(client: TestClient):
    headers = login(client)
    movie = seed_movie(with_release=True)
    cf = create_cf(client, headers)
    profile = client.post("/api/v1/quality-profiles", headers=headers, json={
        "name": "Current Rules",
        "custom_format_scores": [{"custom_format_id": cf["id"], "score": 333}],
    }).json()
    assigned = client.put(f"/api/v1/movies/{movie}/profile-settings", headers=headers, json={
        "quality_profile_id": profile["id"], "expected_revision": 0,
    })
    assert assigned.status_code == 200, assigned.text

    async def release_score():
        async with db_session.async_session_factory() as db:
            release = await db.scalar(select(MovieRelease))
            return release.current_custom_format_score, dict(release.parse_snapshot or {})

    before, before_snapshot = asyncio.run(release_score())
    assert before == 333
    assert before_snapshot["current_score_snapshot"]["total_score"] == 333

    # Change the matching rule so the already-present Hybrid release no longer
    # matches. Current score must follow current rules immediately.
    changed = client.patch(f"/api/v1/custom-formats/{cf['id']}", headers=headers, json={
        "conditions": [{"type": "release_attribute", "value": "REPACK", "required": True}],
        "expected_revision": cf["revision"],
    })
    assert changed.status_code == 200, changed.text
    after, after_snapshot = asyncio.run(release_score())
    assert after == 0
    assert after_snapshot["current_score_snapshot"]["total_score"] == 0


def test_deleting_profile_keeps_title_overrides_and_recomputes_current_score(client: TestClient):
    headers = login(client)
    movie = seed_movie(with_release=True)
    cf = create_cf(client, headers)
    profile = client.post("/api/v1/quality-profiles", headers=headers, json={
        "name": "Disposable Base",
        "custom_format_scores": [{"custom_format_id": cf["id"], "score": 100}],
    }).json()
    assigned = client.put(f"/api/v1/movies/{movie}/profile-settings", headers=headers, json={
        "quality_profile_id": profile["id"],
        "custom_format_score_overrides": {cf["id"]: -44},
        "expected_revision": 0,
    })
    assert assigned.status_code == 200, assigned.text
    old_revision = assigned.json()["revision"]

    deleted = client.delete(f"/api/v1/quality-profiles/{profile['id']}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    settings = client.get(f"/api/v1/movies/{movie}/profile-settings")
    assert settings.status_code == 200, settings.text
    payload = settings.json()
    assert payload["quality_profile_id"] is None
    assert payload["revision"] == old_revision + 1
    assert payload["custom_format_scores"][0]["override_score"] == -44
    assert payload["custom_format_scores"][0]["effective_score"] == -44

    async def current_score():
        async with db_session.async_session_factory() as db:
            release = await db.scalar(select(MovieRelease))
            return release.current_custom_format_score

    assert asyncio.run(current_score()) == -44


def test_episode_search_uses_parent_show_profile(client: TestClient):
    headers = login(client)

    async def seed_show() -> tuple[str, str]:
        async with db_session.async_session_factory() as db:
            show = Show(title="Dollface", year=2019, tmdb_id=88974, identity_state=IdentityState.MATCHED)
            db.add(show)
            await db.flush()
            season = Season(show_id=show.id, season_number=1)
            db.add(season)
            await db.flush()
            episode = Episode(show_id=show.id, season_id=season.id, season_number=1, episode_number=1, title="Guy's Girl")
            db.add(episode)
            await db.commit()
            return "88974", str(episode.id)

    show_resource, episode_id = asyncio.run(seed_show())
    cf_response = client.post("/api/v1/custom-formats", headers=headers, json={
        "name": "DSNP",
        "media_scope": "shows",
        "conditions": [{"type": "web_provider", "value": "DSNP", "required": True}],
    })
    assert cf_response.status_code == 201, cf_response.text
    cf = cf_response.json()
    profile_response = client.post("/api/v1/quality-profiles", headers=headers, json={
        "name": "Shows",
        "custom_format_scores": [{"custom_format_id": cf["id"], "score": 222}],
    })
    assert profile_response.status_code == 201, profile_response.text
    profile = profile_response.json()
    assignment = client.put(f"/api/v1/shows/{show_resource}/profile-settings", headers=headers, json={
        "quality_profile_id": profile["id"], "expected_revision": 0,
    })
    assert assignment.status_code == 200, assignment.text

    indexer = client.post("/api/v1/indexers", headers=headers, json={
        "name": "BTN", "torznab_url": "http://shows.test/api", "api_key": "secret", "scope": "shows", "enabled": True
    })
    assert indexer.status_code == 201, indexer.text
    behavior = Behavior(results=[SearchResult(
        guid="show-one",
        title="Dollface S01E01 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE",
        download_url="magnet:?xt=urn:btih:def",
        size=20_000,
        seeders=8,
        published=None,
    )])
    client.app.dependency_overrides[indexers_api.get_torznab_client_factory] = lambda: behavior.factory
    try:
        started = client.post(f"/api/v1/episodes/{episode_id}/interactive-search", headers=headers)
        assert started.status_code == 202, started.text
        result = client.get(f"/api/v1/search-jobs/{started.json()['job_id']}").json()["results"][0]
        assert result["quality_profile_name"] == "Shows"
        assert result["custom_format_score"] == 222
        assert result["custom_format_snapshot"]["formats"][0]["contribution"] == 222
    finally:
        client.app.dependency_overrides.clear()


def test_custom_format_test_all_can_explain_profile_score(client: TestClient):
    headers = login(client)
    cf = create_cf(client, headers)
    profile = client.post("/api/v1/quality-profiles", headers=headers, json={
        "name": "Tester",
        "custom_format_scores": [{"custom_format_id": cf["id"], "score": -123}],
    }).json()
    tested = client.post("/api/v1/custom-formats/test-all", json={
        "release_name": "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC-LM",
        "media_scope": "movies",
        "quality_profile_id": profile["id"],
    })
    assert tested.status_code == 200, tested.text
    payload = tested.json()
    assert payload["quality_profile_name"] == "Tester"
    assert payload["total_score"] == -123
    assert payload["formats"][0]["matched"] is True
    assert payload["formats"][0]["profile_score"] == -123
    assert payload["formats"][0]["contribution"] == -123


def test_completed_selected_search_release_preserves_download_score_and_tracks_current_score(client: TestClient):
    headers = login(client)
    movie_resource = seed_movie()
    cf = create_cf(client, headers)
    created_profile = client.post(
        "/api/v1/quality-profiles",
        headers=headers,
        json={
            "name": "Historical Score",
            "custom_format_scores": [{"custom_format_id": cf["id"], "score": 500}],
        },
    )
    assert created_profile.status_code == 201, created_profile.text
    profile = created_profile.json()
    assigned = client.put(
        f"/api/v1/movies/{movie_resource}/profile-settings",
        headers=headers,
        json={"quality_profile_id": profile["id"], "expected_revision": 0},
    )
    assert assigned.status_code == 200, assigned.text

    release_name = "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC TrueHD 7.1-LM"
    original_snapshot = {
        "schema_version": 1,
        "custom_format_score": 500,
        "custom_format_snapshot": {
            "schema_version": 2,
            "quality_profile_id": profile["id"],
            "quality_profile_name": "Historical Score",
            "total_score": 500,
        },
    }

    async def seed_selected_result() -> tuple[object, object, object]:
        async with db_session.async_session_factory() as db:
            movie = await db.scalar(select(Movie).where(Movie.tmdb_id == 27205))
            root = StorageRoot(
                name="Movies",
                resolved_root_path="/media/movies",
                media_type=MediaType.MOVIES,
                access_mode=AccessMode.READ_ONLY,
                enabled=True,
            )
            torrent = Torrent(
                info_hash="a" * 40,
                name=release_name,
                total_size=100_000_000_000,
                completed_at=datetime.now(timezone.utc),
            )
            job = Job(
                job_type="interactive_search",
                status=JobStatus.COMPLETED,
                progress={},
                summary={},
                cancellable=True,
            )
            db.add_all([root, torrent, job])
            await db.flush()
            selected = InteractiveSearchResult(
                job_id=job.id,
                indexer_id=None,
                indexer_name="PTP",
                media_type=MediaType.MOVIES,
                target_entity_type="movie",
                target_entity_id=movie.id,
                guid="historical-score",
                title=release_name,
                download_url="magnet:?xt=urn:btih:" + "a" * 40,
                size=100_000_000_000,
                seeders=10,
                parse_snapshot={},
                quality="2160p BluRay REMUX",
                edition=None,
                release_group="LM",
                custom_format_score=500,
                custom_format_snapshot=original_snapshot["custom_format_snapshot"],
                warnings=[],
                selected_at=datetime.now(timezone.utc),
                selection_snapshot=original_snapshot,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            db.add(selected)
            await db.commit()
            return movie.id, root.id, torrent.id

    movie_id, root_id, torrent_id = asyncio.run(seed_selected_result())

    # Current rules change after the user selected the result but before qBit
    # completes. The completed release must preserve 500 as historical evidence
    # while independently evaluating to 900 under the new current profile.
    updated_profile = client.patch(
        f"/api/v1/quality-profiles/{profile['id']}",
        headers=headers,
        json={
            "custom_format_scores": [{"custom_format_id": cf["id"], "score": 900}],
            "expected_revision": profile["revision"],
        },
    )
    assert updated_profile.status_code == 200, updated_profile.text

    async def reconcile_and_read():
        async with db_session.async_session_factory() as db:
            movie = await db.get(Movie, movie_id)
            root = await db.get(StorageRoot, root_id)
            torrent = await db.get(Torrent, torrent_id)
            observation = DirectoryObservation(
                path="/media/movies/" + release_name,
                name=release_name,
                media_files=(release_name + ".mkv",),
                has_dvd_structure=False,
                has_bluray_structure=False,
            )
            result = await reconcile_movie_directory(db, root, observation, torrent=torrent, movie_hint=movie)
            await db.commit()
            release = await db.scalar(select(MovieRelease).where(MovieRelease.movie_id == movie.id))
            return result, release

    result, release = asyncio.run(reconcile_and_read())
    assert result == "matched"
    assert release.original_custom_format_score == 500
    assert release.current_custom_format_score == 900
    assert release.selection_snapshot["custom_format_score"] == 500
    assert release.selection_snapshot["custom_format_snapshot"]["total_score"] == 500
    assert release.parse_snapshot["current_score_snapshot"]["total_score"] == 900


def test_deleting_custom_format_cleans_live_overrides_but_preserves_historical_release_evidence(client: TestClient):
    headers = login(client)
    movie_resource = seed_movie(with_release=True)
    cf = create_cf(client, headers)
    profile = client.post(
        "/api/v1/quality-profiles",
        headers=headers,
        json={
            "name": "CF Cleanup",
            "custom_format_scores": [{"custom_format_id": cf["id"], "score": 100}],
        },
    ).json()
    assigned = client.put(
        f"/api/v1/movies/{movie_resource}/profile-settings",
        headers=headers,
        json={
            "quality_profile_id": profile["id"],
            "custom_format_score_overrides": {cf["id"]: -50},
            "expected_revision": 0,
        },
    )
    assert assigned.status_code == 200, assigned.text
    assignment_revision = assigned.json()["revision"]

    async def seed_history():
        async with db_session.async_session_factory() as db:
            release = await db.scalar(select(MovieRelease))
            release.original_custom_format_score = 777
            release.selection_snapshot = {
                "schema_version": 1,
                "custom_format_score": 777,
                "custom_format_snapshot": {"total_score": 777, "formats": [{"custom_format_id": cf["id"]}]},
            }
            await db.commit()

    asyncio.run(seed_history())

    deleted = client.delete(f"/api/v1/custom-formats/{cf['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    settings = client.get(f"/api/v1/movies/{movie_resource}/profile-settings")
    assert settings.status_code == 200, settings.text
    payload = settings.json()
    assert payload["revision"] == assignment_revision + 1
    assert payload["custom_format_scores"] == []

    async def release_state():
        async with db_session.async_session_factory() as db:
            release = await db.scalar(select(MovieRelease))
            return (
                release.original_custom_format_score,
                release.current_custom_format_score,
                dict(release.selection_snapshot or {}),
            )

    original, current, history = asyncio.run(release_state())
    assert original == 777
    assert current == 0
    assert history["custom_format_score"] == 777
    assert history["custom_format_snapshot"]["total_score"] == 777
