import asyncio
import os
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.torznab import SearchResult
from app.main import create_app
from app.models.domain import (
    AccessMode,
    Episode,
    EpisodeMediaMap,
    IdentityState,
    Job,
    JobStatus,
    MediaDirectory,
    MediaFile,
    MediaProfileOverride,
    MediaType,
    Movie,
    MovieRelease,
    PresenceState,
    QualityDefinition,
    ReleaseScope,
    Season,
    Show,
    ShowRelease,
    StorageRoot,
    UpgradeCandidate,
)
from app.services.upgrade_candidates import _season_airing_eligible, run_upgrade_search_job


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


def _login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _create_indexer(client: TestClient, headers: dict[str, str], *, scope: str = "both") -> None:
    response = client.post(
        "/api/v1/indexers",
        headers=headers,
        json={
            "name": "Upgrade Indexer",
            "torznab_url": "http://upgrade-indexer.test/api",
            "api_key": "secret",
            "scope": scope,
            "enabled": True,
            "enable_interactive_search": True,
            "minimum_seeders": 0,
            "priority": 10,
        },
    )
    assert response.status_code == 201, response.text


@dataclass
class FakeBehavior:
    movie_results: list[SearchResult] = field(default_factory=list)
    season_results: dict[int, list[SearchResult]] = field(default_factory=dict)

    def factory(self, url: str, api_key: str, *, timeout: float = 15.0):
        return FakeTorznab(self)


class FakeTorznab:
    def __init__(self, behavior: FakeBehavior):
        self.behavior = behavior

    async def search(self, query: str, *, media_type: str, tmdb_id=None, season=None, episode=None, categories=()):
        if media_type == "shows" and season is not None:
            return list(self.behavior.season_results.get(season, []))
        return list(self.behavior.movie_results)

    async def close(self):
        return None


async def _create_upgrade_job() -> str:
    async with db_session.async_session_factory() as db:
        job = Job(job_type="search_media_upgrades", status=JobStatus.QUEUED, cancellable=False, summary={})
        db.add(job)
        await db.commit()
        return str(job.id)


def _run_search(behavior: FakeBehavior, *, today: date = date(2026, 8, 31)) -> None:
    job_id = asyncio.run(_create_upgrade_job())
    asyncio.run(run_upgrade_search_job(UUID(job_id), client_factory=behavior.factory, today=today))


