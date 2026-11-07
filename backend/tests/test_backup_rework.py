import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.db.base import Base
from app.models.domain import MediaType, Torrent, TorrentClientObservation, TorrentTrackerObservation
from app.services.torrent_archive import manifest_for_client, recovery_file_priorities, recovery_revision
from app.services.replacements import lost_sources
from app.services.upgrade_candidates import CurrentReleaseEvidence, UpgradeTarget, _score_candidate
from app.services.search import SearchTarget
from app.integrations.torznab import SearchResult
from tests.test_part17_recovery import recovery_env, login_headers


def test_client_file_selections_remain_distinct_and_coverage_ignores_progress():
    a, b = str(uuid4()), str(uuid4())
    manifest = {"client_recovery": {
        a: {"files": [{"index": 0, "priority": 0}], "settings": {}},
        b: {"files": [{"index": 0, "priority": 7}], "settings": {}},
    }}
    assert recovery_file_priorities(manifest_for_client(manifest, a)) == {0: 0}
    assert recovery_file_priorities(manifest_for_client(manifest, b)) == {0: 7}
    before = recovery_revision(manifest)
    manifest["client_recovery"][a]["captured_at"] = "later"
    manifest["client_recovery"][a]["files"][0]["progress"] = 0.8
    assert recovery_revision(manifest) == before
    manifest["client_recovery"][a]["files"][0]["priority"] = 1
    assert recovery_revision(manifest) != before


@pytest.mark.asyncio
async def test_source_failure_requires_recent_terminal_evidence_on_all_live_clients():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        torrent = Torrent(info_hash="a" * 40, name="Movie")
        db.add(torrent); await db.flush()
        client_id = uuid4()
        observation = TorrentClientObservation(torrent_id=torrent.id, download_client_id=client_id,
            is_present=True, last_seen_at=now)
        tracker = TorrentTrackerObservation(torrent_id=torrent.id, download_client_id=client_id,
            endpoint_key="a", endpoint_display="https://tracker.test", active=True,
            health_state="error", terminal_confirmed=True, last_checked_at=now)
        db.add_all([observation, tracker]); await db.commit()
        assert torrent.id in await lost_sources(db)
        tracker.terminal_confirmed = False; await db.flush()
        assert not await lost_sources(db)
        tracker.terminal_confirmed = True; tracker.last_checked_at = now - timedelta(days=1); await db.flush()
        assert not await lost_sources(db)
        tracker.last_checked_at = now; observation.is_present = False; await db.flush()
        assert not await lost_sources(db)
        observation.is_present = True
        second = TorrentClientObservation(torrent_id=torrent.id, download_client_id=uuid4(), is_present=True, last_seen_at=now)
        db.add(second); await db.flush()
        db.expire(torrent, ["observations"])
        assert not await lost_sources(db)
    await engine.dispose()


def test_replacement_allows_equivalent_but_blocks_quality_score_edition_and_hash_regressions():
    evidence = CurrentReleaseEvidence(release_id=uuid4(), release_name="Movie.2024.1080p.WEB-DL-GRP",
        quality="1080p WEB-DL", quality_preference=1, custom_format_score=0, score_snapshot={},
        parse_snapshot={}, release_group="GRP")
    target = UpgradeTarget(target_key="replace:test", media_type=MediaType.MOVIES, profile_entity_id=uuid4(),
        movie_id=uuid4(), season_id=None, search_target=SearchTarget(media_type=MediaType.MOVIES,
        entity_type="movie", entity_id=uuid4(), title="Movie", year=2024), releases=(evidence,),
        reference=evidence, execution_eligible=True, execution_block_reason=None, season_pack_upgrades=False,
        replacement=True, excluded_hashes=("a" * 40,))
    from app.parser import parse_release
    high = parse_release("Movie.2024.2160p.WEB-DL-GRP").quality.canonical
    equal = parse_release(evidence.release_name).quality.canonical
    low = parse_release("Movie.2024.720p.WEB-DL-GRP").quality.canonical
    profile = SimpleNamespace(quality_order=(high, equal, low), profile_scores={}, overrides={},
        minimum_custom_format_score=None, minimum_quality_name=None, snapshot=lambda: {})
    indexer = SimpleNamespace(id=uuid4(), name="Tracker", priority=1, minimum_seeders=1)
    result = SearchResult(guid="different", title=evidence.release_name,
        download_url="magnet:?xt=urn:btih:" + "b" * 40, size=100, seeders=3, published=None)
    def score(t=target, r=result):
        return _score_candidate(target=t, profile=profile, formats=[], indexer=indexer, result=r)
    assert score() is not None
    assert score(t=replace(target, replacement=False)) is None
    assert score(r=replace(result, title="Movie.2024.720p.WEB-DL-GRP")) is None
    assert score(r=replace(result, title="Movie.2024.Directors.Cut.1080p.WEB-DL-GRP")) is None
    assert score(r=replace(result, download_url="magnet:?xt=urn:btih:" + "a" * 40)) is None
    expensive = replace(evidence, custom_format_score=20)
    assert score(t=replace(target, reference=expensive, releases=(expensive,)),
                 r=replace(result, title="Movie.2024.2160p.WEB-DL-GRP")) is None


