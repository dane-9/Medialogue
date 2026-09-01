import asyncio
import os
from dataclasses import dataclass, field
from datetime import date

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
    IdentityState,
    Job,
    JobStatus,
    MediaDirectory,
    MediaType,
    Movie,
    MovieRelease,
    PresenceState,
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
    from uuid import UUID

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
                access_mode=AccessMode.READ_ONLY,
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
    assert row["execution_eligible"] is False
    assert "read-only" in row["execution_block_reason"]
    assert "download_url" not in row
    assert "guid" not in row

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