def test_movie_upgrade_keeps_best_candidate_and_never_exposes_download_url(client: TestClient) -> None:
    headers = _login(client)
    _create_indexer(client, headers, scope="movies")

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name="Movies",
                resolved_root_path="/media/movies",
                media_type=MediaType.MOVIES,
                access_mode=AccessMode.READ_WRITE,
            )
            movie = Movie(
                title="Upgrade Movie",
                sort_title="Upgrade Movie",
                year=2024,
                tmdb_id=1001,
                identity_state=IdentityState.MATCHED,
            )
            db.add_all([root, movie])
            await db.flush()
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name="Upgrade Movie (2024)",
                tracker_title="Upgrade.Movie.2024.1080p.WEB-DL.x264-GRP",
            )
            db.add(release)
            await db.flush()
            db.add(
                MediaDirectory(
                    storage_root_id=root.id,
                    resolved_path="/media/movies/Upgrade Movie (2024)",
                    movie_release_id=release.id,
                )
            )
            await db.commit()

    asyncio.run(seed())
    behavior = FakeBehavior(
        movie_results=[
            SearchResult(
                title="Upgrade.Movie.2024.1080p.WEB-DL.REPACK.x264-GRP",
                guid="same-quality-repack",
                download_url="http://upgrade-indexer.test/download?api_key=do-not-expose",
                size=10,
                seeders=50,
                published=None,
                leechers=2,
            ),
            SearchResult(
                title="Upgrade.Movie.2024.2160p.WEB-DL.x264-GRP",
                guid="quality-upgrade",
                download_url="http://upgrade-indexer.test/download/quality?api_key=do-not-expose",
                size=20,
                seeders=5,
                published=None,
                leechers=1,
            ),
            SearchResult(
                title="Upgrade.Movie.2024.Directors.Cut.2160p.WEB-DL.x264-GRP",
                guid="wrong-edition",
                download_url="http://upgrade-indexer.test/download/wrong-edition?api_key=do-not-expose",
                size=20,
                seeders=100,
                published=None,
                leechers=0,
            ),
        ]
    )
    _run_search(behavior)

    response = client.get("/api/v1/upgrade-candidates")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Upgrade.Movie.2024.2160p.WEB-DL.x264-GRP"
    assert row["current_release_name"] == "Upgrade.Movie.2024.1080p.WEB-DL.x264-GRP"
    assert "quality" in row["upgrade_types"]
    assert row["execution_eligible"] is True
    assert row["result_count"] == 3
    assert "download_url" not in row
    assert "guid" not in row

    results_response = client.get(f"/api/v1/upgrade-candidates/{row['id']}/results")
    assert results_response.status_code == 200, results_response.text
    result_rows = results_response.json()["results"]
    assert len(result_rows) == 3
    assert all("download_url" not in result for result in result_rows)
    assert all("guid" not in result for result in result_rows)
    assert sum(result["recommended"] for result in result_rows) == 1
    assert sum(result["selected"] for result in result_rows) == 1
    rejected = next(result for result in result_rows if result["title"].endswith("Directors.Cut.2160p.WEB-DL.x264-GRP"))
    assert rejected["eligible"] is False
    assert any("edition" in reason.casefold() for reason in rejected["rejection_reasons"])

    repack = next(result for result in result_rows if ".REPACK." in result["title"])
    selected_response = client.post(
        f"/api/v1/upgrade-candidates/{row['id']}/results/select",
        headers=headers,
        json={"result_id": repack["id"]},
    )
    assert selected_response.status_code == 200, selected_response.text
    selected_row = selected_response.json()
    assert selected_row["title"] == "Upgrade.Movie.2024.1080p.WEB-DL.REPACK.x264-GRP"
    assert selected_row["result_count"] == 3
    selected_results = client.get(f"/api/v1/upgrade-candidates/{row['id']}/results").json()["results"]
    assert next(result for result in selected_results if result["id"] == repack["id"])["selected"] is True
    assert next(result for result in selected_results if result["recommended"])["title"] == (
        "Upgrade.Movie.2024.2160p.WEB-DL.x264-GRP"
    )

    behavior.movie_results = [
        SearchResult(
            title="Upgrade.Movie.2024.2160p.WEB-DL.REPACK.x264-OTHER",
            guid="cross-group-quality-upgrade",
            download_url="http://upgrade-indexer.test/download/cross-group-quality",
            size=20,
            seeders=50,
            published=None,
            leechers=2,
        )
    ]
    _run_search(behavior)
    cross_group_quality = client.get("/api/v1/upgrade-candidates").json()
    assert len(cross_group_quality) == 1
    assert "quality" in cross_group_quality[0]["upgrade_types"]
    assert "repack" not in cross_group_quality[0]["upgrade_types"]
    assert cross_group_quality[0]["comparison_snapshot"]["candidate"]["same_release_group"] is False
    assert any(
        item["suppressed_for_upgrade"]
        for item in cross_group_quality[0]["comparison_snapshot"]["candidate"]["formats"]
        if item["matched"]
    )

    for marker in ("REPACK", "PROPER"):
        behavior.movie_results = [
            SearchResult(
                title=f"Upgrade.Movie.2024.1080p.WEB-DL.{marker}.x264-OTHER",
                guid=f"cross-group-{marker.casefold()}",
                download_url=f"http://upgrade-indexer.test/download/{marker.casefold()}",
                size=10,
                seeders=50,
                published=None,
                leechers=2,
            )
        ]
        _run_search(behavior)
        assert client.get("/api/v1/upgrade-candidates").json() == []

    for marker, expected_type in (("REPACK2", "repack"), ("PROPER2", "proper")):
        behavior.movie_results = [
            SearchResult(
                title=f"Upgrade.Movie.2024.1080p.WEB-DL.{marker}.x264-GRP",
                guid=f"same-group-{marker.casefold()}",
                download_url=f"http://upgrade-indexer.test/download/{marker.casefold()}",
                size=10,
                seeders=50,
                published=None,
                leechers=2,
            )
        ]
        _run_search(behavior)
        revision_rows = client.get("/api/v1/upgrade-candidates").json()
        assert len(revision_rows) == 1
        assert expected_type in revision_rows[0]["upgrade_types"]
        comparison = revision_rows[0]["comparison_snapshot"]
        assert comparison["candidate"]["same_release_group"] is True