def test_replacement_queue_search_review_and_submission_share_existing_execution(recovery_env):
    from uuid import UUID
    from app.db import session as db_session
    from app.models.domain import Job, JobStatus, UpgradeCandidate
    from app.services.jobs import create_job
    from app.services.replacements import run_replacement_search
    from tests.test_upgrade_execution import _create_integrations, _seed_movie_upgrade, FakeQBitBehavior
    from tests.test_upgrade_candidates import FakeBehavior
    from app.api import downloads as downloads_api
    client, _, root = recovery_env
    headers = login_headers(client)
    qbit, indexer = _create_integrations(client, headers)
    _, _, _, old_torrent_id, old_dir, _ = asyncio.run(_seed_movie_upgrade(root, qbit, indexer))
    async def source_failure():
        async with db_session.async_session_factory() as db:
            db.add(TorrentTrackerObservation(torrent_id=old_torrent_id, download_client_id=UUID(qbit["id"]),
                endpoint_key="removed", endpoint_display="https://tracker.test", active=True,
                health_state="error", terminal_confirmed=True, last_checked_at=datetime.now(timezone.utc)))
            await db.commit()
    asyncio.run(source_failure())
    queue = client.get("/api/v1/replacements")
    assert queue.status_code == 200, queue.text
    item = queue.json()["items"][0]
    assert item["target_key"].startswith("replace:")
    assert item["candidate_id"] is None
    behavior = FakeBehavior(movie_results=[
        SearchResult(guid="equal", title="Upgrade.Movie.2024.1080p.WEB-DL-GRP",
            download_url="magnet:?xt=urn:btih:" + "b" * 40, size=100, seeders=5, published=None),
        SearchResult(guid="lower", title="Upgrade.Movie.2024.720p.WEB-DL-GRP",
            download_url="magnet:?xt=urn:btih:" + "c" * 40, size=90, seeders=50, published=None),
    ])
    async def search():
        async with db_session.async_session_factory() as db:
            job = await create_job(db, "search_media_replacements", cancellable=False)
            await db.commit(); job_id = job.id
        # A title new to Replace Media is searched by the "new titles" run.
        await run_replacement_search(job_id, only_new=True, client_factory=behavior.factory)
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            assert job.status == JobStatus.COMPLETED, job.error
            assert job.summary["targets"] == 1
            again = await create_job(db, "search_new_replacements", cancellable=False)
            await db.commit(); again_id = again.id
        # Once searched, it is not new any more.
        await run_replacement_search(again_id, only_new=True, client_factory=behavior.factory)
        async with db_session.async_session_factory() as db:
            assert (await db.get(Job, again_id)).summary["targets"] == 0
    asyncio.run(search())
    item = client.get("/api/v1/replacements").json()["items"][0]
    assert item["candidate_title"] == behavior.movie_results[0].title
    candidate_id = item["candidate_id"]
    results = client.get(f"/api/v1/upgrade-candidates/{candidate_id}/results")
    assert results.status_code == 200, results.text
    assert len(results.json()["results"]) == 2
    assert sum(r["eligible"] for r in results.json()["results"]) == 1
    assert "download_url" not in results.text
    fake_qbit = FakeQBitBehavior()
    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: fake_qbit.factory
    try:
        response = client.post(f"/api/v1/upgrade-candidates/{candidate_id}/upgrade", headers=headers,
            json={"download_client_id": qbit["id"], "category": "movies", "delete_old_media": False})
        assert response.status_code == 200, response.text
        assert response.json()["execution_state"] == "submitted"
        assert len(fake_qbit.submissions) == 1
        assert fake_qbit.removals == []
        assert old_dir.exists()
        assert all(row["id"] != candidate_id for row in client.get("/api/v1/upgrade-candidates").json())
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_lost_cross_seed_with_a_working_partner_is_not_listed():
    """A torrent whose tracker died, but whose release still has a working
    cross-seed, must not appear in Replace Media at all."""

    from app.models.domain import IdentityState, Movie, MovieRelease, MovieReleaseTorrent, AssociationType
    from app.services.replacements import replacement_queue

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with sessions() as db:
        movie = Movie(title="Shared", sort_title="Shared", identity_state=IdentityState.MATCHED)
        db.add(movie); await db.flush()
        release = MovieRelease(movie_id=movie.id, raw_release_name="Shared.2024.1080p")
        dead = Torrent(info_hash="d" * 40, name="Shared dead")
        alive = Torrent(info_hash="e" * 40, name="Shared alive")
        db.add_all([release, dead, alive]); await db.flush()
        client_id = uuid4()
        for torrent in (dead, alive):
            db.add(MovieReleaseTorrent(movie_release_id=release.id, torrent_id=torrent.id,
                                       association_type=AssociationType.ATTACHED))
            db.add(TorrentClientObservation(torrent_id=torrent.id, download_client_id=client_id,
                                            is_present=True, last_seen_at=now))
        db.add(TorrentTrackerObservation(torrent_id=dead.id, download_client_id=client_id, endpoint_key="x",
            endpoint_display="https://dead.test", active=True, health_state="error",
            terminal_confirmed=True, last_checked_at=now))
        db.add(TorrentTrackerObservation(torrent_id=alive.id, download_client_id=client_id, endpoint_key="y",
            endpoint_display="https://alive.test", active=True, health_state="healthy",
            terminal_confirmed=False, last_checked_at=now))
        await db.commit()
        assert dead.id in await lost_sources(db)
        assert await replacement_queue(db) == []
    await engine.dispose()


