import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import httpx
import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.db.base import Base
from app.models.domain import MediaType, Torrent, TorrentClientObservation, TorrentTrackerObservation
from app.services.backup_crypto import new_recovery_key, encrypt_backup, decrypt_backup
from app.services.remote_backups import DriveBackups, retained_ids
from app.services.torrent_archive import manifest_for_client, recovery_file_priorities, recovery_revision
from app.services.replacements import lost_sources, releases_without_any_healthy_source
from app.services.upgrade_candidates import CurrentReleaseEvidence, UpgradeTarget, _score_candidate
from app.services.search import SearchTarget
from app.integrations.torznab import SearchResult
from tests.test_part17_recovery import recovery_env, login_headers


def test_encryption_is_portable_and_rejects_wrong_key_and_tampering(tmp_path):
    source = tmp_path / "snapshot.zip"
    source.write_bytes(b"credentials and torrent data" * 100000)
    encrypted = tmp_path / "snapshot.mlbackup"
    key = new_recovery_key()
    encrypt_backup(source, encrypted, key)
    assert b"credentials" not in encrypted.read_bytes()
    restored = tmp_path / "restored.zip"
    decrypt_backup(encrypted, restored, key)
    assert restored.read_bytes() == source.read_bytes()
    for bad_key, mutate in [(new_recovery_key(), False), (key, True)]:
        if mutate:
            payload = bytearray(encrypted.read_bytes()); payload[80] ^= 1; encrypted.write_bytes(payload)
        output = tmp_path / "untrusted.zip"
        with pytest.raises(InvalidTag):
            decrypt_backup(encrypted, output, bad_key)
        assert not output.exists()
        assert not output.with_name("untrusted.zip.decrypting").exists()
    original = encrypted.read_bytes()
    with pytest.raises(FileExistsError):
        encrypt_backup(source, encrypted, key)
    assert encrypted.read_bytes() == original


def test_retention_keeps_daily_and_weekly_points_without_emptying_backup_set():
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    rows = [{"id": str(i), "createdTime": (now - timedelta(days=i)).isoformat()} for i in range(60)]
    keep = retained_ids(rows, 7, 4)
    assert {str(i) for i in range(7)} <= keep
    assert "0" in keep
    assert 7 <= len(keep) <= 11
    assert "59" not in keep


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
async def test_google_upload_resumes_after_network_interruption(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.mlbackup"
    path.write_bytes(b"x" * (5 * 1024 * 1024))
    drive = DriveBackups("token")
    await drive.client.aclose()
    calls = []
    interrupted = False
    def handler(request):
        nonlocal interrupted
        calls.append(request.headers.get("Content-Range"))
        if request.method == "POST":
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/session"})
        if request.headers["Content-Range"].startswith("bytes */"):
            return httpx.Response(308, headers={"Range": "bytes=0-4194303"})
        if not interrupted:
            interrupted = True
            raise httpx.ReadError("lost response", request=request)
        return httpx.Response(200, json={"id": "uploaded"})
    drive.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async def folder(): return "folder"
    async def no_sleep(_): pass
    monkeypatch.setattr(drive, "folder", folder)
    monkeypatch.setattr("app.services.remote_backups.read_backup_settings", lambda: {"instance_id": "test"})
    monkeypatch.setattr("app.services.remote_backups.asyncio.sleep", no_sleep)
    try:
        assert (await drive.upload(path, "snapshot"))["id"] == "uploaded"
        assert calls[-1] == f"bytes 4194304-{path.stat().st_size - 1}/{path.stat().st_size}"
    finally:
        await drive.close()


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


def test_cross_seed_release_is_not_lost_while_any_torrent_source_is_healthy():
    failed = SimpleNamespace(id=uuid4())
    healthy = SimpleNamespace(id=uuid4())
    release = SimpleNamespace(torrents=[failed, healthy])

    assert releases_without_any_healthy_source([release], {failed.id: failed}) == []
    assert releases_without_any_healthy_source(
        [release],
        {failed.id: failed, healthy.id: healthy},
    ) == [failed, healthy]


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


def test_restore_staging_rejects_zip_traversal_and_preserves_destination(tmp_path):
    from scripts.restore_backup import stage_restore
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escaped", "bad")
    with pytest.raises(ValueError, match="Unsafe"):
        stage_restore(archive, tmp_path / "restored")
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path / "restored").exists()