def test_cross_group_custom_format_with_real_in_its_text_is_not_treated_as_revision(client: TestClient) -> None:
    headers = _login(client)
    _create_indexer(client, headers, scope="movies")
    custom_format = client.post(
        "/api/v1/custom-formats",
        headers=headers,
        json={
            "name": "Unreal Bonus",
            "conditions": [{"pattern": r"\bUnreal\b", "required": True}],
        },
    ).json()
    profile = client.post(
        "/api/v1/quality-profiles",
        headers=headers,
        json={
            "name": "Unreal Profile",
            "custom_format_scores": [{"custom_format_id": custom_format["id"], "score": 500}],
        },
    ).json()

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name="Movies",
                resolved_root_path="/media/movies",
                media_type=MediaType.MOVIES,
                access_mode=AccessMode.READ_WRITE,
            )
            movie = Movie(
                title="Odd Movie",
                sort_title="Odd Movie",
                year=2024,
                tmdb_id=1009,
                identity_state=IdentityState.MATCHED,
            )
            db.add_all([root, movie])
            await db.flush()
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name="Odd Movie (2024)",
                tracker_title="Odd.Movie.2024.1080p.WEB-DL.x264-GRP",
            )
            db.add(release)
            await db.flush()
            db.add(
                MediaDirectory(
                    storage_root_id=root.id,
                    resolved_path="/media/movies/Odd Movie (2024)",
                    movie_release_id=release.id,
                )
            )
            db.add(
                MediaProfileOverride(
                    media_type=MediaType.MOVIES,
                    movie_id=movie.id,
                    quality_profile_id=UUID(profile["id"]),
                    override_definition={},
                    revision=1,
                )
            )
            await db.commit()

    asyncio.run(seed())
    behavior = FakeBehavior(
        movie_results=[
            SearchResult(
                title="Odd.Movie.2024.Unreal.1080p.WEB-DL.x264-OTHER",
                guid="surreal-custom-format",
                download_url="http://upgrade-indexer.test/download/surreal",
                size=10,
                seeders=20,
                published=None,
                leechers=1,
            )
        ]
    )
    _run_search(behavior)

    rows = client.get("/api/v1/upgrade-candidates").json()
    assert len(rows) == 1
    assert rows[0]["upgrade_types"] == ["custom_format"]
    matched = next(
        item
        for item in rows[0]["comparison_snapshot"]["candidate"]["formats"]
        if item["custom_format_name"] == "Unreal Bonus"
    )
    assert matched["matched"] is True
    assert matched["contribution"] == 500
    assert matched["suppressed_for_upgrade"] is False


def test_movie_upgrade_preserves_the_current_edition(client: TestClient) -> None:
    headers = _login(client)
    _create_indexer(client, headers, scope="movies")

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name="Movies",
                resolved_root_path="/media/movies",
                media_type=MediaType.MOVIES,
                access_mode=AccessMode.READ_WRITE,
            )
            movie = Movie(
                title="Blade Runner",
                sort_title="Blade Runner",
                year=1982,
                tmdb_id=78,
                identity_state=IdentityState.MATCHED,
            )
            db.add_all([root, movie])
            await db.flush()
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name="Blade.Runner.1982.The.Final.Cut.1080p.BluRay.x264-GRP",
                tracker_title="Blade.Runner.1982.The.Final.Cut.1080p.BluRay.x264-GRP",
                parsed_edition="Final Cut",
                effective_edition="Final Cut",
            )
            db.add(release)
            await db.flush()
            db.add(MediaDirectory(
                storage_root_id=root.id,
                resolved_path="/media/movies/Blade Runner (1982) [Final Cut]",
                movie_release_id=release.id,
            ))
            await db.commit()

    asyncio.run(seed())
    behavior = FakeBehavior(movie_results=[
        SearchResult(
            title="Blade.Runner.1982.2160p.UHD.BluRay.REMUX-GRP",
            guid="theatrical",
            download_url="http://upgrade-indexer.test/theatrical",
            size=20,
            seeders=100,
            published=None,
        ),
        SearchResult(
            title="Blade.Runner.1982.The.Final.Cut.2160p.UHD.BluRay.REMUX-GRP",
            guid="final-cut",
            download_url="http://upgrade-indexer.test/final-cut",
            size=20,
            seeders=5,
            published=None,
        ),
    ])
    _run_search(behavior)

    rows = client.get("/api/v1/upgrade-candidates?media_type=movies").json()
    assert len(rows) == 1
    assert rows[0]["title"] == "Blade.Runner.1982.The.Final.Cut.2160p.UHD.BluRay.REMUX-GRP"
    assert rows[0]["current_edition"] == "Final Cut"
    assert rows[0]["candidate_edition"] == "Final Cut"
    assert rows[0]["comparison_snapshot"]["current"]["edition"] == "Final Cut"
    assert rows[0]["comparison_snapshot"]["candidate"]["edition"] == "Final Cut"

    behavior.movie_results = [behavior.movie_results[0]]
    _run_search(behavior)
    assert client.get("/api/v1/upgrade-candidates?media_type=movies").json() == []