def test_replacement_search_is_scoped_per_item_and_finish_is_recorded(recovery_env):
    from app.db import session as db_session
    from app.models.domain import Event, Job, JobStatus
    from sqlalchemy import select

    client, _, _ = recovery_env
    headers = login_headers(client)

    async def running(target_key):
        async with db_session.async_session_factory() as db:
            job = Job(job_type="search_media_replacements", status=JobStatus.RUNNING, cancellable=False,
                      summary={"target_key": target_key})
            db.add(job); await db.commit()
            return str(job.id)

    other = asyncio.run(running("replace:movie-release:other"))
    from app.api import replacements as replacements_api
    launched: list[str] = []
    original = replacements_api.launch_runtime_job
    replacements_api.launch_runtime_job = lambda job_id, factory: launched.append(str(job_id)) or True
    try:
        # A different item's running search must not answer this request.
        mine = client.post("/api/v1/replacements/search", headers=headers, json={"target_key": "replace:movie-release:mine"})
        assert mine.status_code == 202 and mine.json()["job_id"] != other
        # Asking again for the same item reuses its own job.
        again = client.post("/api/v1/replacements/search", headers=headers, json={"target_key": "replace:movie-release:mine"})
        assert again.json()["job_id"] == mine.json()["job_id"]
    finally:
        replacements_api.launch_runtime_job = original
    assert launched == [mine.json()["job_id"]]

    queue = client.get("/api/v1/replacements")
    assert queue.status_code == 200, queue.text
    assert set(queue.json()["counts"]) == {"needs_search", "found", "in_progress", "dismissed"}
    assert client.get("/api/v1/replacements/count").json() == {"count": 0}


def test_title_with_a_new_download_in_qbittorrent_is_left_alone(recovery_env):
    """Removing a torrent by hand and adding its new version by hand must not
    list the title or re-add the old torrent while the new one downloads."""

    from uuid import UUID
    from app.db import session as db_session
    from app.models.domain import AssociationType, MovieRelease, MovieReleaseTorrent, Torrent, TorrentClientObservation
    from sqlalchemy import select
    from app.services.replacements import replacement_targets
    from tests.test_upgrade_execution import _create_integrations, _seed_movie_upgrade
    client, _, root = recovery_env
    headers = login_headers(client)
    qbit, indexer = _create_integrations(client, headers)
    _, movie_id, _, old_torrent_id, _, _ = asyncio.run(_seed_movie_upgrade(root, qbit, indexer))

    async def scenario():
        async with db_session.async_session_factory() as db:
            # The old torrent left qBittorrent two days ago.
            old = (await db.scalars(select(TorrentClientObservation).where(TorrentClientObservation.torrent_id == old_torrent_id))).all()
            for row in old:
                row.is_present = False
                row.removed_at = datetime.now(timezone.utc) - timedelta(days=2)
            await db.commit()
            assert [entry.reason for entry in await replacement_targets(db)] == ["not_watched"]

            # The new version, added by hand, is downloading.
            new = Torrent(info_hash="9" * 40, name="Upgrade.Movie.2024.2160p.WEB-DL-GRP")
            release = MovieRelease(movie_id=movie_id, raw_release_name=new.name)
            db.add_all([new, release]); await db.flush()
            db.add(MovieReleaseTorrent(movie_release_id=release.id, torrent_id=new.id, association_type=AssociationType.INCOMING))
            observation = TorrentClientObservation(torrent_id=new.id, download_client_id=UUID(qbit["id"]), is_present=True,
                                                   last_seen_at=datetime.now(timezone.utc))
            db.add(observation); await db.commit()
            assert await replacement_targets(db) == []

            # Removed again without finishing, the old problem shows once more.
            observation.is_present = False
            await db.commit()
            assert [entry.reason for entry in await replacement_targets(db)] == ["not_watched"]

    asyncio.run(scenario())