def _archive_fixture(root):
    info_hash = "ab" + "c" * 38
    payload = b"d4:infod4:name7:exampleee"
    manifest = {"schema_version": 1, "torrent_info_hash": info_hash, "torrent_name": "Example",
        "archive": {"torrent_sha256": hashlib.sha256(payload).hexdigest()},
        "recovery": {"properties": {}, "files": [{"index": 0, "priority": 0}], "trackers": []},
        "media_associations": [{"media_type": "movies", "title": "Example"}]}
    torrent = root / "torrents/ab" / f"{info_hash}.torrent"
    metadata = root / "manifests/ab" / f"{info_hash}.json"
    torrent.parent.mkdir(parents=True, exist_ok=True); metadata.parent.mkdir(parents=True, exist_ok=True)
    torrent.write_bytes(payload); metadata.write_text(json.dumps(manifest))
    return info_hash


def test_fresh_database_rebuilds_catalogue_without_inventing_live_presence(recovery_env):
    client, settings, root = recovery_env
    headers = login_headers(client)
    info_hash = _archive_fixture(Path(settings.torrent_archive_dir))
    response = client.post("/api/v1/torrent-archive/rebuild", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"imported": 1, "skipped": 0, "errors": []}
    rows = client.get("/api/v1/torrent-archive").json()["items"]
    assert rows[0]["info_hash"] == info_hash
    assert rows[0]["archive_verified"] is True
    assert rows[0]["qbit_present"] is False
    assert rows[0]["source_state"] == "unknown"
    assert rows[0]["media_title"] == "Example"
    assert client.post("/api/v1/torrent-archive/rebuild", headers=headers).json()["skipped"] == 1


def test_remote_backup_roundtrip_and_failed_verification_preserve_previous_backup(recovery_env, monkeypatch):
    from app.db import session as db_session
    from app.models.domain import Job, JobStatus
    from app.services.jobs import create_job
    from app.services import remote_backups as remote
    from scripts.restore_backup import stage_restore
    client, settings, root = recovery_env
    headers = login_headers(client)
    info_hash = _archive_fixture(Path(settings.torrent_archive_dir))
    key = new_recovery_key()
    remote.update_backup_settings({"recovery_key": key, "refresh_token": "test-refresh-token"})
    saved = root / "remote.mlbackup"
    operations = []
    bad_checksum = False
    class FakeDrive:
        def __init__(self, token): pass
        async def close(self): pass
        async def upload(self, path, snapshot_id):
            saved.write_bytes(path.read_bytes())
            return {"id": "new-snapshot"}
        async def request(self, method, url, **kwargs):
            operations.append((method, kwargs))
            return httpx.Response(200, json={"id": "new-snapshot", "size": str(saved.stat().st_size),
                "sha256Checksum": "wrong" if bad_checksum else hashlib.sha256(saved.read_bytes()).hexdigest(),
                "appProperties": {"instance": remote.read_backup_settings()["instance_id"]}})
        async def list(self): return []
    async def token(): return "test-access-token"
    monkeypatch.setattr(remote, "DriveBackups", FakeDrive)
    monkeypatch.setattr(remote, "access_token", token)
    async def run():
        async with db_session.async_session_factory() as db:
            job = await create_job(db, "remote_backup", cancellable=False)
            await db.commit(); job_id = job.id
        await remote.run_remote_backup(job_id)
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            return job.status
    assert asyncio.run(run()) == JobStatus.COMPLETED
    assert remote.read_backup_settings()["coverage"][info_hash]
    destination = root / "restored"
    metadata = stage_restore(saved, destination, key)
    assert metadata["recovery_schema_version"] == 1
    assert (destination / "database/medialogue.db").is_file()
    assert (destination / "torrent-archive/torrents/ab" / f"{info_hash}.torrent").is_file()
    assert not list(Path(settings.recovery_export_dir).glob("*.zip"))
    # Automatic export children cannot bypass the manual-export password gate.
    async def export_id():
        from sqlalchemy import select
        async with db_session.async_session_factory() as db:
            return await db.scalar(select(Job.id).where(Job.job_type == "recovery_export").limit(1))
    assert client.get(f"/api/v1/recovery/exports/{asyncio.run(export_id())}/download").status_code == 409
    previous = remote.read_backup_settings()["last_success_at"]
    operations.clear(); bad_checksum = True
    assert asyncio.run(run()) == JobStatus.FAILED
    assert remote.read_backup_settings()["last_success_at"] == previous
    assert not any(method == "PATCH" for method, _ in operations)


def test_backup_key_endpoint_requires_authentication_and_is_not_cached(recovery_env):
    client, _, _ = recovery_env
    assert client.post("/api/v1/backups/recovery-key").status_code == 401
    headers = login_headers(client)
    assert client.post("/api/v1/backups/recovery-key").status_code == 403
    response = client.post("/api/v1/backups/recovery-key", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "recovery_key" not in client.get("/api/v1/backups/status").json()


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
        await run_replacement_search(job_id, target_key=item["target_key"], client_factory=behavior.factory)
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            assert job.status == JobStatus.COMPLETED, job.error
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