def test_read_only_media_folder_is_never_searched_and_is_reported_once(client: TestClient) -> None:
    """A read-only folder cannot receive a replacement, so it yields no rows.

    Searching it would spend indexer queries producing candidates nobody can act
    on, and one per release would restate a single folder setting many times.
    """

    headers = _login(client)
    _create_indexer(client, headers, scope="movies")

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name="Archive",
                resolved_root_path="/media/archive",
                media_type=MediaType.MOVIES,
                access_mode=AccessMode.READ_ONLY,
            )
            movie = Movie(
                title="Locked Movie",
                sort_title="Locked Movie",
                year=2024,
                tmdb_id=2002,
                identity_state=IdentityState.MATCHED,
            )
            db.add_all([root, movie])
            await db.flush()
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name="Locked Movie (2024)",
                tracker_title="Locked.Movie.2024.1080p.WEB-DL.x264-GRP",
            )
            db.add(release)
            await db.flush()
            db.add(
                MediaDirectory(
                    storage_root_id=root.id,
                    resolved_path="/media/archive/Locked Movie (2024)",
                    movie_release_id=release.id,
                )
            )
            await db.commit()

    asyncio.run(seed())
    behavior = FakeBehavior(
        movie_results=[
            SearchResult(
                title="Locked.Movie.2024.2160p.WEB-DL.x264-GRP",
                guid="quality-upgrade",
                download_url="http://upgrade-indexer.test/download/quality",
                size=20,
                seeders=50,
                published=None,
                leechers=1,
            )
        ]
    )
    _run_search(behavior)

    assert client.get("/api/v1/upgrade-candidates").json() == []

    count = client.get("/api/v1/upgrade-candidates/count").json()
    assert count["count"] == 0
    assert count["read_only_roots"] == ["Archive"]


def test_show_season_pack_must_match_highest_quality_and_wait_until_season_complete(client: TestClient) -> None:
    headers = _login(client)
    _create_indexer(client, headers, scope="shows")

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name="Shows",
                resolved_root_path="/media/shows",
                media_type=MediaType.SHOWS,
                access_mode=AccessMode.READ_WRITE,
            )
            show = Show(
                title="Pack Show",
                year=2025,
                tmdb_id=2002,
                identity_state=IdentityState.MATCHED,
            )
            db.add_all([root, show])
            await db.flush()
            season = Season(
                show_id=show.id,
                season_number=1,
                monitored=True,
                metadata_json={"episode_count": 2, "provider": "tmdb"},
            )
            db.add(season)
            await db.flush()
            episodes = [
                Episode(
                    show_id=show.id,
                    season_id=season.id,
                    season_number=1,
                    episode_number=1,
                    air_date=date(2025, 1, 1),
                    monitored=True,
                    presence_state=PresenceState.PRESENT,
                ),
                Episode(
                    show_id=show.id,
                    season_id=season.id,
                    season_number=1,
                    episode_number=2,
                    air_date=date(2025, 1, 8),
                    monitored=True,
                    presence_state=PresenceState.PRESENT,
                ),
            ]
            db.add_all(episodes)
            high = ShowRelease(
                show_id=show.id,
                season_id=season.id,
                raw_release_name="Pack.Show.S01E01.2160p.WEB-DL.x264-GRP",
                release_scope=ReleaseScope.EPISODE,
            )
            low = ShowRelease(
                show_id=show.id,
                season_id=season.id,
                raw_release_name="Pack.Show.S01E02.1080p.WEB-DL.x264-GRP",
                release_scope=ReleaseScope.EPISODE,
            )
            db.add_all([high, low])
            await db.flush()
            db.add_all(
                [
                    MediaDirectory(
                        storage_root_id=root.id,
                        resolved_path="/media/shows/Pack Show/Season 01/E01",
                        show_release_id=high.id,
                    ),
                    MediaDirectory(
                        storage_root_id=root.id,
                        resolved_path="/media/shows/Pack Show/Season 01/E02",
                        show_release_id=low.id,
                    ),
                ]
            )
            await db.commit()

    asyncio.run(seed())
    behavior = FakeBehavior(
        season_results={
            1: [
                SearchResult(
                    title="Pack.Show.S01.1080p.WEB-DL.x264-GRP",
                    guid="bad-pack",
                    download_url="http://upgrade-indexer.test/bad",
                    size=None,
                    seeders=100,
                    published=None,
                ),
                SearchResult(
                    title="Pack.Show.S01.2160p.WEB-DL.x264-GRP",
                    guid="good-pack",
                    download_url="http://upgrade-indexer.test/good",
                    size=None,
                    seeders=10,
                    published=None,
                ),
            ]
        }
    )
    _run_search(behavior)

    rows = client.get("/api/v1/upgrade-candidates?media_type=shows").json()
    assert len(rows) == 1
    assert rows[0]["title"] == "Pack.Show.S01.2160p.WEB-DL.x264-GRP"
    assert rows[0]["current_quality"] == "2160p WEB-DL"
    assert rows[0]["current_release_name"] == "Pack.Show.S01E01.2160p.WEB-DL.x264-GRP"
    assert "season_pack" in rows[0]["upgrade_types"]
    assert rows[0]["execution_eligible"] is True

    async def mark_future() -> None:
        async with db_session.async_session_factory() as db:
            episode = await db.scalar(select(Episode).where(Episode.episode_number == 2))
            assert episode is not None
            episode.air_date = date(2026, 9, 5)
            await db.commit()

    asyncio.run(mark_future())
    _run_search(behavior, today=date(2026, 8, 31))
    assert client.get("/api/v1/upgrade-candidates?media_type=shows").json() == []


