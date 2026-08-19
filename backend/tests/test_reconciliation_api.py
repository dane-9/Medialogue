"""Persistence/API workflows for Part 8 reconciliation."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.qbittorrent import TorrentObservation
from app.integrations.tmdb import TMDBMovieMatch
from app.main import create_app
from app.models.domain import (
    AssociationType,
    Event,
    IdentityState,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    PlexMatchState,
    PlexObservation,
    Problem,
    ProblemStatus,
    ReleaseState,
    Torrent,
)
from app.services.library_scan import storage_root_scan_running, _root_locks


@pytest.fixture
def client():
    db_path = tempfile.mktemp(prefix="medialogue-reconciliation-", suffix=".db", dir=os.getcwd())
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


@dataclass
class FakeQBitBehavior:
    torrents: list[TorrentObservation] = field(default_factory=list)

    def factory(self, url: str, username: str, password: str) -> "FakeQBitClient":
        return FakeQBitClient(self)


class FakeQBitClient:
    def __init__(self, behavior: FakeQBitBehavior):
        self.behavior = behavior

    async def list_torrents(self) -> list[TorrentObservation]:
        return list(self.behavior.torrents)

    async def health(self) -> dict[str, str]:
        return {"status": "healthy", "version": "fake"}

    async def close(self) -> None:
        return None


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "adminadmin"},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _enable_operations(client: TestClient, headers: dict[str, str]) -> None:
    response = client.put("/api/v1/operations", headers=headers, json={"enabled": True})
    assert response.status_code == 200, response.text


def _torrent(
    info_hash: str,
    name: str,
    *,
    progress: float,
    state: str = "downloading",
    root: Path,
) -> TorrentObservation:
    return TorrentObservation(
        info_hash=info_hash,
        name=name,
        progress=progress,
        state=state,
        save_path=str(root),
        content_path=str(root / name),
        category="movies",
        tags=("managed",),
        tracker="https://tracker.example/announce",
        total_size=10_000,
        added_at=1_700_000_000,
        completed_at=1_700_000_100 if progress >= 1 else None,
    )


def _install_qbit_fake(client: TestClient, behavior: FakeQBitBehavior) -> None:
    from app.api import downloads as downloads_api

    client.app.dependency_overrides[downloads_api.get_qbit_client_factory] = lambda: behavior.factory


def _create_qbit(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/v1/download-clients",
        headers=headers,
        json={
            "name": "qbit-movies-1",
            "url": "http://qbit.test:8080",
            "username": "media",
            "password": "qbit-secret",
            "scope": "movies",
            "category": "movies",
            "tags": ["managed"],
            "enabled": True,
            "poll_interval_seconds": 15,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _scan(client: TestClient, headers: dict[str, str], root_id: str) -> dict:
    response = client.post(f"/api/v1/storage-roots/{root_id}/scan", headers=headers)
    assert response.status_code == 202, response.text
    job = client.get(f"/api/v1/jobs/{response.json()['job_id']}")
    assert job.status_code == 200, job.text
    return job.json()


def _movie_setup(client: TestClient, *, release_name: str = "Inception 2010 1080p BluRay REMUX AVC DTS-HD MA 5.1-K"):
    headers = _login(client)
    _configure_tmdb(client, headers)
    _enable_operations(client, headers)
    root = Path.cwd() / f"reconciliation-fixture-{os.urandom(8).hex()}"
    release_dir = root / release_name
    release_dir.mkdir(parents=True)
    (release_dir / f"{release_name}.mkv").write_bytes(b"old-media")
    configured = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": f"Movies-{os.urandom(8).hex()}", "path": str(root), "media_type": "movies"},
    )
    assert configured.status_code == 201, configured.text
    root_payload = configured.json()
    job = _scan(client, headers, root_payload["id"])
    assert job["status"] == "completed", job
    movie = client.get("/api/v1/movies").json()["items"][0]
    return headers, root, release_dir, root_payload, movie


def _db_read(read):
    async def run():
        async with db_session.async_session_factory() as db:
            return await read(db)

    return asyncio.run(run())


def _movie_revision(movie_id: str) -> int:
    async def read(db):
        movie = await db.get(Movie, UUID(movie_id))
        return movie.revision

    return _db_read(read)


def test_incoming_replacement_is_provisional_and_cancellation_is_historical(client: TestClient) -> None:
    headers, root, old_dir, root_payload, movie = _movie_setup(client)
    try:
        shutil.rmtree(old_dir)
        _scan(client, headers, root_payload["id"])
        _scan(client, headers, root_payload["id"])
        assert client.get(f"/api/v1/movies/{movie['id']}").json()["state"] == "Missing"

        behavior = FakeQBitBehavior(
            torrents=[
                _torrent(
                    "incoming-hash",
                    "Inception 2010 Directors Cut 2160p UHD BluRay REMUX HEVC TrueHD-K",
                    progress=0.62,
                    root=root,
                )
            ]
        )
        _install_qbit_fake(client, behavior)
        qbit = _create_qbit(client, headers)
        try:
            observed = client.post(f"/api/v1/download-clients/{qbit['id']}/poll", headers=headers)
            assert observed.status_code == 200, observed.text
            assert observed.json()["added"] == 1
            status = client.get("/api/v1/reconciliation/status")
            assert status.status_code == 200, status.text
            assert status.json()["incoming_count"] == 1

            incoming = _db_read(
                lambda db: db.scalar(
                    select(MovieReleaseTorrent).where(MovieReleaseTorrent.association_type == AssociationType.INCOMING)
                )
            )
            release_state = _db_read(lambda db: db.get(MovieRelease, incoming.movie_release_id))
            assert incoming is not None
            assert release_state.release_state is ReleaseState.MISSING

            behavior.torrents = []
            cancelled = client.post(f"/api/v1/download-clients/{qbit['id']}/poll", headers=headers)
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["removed"] == 1
            assert client.get("/api/v1/reconciliation/status").json()["incoming_count"] == 0
            assert client.get(f"/api/v1/movies/{movie['id']}").json()["state"] == "Missing"

            association, release, event_count = _db_read(
                lambda db: _cancelled_state(db, incoming.movie_release_id, incoming.torrent_id)
            )
            assert association.association_type is AssociationType.HISTORICAL
            assert release.release_state is ReleaseState.REMOVED
            assert event_count == 1
        finally:
            client.app.dependency_overrides.clear()
    finally:
        if root.exists():
            shutil.rmtree(root)


async def _cancelled_state(db, release_id, torrent_id):
    association = await db.scalar(
        select(MovieReleaseTorrent).where(
            MovieReleaseTorrent.movie_release_id == release_id,
            MovieReleaseTorrent.torrent_id == torrent_id,
        )
    )
    release = await db.get(MovieRelease, release_id)
    event_count = await db.scalar(
        select(func.count()).select_from(Event).where(Event.event_type == "download.cancelled")
    )
    return association, release, event_count


def test_completed_inception_replacement_promotes_same_release_and_is_idempotent(client: TestClient) -> None:
    headers, root, old_dir, root_payload, movie = _movie_setup(client)
    new_name = "Inception 2010 Directors Cut 2160p UHD BluRay REMUX HEVC TrueHD-K"
    new_dir = root / new_name
    try:
        shutil.rmtree(old_dir)
        _scan(client, headers, root_payload["id"])
        _scan(client, headers, root_payload["id"])
        behavior = FakeQBitBehavior(
            torrents=[_torrent("replacement-hash", new_name, progress=0.62, root=root)]
        )
        _install_qbit_fake(client, behavior)
        qbit = _create_qbit(client, headers)
        try:
            incoming = client.post(f"/api/v1/download-clients/{qbit['id']}/poll", headers=headers)
            assert incoming.status_code == 200, incoming.text
            new_dir.mkdir()
            (new_dir / f"{new_name}.mkv").write_bytes(b"new-media")
            behavior.torrents = [_torrent("replacement-hash", new_name, progress=1, state="uploading", root=root)]
            completed = client.post(f"/api/v1/download-clients/{qbit['id']}/poll", headers=headers)
            assert completed.status_code == 200, completed.text
            assert completed.json()["completed"] == 1

            detail = client.get(f"/api/v1/movies/{movie['id']}").json()
            assert detail["state"] == "Present"
            assert {release["state"] for release in detail["releases"]} == {"current", "replaced"}
            current = next(release for release in detail["releases"] if release["state"] == "current")
            assert current["edition"] == "Director's Cut"
            assert current["directories"][0]["resolved_path"] == str(new_dir.resolve())

            # Look up by the unique info hash to verify the provisional row was
            # promoted in place rather than replaced by a second association.
            association = _db_read(lambda db: _attached_for_hash(db, "replacement-hash"))
            assert association.association_type is AssociationType.ATTACHED
            assert str(association.movie_release_id) == str(UUID(current["id"]))

            events_before = client.get(f"/api/v1/events?entity_type=movie&entity_id={movie['id']}").json()["items"]
            replaced_before = [event for event in events_before if event["event_type"] == "release.replaced"]
            present_before = [event for event in events_before if event["event_type"] == "media.present"]
            assert len(replaced_before) == len(present_before) == 1
            repeated = client.post(f"/api/v1/download-clients/{qbit['id']}/poll", headers=headers)
            assert repeated.status_code == 200, repeated.text
            events_after = client.get(f"/api/v1/events?entity_type=movie&entity_id={movie['id']}").json()["items"]
            assert len([event for event in events_after if event["event_type"] == "release.replaced"]) == 1
            assert len([event for event in events_after if event["event_type"] == "media.present"]) == 1
        finally:
            client.app.dependency_overrides.clear()
    finally:
        if root.exists():
            shutil.rmtree(root)


async def _attached_for_hash(db, info_hash: str):
    return await db.scalar(
        select(MovieReleaseTorrent)
        .join_from(MovieReleaseTorrent, Torrent)
        .where(Torrent.info_hash == info_hash, MovieReleaseTorrent.association_type == AssociationType.ATTACHED)
    )


def test_plex_conflict_blocks_completed_replacement(client: TestClient) -> None:
    headers, root, old_dir, root_payload, movie = _movie_setup(client)
    new_name = "Inception 2010 Directors Cut 2160p UHD BluRay REMUX HEVC TrueHD-K"
    new_dir = root / new_name
    try:
        shutil.rmtree(old_dir)
        _scan(client, headers, root_payload["id"])
        _scan(client, headers, root_payload["id"])
        behavior = FakeQBitBehavior(torrents=[_torrent("conflict-hash", new_name, progress=0.5, root=root)])
        _install_qbit_fake(client, behavior)
        qbit = _create_qbit(client, headers)
        try:
            client.post(f"/api/v1/download-clients/{qbit['id']}/poll", headers=headers)
            incoming_release_id = _db_read(lambda db: _incoming_release_id(db, "conflict-hash"))
            _db_read(lambda db: _insert_plex_conflict(db, movie["id"], incoming_release_id, str(new_dir.resolve())))
            new_dir.mkdir()
            (new_dir / f"{new_name}.mkv").write_bytes(b"wrong-media")
            behavior.torrents = [_torrent("conflict-hash", new_name, progress=1, state="uploading", root=root)]
            completed = client.post(f"/api/v1/download-clients/{qbit['id']}/poll", headers=headers)
            assert completed.status_code == 200, completed.text
            detail = client.get(f"/api/v1/movies/{movie['id']}").json()
            assert detail["state"] == "Conflict"
            assert "current" not in {release["state"] for release in detail["releases"]}
            problems = client.get("/api/v1/problems?reason=PLEX_IDENTITY_MISMATCH")
            assert problems.status_code == 200, problems.text
            assert problems.json()["total"] == 1
            assert problems.json()["items"][0]["status"] == "open"
        finally:
            client.app.dependency_overrides.clear()
    finally:
        if root.exists():
            shutil.rmtree(root)


async def _incoming_release_id(db, info_hash: str):
    return await db.scalar(
        select(MovieReleaseTorrent.movie_release_id)
        .join_from(MovieReleaseTorrent, Torrent)
        .where(Torrent.info_hash == info_hash, MovieReleaseTorrent.association_type == AssociationType.INCOMING)
    )


async def _insert_plex_conflict(db, movie_id: str, release_id, path: str):
    db.add(
        PlexObservation(
            media_type=MediaType.MOVIES,
            movie_id=UUID(movie_id),
            movie_release_id=release_id,
            match_state=PlexMatchState.CONFLICT,
            resolved_path=path,
            plex_title="The Wrong Movie",
            plex_year=2001,
        )
    )
    await db.commit()


def test_root_outage_problem_event_is_idempotent_and_restores(client: TestClient) -> None:
    headers, root, old_dir, root_payload, movie = _movie_setup(client)
    try:
        shutil.rmtree(root)
        first = _scan(client, headers, root_payload["id"])
        second = _scan(client, headers, root_payload["id"])
        assert first["status"] == second["status"] == "failed"
        assert client.get(f"/api/v1/movies/{movie['id']}").json()["state"] == "Present"
        outage_events = client.get(
            f"/api/v1/events?entity_type=storage_root&entity_id={root_payload['id']}"
        ).json()["items"]
        assert len([event for event in outage_events if event["event_type"] == "storage_root.unavailable"]) == 1
        open_problem = client.get("/api/v1/problems?reason=ROOT_UNREACHABLE&status=open").json()
        assert open_problem["total"] == 1

        root.mkdir()
        restored = _scan(client, headers, root_payload["id"])
        assert restored["status"] == "completed"
        restored_events = client.get(
            f"/api/v1/events?entity_type=storage_root&entity_id={root_payload['id']}"
        ).json()["items"]
        assert len([event for event in restored_events if event["event_type"] == "storage_root.restored"]) == 1
        resolved = client.get("/api/v1/problems?reason=ROOT_UNREACHABLE&status=resolved").json()
        assert resolved["total"] == 1
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_manual_attach_expected_revision_race_is_rejected(client: TestClient) -> None:
    headers, root, old_dir, root_payload, movie = _movie_setup(client)
    try:
        revision = _movie_revision(movie["id"])
        first = client.post(
            f"/api/v1/reconciliation/movies/{movie['id']}/manual-attach",
            headers=headers,
            json={"root_id": root_payload["id"], "path": str(old_dir), "expected_revision": revision},
        )
        assert first.status_code == 200, first.text
        assert first.json()["details"]["revision"] == revision + 1
        stale = client.post(
            f"/api/v1/reconciliation/movies/{movie['id']}/manual-attach",
            headers=headers,
            json={"root_id": root_payload["id"], "path": str(old_dir), "expected_revision": revision},
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
        assert _movie_revision(movie["id"]) == revision + 1
        state = _db_read(lambda db: db.get(Movie, UUID(movie["id"])))
        assert state.identity_state is IdentityState.MANUAL
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_reconciliation_refresh_status_and_root_overlap_lock(client: TestClient) -> None:
    headers, root, old_dir, root_payload, movie = _movie_setup(client)
    root_id = UUID(root_payload["id"])
    lock = _root_locks[root_id]
    try:
        run = client.post(
            "/api/v1/reconciliation/run",
            headers=headers,
            json={"root_id": root_payload["id"]},
        )
        assert run.status_code == 202, run.text
        assert len(run.json()["job_ids"]) == 1
        assert run.json()["skipped_root_ids"] == []
        run_job = client.get(f"/api/v1/jobs/{run.json()['job_ids'][0]}")
        assert run_job.status_code == 200, run_job.text
        assert run_job.json()["status"] == "completed"

        status = client.get("/api/v1/reconciliation/status")
        assert status.status_code == 200, status.text
        assert status.json()["roots"][0]["health"] == "available"
        assert not storage_root_scan_running(root_id)
        asyncio.run(lock.acquire())
        assert storage_root_scan_running(root_id)
        # The refresh/status route remains responsive while another run owns
        # the per-root lock; it reports the last committed observation.
        refreshed = client.get("/api/v1/reconciliation/status")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["roots"][0]["affected_count"] == 1
    finally:
        if lock.locked():
            lock.release()
        if root.exists():
            shutil.rmtree(root)


def test_completed_in_scope_manual_qbit_torrent_can_create_tmdb_backed_movie(client: TestClient) -> None:
    headers = _login(client)
    _configure_tmdb(client, headers)
    _enable_operations(client, headers)
    root = Path.cwd() / f"qbit-new-movie-{os.urandom(8).hex()}"
    release_name = "Inception 2010 2160p UHD BluRay REMUX HEVC TrueHD 7.1-GROUP"
    release_dir = root / release_name
    release_dir.mkdir(parents=True)
    (release_dir / f"{release_name}.mkv").write_bytes(b"media")
    configured = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": "qBit new movies", "path": str(root), "media_type": "movies"},
    )
    assert configured.status_code == 201, configured.text

    behavior = FakeQBitBehavior(
        torrents=[_torrent("brand-new-hash", release_name, progress=1.0, state="pausedUP", root=root)]
    )
    _install_qbit_fake(client, behavior)
    qbit = _create_qbit(client, headers)
    try:
        observed = client.post(f"/api/v1/download-clients/{qbit['id']}/poll", headers=headers)
        assert observed.status_code == 200, observed.text
        assert observed.json()["completed"] == 1
        movies = client.get("/api/v1/movies").json()
        assert movies["total"] == 1
        assert movies["items"][0]["tmdb_id"] == 27205
        assert movies["items"][0]["state"] == "Present"
    finally:
        client.app.dependency_overrides.clear()
        if root.exists():
            shutil.rmtree(root)