def test_season_pack_upgrade_policy_can_be_disabled_per_profile(client: TestClient) -> None:
    headers = _login(client)
    profiles = client.get("/api/v1/quality-profiles").json()
    default = next(profile for profile in profiles if profile["is_default"])
    assert default["season_pack_upgrades"] is True
    response = client.patch(
        f"/api/v1/quality-profiles/{default['id']}",
        headers=headers,
        json={"season_pack_upgrades": False, "expected_revision": default["revision"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["season_pack_upgrades"] is False


def test_upgrade_profile_controls_preserve_overrides_and_support_bulk(client: TestClient, monkeypatch) -> None:
    headers = _login(client)
    # Keep the scheduled re-evaluation queued during this focused API test.
    monkeypatch.setattr("app.services.scheduler.launch_runtime_job", lambda *_args, **_kwargs: None)
    default = next(profile for profile in client.get("/api/v1/quality-profiles").json() if profile["is_default"])
    preferred_response = client.post(
        "/api/v1/quality-profiles",
        headers=headers,
        json={"name": "Upgrade Preferred", "custom_format_scores": []},
    )
    assert preferred_response.status_code == 201, preferred_response.text
    preferred = preferred_response.json()

    async def seed() -> str:
        async with db_session.async_session_factory() as db:
            movie = Movie(
                title="Profile Movie",
                sort_title="Profile Movie",
                year=2026,
                tmdb_id=9001,
                identity_state=IdentityState.MATCHED,
            )
            db.add(movie)
            await db.flush()
            db.add(MediaProfileOverride(
                media_type=MediaType.MOVIES,
                movie_id=movie.id,
                quality_profile_id=UUID(default["id"]),
                override_definition={"minimum_custom_format_score": 25, "custom_format_scores": {}},
                revision=3,
            ))
            candidate = UpgradeCandidate(
                target_key=f"movie:{movie.id}",
                media_type=MediaType.MOVIES,
                movie_id=movie.id,
                current_release_ids=[],
                current_release_name="Profile.Movie.2026.1080p.WEB-DL-GRP",
                current_quality="1080p WEB-DL",
                indexer_name="Test Indexer",
                guid="profile-candidate",
                title="Profile.Movie.2026.2160p.WEB-DL-GRP",
                quality="2160p WEB-DL",
                upgrade_types=["quality"],
                execution_eligible=True,
            )
            db.add(candidate)
            await db.commit()
            return str(candidate.id)

    candidate_id = asyncio.run(seed())
    listed = client.get("/api/v1/upgrade-candidates").json()[0]
    assert listed["quality_profile_id"] == default["id"]
    assert listed["quality_profile_inherited"] is False

    changed = client.patch(
        f"/api/v1/upgrade-candidates/{candidate_id}/quality-profile",
        headers=headers,
        json={"quality_profile_id": preferred["id"]},
    )
    assert changed.status_code == 202, changed.text
    assert changed.json()["quality_profile_id"] == preferred["id"]
    assert changed.json()["candidate_ids"] == [candidate_id]

    inherited = client.post(
        "/api/v1/upgrade-candidates/quality-profile",
        headers=headers,
        json={"candidate_ids": [candidate_id], "quality_profile_id": None},
    )
    assert inherited.status_code == 202, inherited.text
    assert inherited.json()["quality_profile_id"] is None

    async def verify() -> None:
        async with db_session.async_session_factory() as db:
            assignment = await db.scalar(select(MediaProfileOverride).where(MediaProfileOverride.movie_id.is_not(None)))
            assert assignment is not None
            assert assignment.quality_profile_id is None
            assert assignment.override_definition == {"minimum_custom_format_score": 25, "custom_format_scores": {}}
            assert assignment.revision == 5
            candidate = await db.get(UpgradeCandidate, UUID(candidate_id))
            assert candidate is not None
            assert candidate.execution_eligible is False
            assert "re-evaluated" in str(candidate.execution_block_reason)

    asyncio.run(verify())


def test_airing_guard_fails_closed_on_unknown_or_future_metadata() -> None:
    season = Season(show_id=None, season_number=1, metadata_json={"episode_count": 2})  # type: ignore[arg-type]
    episode1 = Episode(
        show_id=None,  # type: ignore[arg-type]
        season_id=None,  # type: ignore[arg-type]
        season_number=1,
        episode_number=1,
        air_date=date(2026, 8, 1),
        monitored=True,
    )
    episode2 = Episode(
        show_id=None,  # type: ignore[arg-type]
        season_id=None,  # type: ignore[arg-type]
        season_number=1,
        episode_number=2,
        air_date=None,
        monitored=True,
    )
    eligible, reason = _season_airing_eligible(season, [episode1, episode2], today=date(2026, 8, 31))
    assert eligible is False
    assert "unknown air date" in str(reason)
    episode2.air_date = date(2026, 9, 1)
    eligible, reason = _season_airing_eligible(season, [episode1, episode2], today=date(2026, 8, 31))
    assert eligible is False
    assert "still airing" in str(reason)


def test_show_on_read_only_root_is_skipped_through_episode_media_maps(client: TestClient) -> None:
    headers = _login(client)
    _create_indexer(client, headers, scope="shows")

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name="Show Archive",
                resolved_root_path="/archive/shows",
                media_type=MediaType.SHOWS,
                access_mode=AccessMode.READ_ONLY,
            )
            show = Show(title="Mapped Show", year=2024, tmdb_id=8101, identity_state=IdentityState.MATCHED)
            db.add_all([root, show])
            await db.flush()
            season = Season(show_id=show.id, season_number=1, monitored=True, metadata_json={"episode_count": 1})
            db.add(season)
            await db.flush()
            episode = Episode(
                show_id=show.id,
                season_id=season.id,
                season_number=1,
                episode_number=1,
                air_date=date(2024, 1, 1),
                monitored=True,
                presence_state=PresenceState.PRESENT,
            )
            release = ShowRelease(
                show_id=show.id,
                season_id=season.id,
                raw_release_name="Mapped.Show.S01E01.1080p.WEB-DL.x264-GRP",
                release_scope=ReleaseScope.EPISODE,
            )
            db.add_all([episode, release])
            await db.flush()
            directory = MediaDirectory(
                storage_root_id=root.id,
                resolved_path="/archive/shows/Mapped Show/Season 01",
            )
            db.add(directory)
            await db.flush()
            media_file = MediaFile(
                media_directory_id=directory.id,
                relative_path="Mapped.Show.S01E01.1080p.WEB-DL.x264-GRP.mkv",
                filename="Mapped.Show.S01E01.1080p.WEB-DL.x264-GRP.mkv",
            )
            db.add(media_file)
            await db.flush()
            db.add(EpisodeMediaMap(episode_id=episode.id, media_file_id=media_file.id, show_release_id=release.id))
            await db.commit()

    asyncio.run(seed())
    behavior = FakeBehavior(
        season_results={
            1: [
                SearchResult(
                    title="Mapped.Show.S01.2160p.WEB-DL.x264-GRP",
                    guid="mapped-show-upgrade",
                    download_url="http://upgrade-indexer.test/mapped-show",
                    size=100,
                    seeders=10,
                    published=None,
                )
            ]
        }
    )
    _run_search(behavior)

    assert client.get("/api/v1/upgrade-candidates?media_type=shows").json() == []
    count = client.get("/api/v1/upgrade-candidates/count").json()
    assert count["read_only_roots"] == ["Show Archive"]


def test_show_season_is_not_upgradeable_when_any_current_release_is_unscorable(client: TestClient) -> None:
    headers = _login(client)
    _create_indexer(client, headers, scope="shows")

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name="Shows",
                resolved_root_path="/media/shows",
                media_type=MediaType.SHOWS,
                access_mode=AccessMode.READ_WRITE,
            )
            show = Show(title="Mixed Quality", year=2024, tmdb_id=8102, identity_state=IdentityState.MATCHED)
            unknown = QualityDefinition(name="Unknown Private Quality", rank=-999, enabled=True)
            db.add_all([root, show, unknown])
            await db.flush()
            season = Season(show_id=show.id, season_number=1, monitored=True, metadata_json={"episode_count": 2})
            db.add(season)
            await db.flush()
            db.add_all([
                Episode(
                    show_id=show.id, season_id=season.id, season_number=1, episode_number=1,
                    air_date=date(2024, 1, 1), monitored=True, presence_state=PresenceState.PRESENT,
                ),
                Episode(
                    show_id=show.id, season_id=season.id, season_number=1, episode_number=2,
                    air_date=date(2024, 1, 8), monitored=True, presence_state=PresenceState.PRESENT,
                ),
            ])
            recognized = ShowRelease(
                show_id=show.id, season_id=season.id,
                raw_release_name="Mixed.Quality.S01E01.1080p.WEB-DL.x264-GRP",
                release_scope=ReleaseScope.EPISODE,
            )
            unscorable = ShowRelease(
                show_id=show.id, season_id=season.id,
                raw_release_name="Mixed.Quality.S01E02.1080p.WEB-DL.x264-GRP",
                release_scope=ReleaseScope.EPISODE,
                quality_definition_id=unknown.id,
            )
            db.add_all([recognized, unscorable])
            await db.flush()
            db.add_all([
                MediaDirectory(storage_root_id=root.id, resolved_path="/media/shows/Mixed Quality/E01", show_release_id=recognized.id),
                MediaDirectory(storage_root_id=root.id, resolved_path="/media/shows/Mixed Quality/E02", show_release_id=unscorable.id),
            ])
            await db.commit()

    asyncio.run(seed())
    behavior = FakeBehavior(
        season_results={
            1: [
                SearchResult(
                    title="Mixed.Quality.S01.2160p.WEB-DL.x264-GRP",
                    guid="unsafe-partial-season",
                    download_url="http://upgrade-indexer.test/unsafe-partial-season",
                    size=100,
                    seeders=10,
                    published=None,
                )
            ]
        }
    )
    _run_search(behavior)

    assert client.get("/api/v1/upgrade-candidates?media_type=shows").json() == []


def test_profile_policy_change_blocks_cached_upgrade_candidates(client: TestClient) -> None:
    headers = _login(client)
    profiles = client.get("/api/v1/quality-profiles").json()
    default = next(profile for profile in profiles if profile["is_default"])

    async def seed() -> str:
        async with db_session.async_session_factory() as db:
            movie = Movie(title="Stale Candidate", sort_title="Stale Candidate", year=2024, tmdb_id=8103)
            db.add(movie)
            await db.flush()
            candidate = UpgradeCandidate(
                target_key=f"movie-release:{movie.id}",
                media_type=MediaType.MOVIES,
                movie_id=movie.id,
                current_release_ids=[],
                current_release_name="Stale.Candidate.2024.1080p.WEB-DL-GRP",
                current_quality="1080p WEB-DL",
                indexer_name="Upgrade Indexer",
                guid="stale",
                title="Stale.Candidate.2024.2160p.WEB-DL-GRP",
                quality="2160p WEB-DL",
                execution_eligible=True,
            )
            db.add(candidate)
            await db.commit()
            return str(candidate.id)

    candidate_id = asyncio.run(seed())
    changed = client.patch(
        f"/api/v1/quality-profiles/{default['id']}",
        headers=headers,
        json={
            "season_pack_upgrades": not default["season_pack_upgrades"],
            "expected_revision": default["revision"],
        },
    )
    assert changed.status_code == 200, changed.text

    row = next(item for item in client.get("/api/v1/upgrade-candidates").json() if item["id"] == candidate_id)
    assert row["execution_eligible"] is False
    assert "search for upgrades again" in row["execution_block_reason"].casefold()


def test_release_title_custom_format_is_scored_symmetrically_for_current_and_candidate(client: TestClient) -> None:
    headers = _login(client)
    _create_indexer(client, headers, scope="movies")
    custom_format = client.post(
        "/api/v1/custom-formats",
        headers=headers,
        json={
            "name": "Indexer Movie",
            "conditions": [{"pattern": "Indexer\\.Movie", "required": True}],
        },
    ).json()
    default = next(profile for profile in client.get("/api/v1/quality-profiles").json() if profile["is_default"])
    updated = client.patch(
        f"/api/v1/quality-profiles/{default['id']}",
        headers=headers,
        json={
            "custom_format_scores": [{"custom_format_id": custom_format["id"], "score": 500}],
            "expected_revision": default["revision"],
        },
    )
    assert updated.status_code == 200, updated.text

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name="Movies", resolved_root_path="/media/movies", media_type=MediaType.MOVIES,
                access_mode=AccessMode.READ_WRITE,
            )
            movie = Movie(title="Indexer Movie", sort_title="Indexer Movie", year=2024, tmdb_id=8104)
            db.add_all([root, movie])
            await db.flush()
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name="Indexer.Movie.2024.1080p.WEB-DL.x264-GRP",
                tracker_title="Indexer.Movie.2024.1080p.WEB-DL.x264-GRP",
                selection_snapshot={"indexer_name": "Upgrade Indexer"},
            )
            db.add(release)
            await db.flush()
            db.add(MediaDirectory(storage_root_id=root.id, resolved_path="/media/movies/Indexer Movie", movie_release_id=release.id))
            await db.commit()

    asyncio.run(seed())
    _run_search(
        FakeBehavior(movie_results=[
            SearchResult(
                title="Indexer.Movie.2024.1080p.WEB-DL.x264-GRP",
                guid="same-indexer-same-score",
                download_url="http://upgrade-indexer.test/same",
                size=100,
                seeders=10,
                published=None,
            )
        ])
    )

    assert client.get("/api/v1/upgrade-candidates?media_type=movies").json() == []


def test_upgrade_response_exposes_current_custom_format_breakdown(client: TestClient) -> None:
    headers = _login(client)
    _create_indexer(client, headers, scope="movies")
    custom_format = client.post(
        "/api/v1/custom-formats",
        headers=headers,
        json={
            "name": "Group GRP",
            "conditions": [{"pattern": "-GRP$"}],
        },
    ).json()
    default = next(profile for profile in client.get("/api/v1/quality-profiles").json() if profile["is_default"])
    updated = client.patch(
        f"/api/v1/quality-profiles/{default['id']}",
        headers=headers,
        json={
            "custom_format_scores": [{"custom_format_id": custom_format["id"], "score": 100}],
            "expected_revision": default["revision"],
        },
    )
    assert updated.status_code == 200, updated.text

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name="Movies", resolved_root_path="/media/movies", media_type=MediaType.MOVIES,
                access_mode=AccessMode.READ_WRITE,
            )
            movie = Movie(title="Breakdown Movie", sort_title="Breakdown Movie", year=2024, tmdb_id=8105)
            db.add_all([root, movie])
            await db.flush()
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name="Breakdown.Movie.2024.1080p.WEB-DL.x264-GRP",
                tracker_title="Breakdown.Movie.2024.1080p.WEB-DL.x264-GRP",
            )
            db.add(release)
            await db.flush()
            db.add(MediaDirectory(storage_root_id=root.id, resolved_path="/media/movies/Breakdown Movie", movie_release_id=release.id))
            await db.commit()

    asyncio.run(seed())
    _run_search(
        FakeBehavior(movie_results=[
            SearchResult(
                title="Breakdown.Movie.2024.2160p.WEB-DL.x264-GRP",
                guid="quality-with-same-format",
                download_url="http://upgrade-indexer.test/breakdown",
                size=100,
                seeders=10,
                published=None,
            )
        ])
    )

    rows = client.get("/api/v1/upgrade-candidates?media_type=movies").json()
    assert len(rows) == 1
    assert rows[0]["current_custom_format_score"] == 100
    assert any(item["custom_format_name"] == "Group GRP" and item["contribution"] == 100 for item in rows[0]["current_matched_formats"])
