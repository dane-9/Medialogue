"""qBittorrent client and observation API coverage for Part 7.

The fake below deliberately exposes only the read-only methods used by the
poller.  This keeps these tests independent of a running qBittorrent process
while still asserting the durable behavior at the API boundary.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.qbittorrent import TorrentObservation
from app.main import create_app
from app.models.domain import DownloadClient, Torrent


class _NoMatchTMDBClient:
    """Reachable TMDB that recognises nothing.

    Keeps these suites focused on their own subject: the scan completes, and
    identity simply stays unresolved.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def health(self):
        return {"status": "healthy"}

    async def search_movie(self, title: str, year: int | None = None):
        return []

    async def search_show(self, title: str, year: int | None = None):
        return []

    async def get_movie_alternative_titles(self, tmdb_id: int):
        return ()

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def _configure_fake_tmdb(monkeypatch):
    import app.api.tmdb as tmdb_api
    import app.services.tmdb as tmdb_service

    monkeypatch.setattr(tmdb_service, "TMDBClient", _NoMatchTMDBClient)
    monkeypatch.setattr(tmdb_api, "TMDBClient", _NoMatchTMDBClient)




@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
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


@dataclass
class FakeQBitBehavior:
    torrents: list[TorrentObservation] = field(default_factory=list)
    health_error: str | None = None
    version: str = "v4.6.4"
    instances: list[FakeQBitClient] = field(default_factory=list)

    def factory(self, url: str, username: str, password: str) -> FakeQBitClient:
        return FakeQBitClient(self, url, username, password)


class FakeQBitClient:
    def __init__(self, behavior: FakeQBitBehavior, url: str, username: str, password: str):
        self.behavior = behavior
        self.url = url
        self.username = username
        self.password = password
        self.closed = False
        behavior.instances.append(self)

    async def health(self) -> dict[str, str]:
        if self.behavior.health_error:
            raise RuntimeError(self.behavior.health_error)
        return {"status": "healthy", "version": self.behavior.version}

    async def list_torrents(self) -> list[TorrentObservation]:
        return list(self.behavior.torrents)

    async def close(self) -> None:
        self.closed = True


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "adminadmin"},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _configure_tmdb(client: TestClient, headers: dict[str, str]) -> None:
    """Scanning requires TMDB, so every suite that scans has to configure it."""

    response = client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
    assert response.status_code == 200, response.text


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
    raise AssertionError(f"job did not finish: {payload}")


def _poll_job(client: TestClient, url: str, headers: dict[str, str]) -> dict:
    response = client.post(url, headers=headers)
    assert response.status_code == 202, response.text
    job = _wait_job(client, response.json()["job_id"])
    assert job["status"] == "completed", job
    return job["summary"]


def _initialize_root(client: TestClient, headers: dict[str, str], root_id: str) -> None:
    response = client.post(f"/api/v1/storage-roots/{root_id}/scan", headers=headers)
    assert response.status_code == 202, response.text
    job = _wait_job(client, response.json()["job_id"])
    assert job["status"] == "completed", job


def _torrent(
    info_hash: str,
    name: str,
    *,
    progress: float = 0.2,
    state: str = "downloading",
    save_path: str = "/downloads/movies",
    content_path: str | None = None,
    category: str = "movies",
    tags: tuple[str, ...] = ("managed",),
) -> TorrentObservation:
    return TorrentObservation(
        info_hash=info_hash,
        name=name,
        progress=progress,
        state=state,
        save_path=save_path,
        content_path=content_path,
        category=category,
        tags=tags,
        tracker="https://tracker.example/announce",
        total_size=10_000,
        added_at=1_700_000_000,
        completed_at=1_700_000_100 if progress >= 1 else None,
    )


def _create_client(client: TestClient, headers: dict[str, str], **overrides) -> dict:
    payload = {
        "name": "qbit-movies-1",
        "url": "http://qbit.test:8080",
        "username": "media",
        "password": "super-secret-qbit-password",
        "scope": "movies",
        "category": "movies",
        "tags": ["managed", "archive"],
        "enabled": True,
        "poll_interval_seconds": 15,
    }
    payload.update(overrides)
    response = client.post("/api/v1/download-clients", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _install_fake(client: TestClient, behavior: FakeQBitBehavior) -> None:
    # The dependency mirrors the Plex adapter seam and accepts URL, username,
    # and password.  Keep this override local to each test to avoid leaking a
    # fake into another isolated TestClient.
    from app.api import downloads as downloads_api

    client.app.dependency_overrides[downloads_api.get_qbit_client_factory] = lambda: behavior.factory


def test_qbit_remote_mapping_preserves_local_path_case() -> None:
    from app.services.qbittorrent import _inside_local_root, resolve_remote_path

    mapping_id = uuid4()
    mapping = SimpleNamespace(
        id=mapping_id,
        remote_prefix="/Movies",
        local_prefix="/movies",
        enabled=True,
    )

    resolved, used_mapping = resolve_remote_path(
        "/Movies/Cartoons/The.Movie.2026/The.Movie.2026.mkv",
        [mapping],
    )

    assert resolved == "/movies/Cartoons/The.Movie.2026/The.Movie.2026.mkv"
    assert used_mapping == mapping_id

    # An unmatched remote qBit path must not be mistaken for the Linux-local
    # /movies mount merely because the comparison used to case-fold both.
    assert _inside_local_root("/Movies/Movies/Unmanaged.Movie.2026", "/movies") is False


def test_download_client_crud_redacts_password_and_preserves_multiple_scopes(client: TestClient) -> None:
    headers = _login(client)
    movie = _create_client(client, headers)
    show_one = _create_client(
        client,
        headers,
        name="qbit-shows-1",
        scope="shows",
        url="http://qbit-shows-1.test:8080",
        password="show-secret-1",
    )
    show_two = _create_client(
        client,
        headers,
        name="qbit-shows-2",
        scope="shows",
        url="http://qbit-shows-2.test:8080",
        password="show-secret-2",
    )

    assert movie["scope"] == "movies"
    assert movie["password_configured"] is True
    assert "password" not in movie
    assert "super-secret-qbit-password" not in str(movie)
    assert {show_one["scope"], show_two["scope"]} == {"shows"}

    listing = client.get("/api/v1/download-clients")
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert {item["name"] for item in items} == {"qbit-movies-1", "qbit-shows-1", "qbit-shows-2"}
    assert all("password" not in item for item in items)
    assert all(item["password_configured"] is True for item in items)

    updated = client.patch(
        f"/api/v1/download-clients/{movie['id']}",
        headers=headers,
        json={
            "name": "qbit-movies-renamed",
            "password": "",
            "expected_revision": movie["revision"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "qbit-movies-renamed"
    assert updated.json()["password_configured"] is True
    assert updated.json()["revision"] == movie["revision"] + 1
    assert "super-secret-qbit-password" not in updated.text

    stale = client.patch(
        f"/api/v1/download-clients/{movie['id']}",
        headers=headers,
        json={"expected_revision": movie["revision"]},
    )
    assert stale.status_code == 409, stale.text

    deleted = client.delete(f"/api/v1/download-clients/{show_two['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    remaining = client.get("/api/v1/download-clients").json()["items"]
    assert {item["name"] for item in remaining} == {"qbit-movies-renamed", "qbit-shows-1"}


def test_download_client_test_and_health_refresh_use_read_only_fake(client: TestClient) -> None:
    headers = _login(client)
    configured = _create_client(client, headers)
    behavior = FakeQBitBehavior()
    _install_fake(client, behavior)
    try:
        tested = client.post(f"/api/v1/download-clients/{configured['id']}/test", headers=headers)
        assert tested.status_code == 200, tested.text
        assert tested.json()["status"] == "healthy"
        assert tested.json()["version"] == "v4.6.4"
        assert tested.json()["latency_ms"] is not None
        assert behavior.instances[-1].password == "super-secret-qbit-password"

        # Existing-client tests can exercise unsaved form edits while keeping
        # the write-only stored password when the password field is blank.
        edited = client.post(
            f"/api/v1/download-clients/{configured['id']}/test",
            headers=headers,
            json={"url": "http://edited-qbit.test:8081", "username": "edited-user"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "healthy"
        assert behavior.instances[-1].url == "http://edited-qbit.test:8081"
        assert behavior.instances[-1].username == "edited-user"
        assert behavior.instances[-1].password == "super-secret-qbit-password"

        new_password = client.post(
            f"/api/v1/download-clients/{configured['id']}/test",
            headers=headers,
            json={"password": "unsaved-new-password"},
        )
        assert new_password.status_code == 200, new_password.text
        assert behavior.instances[-1].password == "unsaved-new-password"

        refreshed = client.post(
            f"/api/v1/download-clients/{configured['id']}/test",
            headers=headers,
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["status"] == "healthy"

        fetched = client.get(f"/api/v1/download-clients/{configured['id']}")
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["health"] == "healthy"
    finally:
        client.app.dependency_overrides.clear()

    unavailable = FakeQBitBehavior(health_error="qBittorrent is offline")
    _install_fake(client, unavailable)
    try:
        failed = client.post(
            f"/api/v1/download-clients/{configured['id']}/test",
            headers=headers,
        )
        assert failed.status_code == 200, failed.text
        assert failed.json()["status"] == "unavailable"
        assert "offline" in (failed.json().get("message") or "")
    finally:
        client.app.dependency_overrides.clear()


def test_poll_persists_progress_filters_unrelated_paths_and_tracks_disappearance(client: TestClient) -> None:
    headers = _login(client)
    _configure_tmdb(client, headers)
    root = Path.cwd() / f"qbit-movies-{os.urandom(8).hex()}"
    root.mkdir(parents=True)
    try:
        configured_root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "qbit movies", "path": str(root), "media_type": "movies"},
        )
        assert configured_root.status_code == 201, configured_root.text
        _initialize_root(client, headers, configured_root.json()["id"])
        configured = _create_client(client, headers, tags=["managed"])
        behavior = FakeQBitBehavior(
            torrents=[
                _torrent(
                    "moviehash",
                    "Inception 2010 2160p WEB-DL",
                    progress=0.25,
                    save_path=str(root),
                    content_path=str(root / "Inception 2010 2160p WEB-DL"),
                ),
                _torrent(
                    "unrelatedhash",
                    "Linux ISO",
                    progress=0.75,
                    save_path="/other/downloads",
                    content_path="/other/downloads/Linux ISO",
                ),
            ]
        )
        _install_fake(client, behavior)
        try:
            first = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert first["observed"] == 2
            assert first["relevant"] == 1
            assert first["added"] == 1
            assert first["ignored"] == 1

            downloads = client.get("/api/v1/downloads")
            assert downloads.status_code == 200, downloads.text
            items = downloads.json()["items"]
            assert len(items) == 1
            assert items[0]["info_hash"] == "moviehash"
            assert items[0]["progress"] == pytest.approx(0.25)
            assert items[0]["is_present"] is True
            assert root.exists(), "Polling must not mutate media roots"

            behavior.torrents = [
                _torrent(
                    "moviehash",
                    "Inception 2010 2160p WEB-DL",
                    progress=0.85,
                    save_path=str(root),
                    content_path=str(root / "Inception 2010 2160p WEB-DL"),
                ),
            ]
            second = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert second["added"] == 0
            assert second["removed"] == 0
            current = client.get("/api/v1/downloads").json()["items"]
            assert current[0]["progress"] == pytest.approx(0.85)

            behavior.torrents = []
            third = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert third["removed"] == 1
            historical = client.get("/api/v1/downloads")
            assert historical.status_code == 200, historical.text
            assert historical.json()["items"] == []
            historical = client.get("/api/v1/downloads?include_removed=true")
            assert historical.status_code == 200, historical.text
            assert historical.json()["items"][0]["is_present"] is False
            assert historical.json()["items"][0]["removed_at"] is not None
        finally:
            client.app.dependency_overrides.clear()
    finally:
        if root.exists():
            root.rmdir()


def test_checking_torrent_updates_telemetry_without_reconciliation(client: TestClient) -> None:
    headers = _login(client)
    _configure_tmdb(client, headers)
    root = Path.cwd() / f"qbit-checking-{os.urandom(8).hex()}"
    root.mkdir(parents=True)
    try:
        configured_root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "qbit checking", "path": str(root), "media_type": "movies"},
        )
        assert configured_root.status_code == 201, configured_root.text
        _initialize_root(client, headers, configured_root.json()["id"])
        configured = _create_client(client, headers, tags=["managed"])
        behavior = FakeQBitBehavior(
            torrents=[
                _torrent(
                    "checkinghash",
                    "Checking Movie 2020 1080p WEB-DL",
                    progress=1.0,
                    state="checkingUP",
                    save_path=str(root),
                    content_path=str(root / "Checking Movie 2020 1080p WEB-DL"),
                )
            ]
        )
        _install_fake(client, behavior)
        try:
            summary = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert summary["added"] == 1
            assert summary["completed"] == 0

            downloads = client.get("/api/v1/downloads").json()["items"]
            assert len(downloads) == 1
            assert downloads[0]["state"] == "checkingUP"
            assert downloads[0]["progress"] == pytest.approx(1.0)
            assert downloads[0]["incoming"] is False

            assert client.get("/api/v1/movies").json()["total"] == 0
            assert client.get("/api/v1/problems?reason=TORRENT_PATH_NOT_FOUND").json()["total"] == 0

            behavior.torrents = [
                _torrent(
                    "checkinghash",
                    "Checking Movie 2020 1080p WEB-DL",
                    progress=0.5,
                    state="checkingResumeData",
                    save_path="",
                    content_path=None,
                )
            ]
            transient = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert transient["relevant"] == 1
            during_resume_check = client.get("/api/v1/downloads").json()["items"][0]
            assert during_resume_check["state"] == "checkingResumeData"
            assert during_resume_check["is_present"] is True
            assert during_resume_check["resolved_save_path"] == str(root / "Checking Movie 2020 1080p WEB-DL")

            behavior.torrents = [
                _torrent(
                    "checkinghash",
                    "Checking Movie 2020 1080p WEB-DL",
                    progress=1.0,
                    state="pausedUP",
                    save_path=str(root),
                    content_path=str(root / "Checking Movie 2020 1080p WEB-DL"),
                )
            ]
            finished = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert finished["completed"] == 1
            assert client.get("/api/v1/problems?reason=TORRENT_PATH_NOT_FOUND").json()["total"] == 1
        finally:
            client.app.dependency_overrides.clear()
    finally:
        if root.exists():
            root.rmdir()


def test_known_torrent_outside_configured_root_is_ignored_and_path_problem_resolves(client: TestClient) -> None:
    """A qBit path outside enabled roots is not a Medialogue filesystem fault.

    This covers the production case where a client also contains torrents for
    e.g. /Movies/Movies while this Medialogue instance is configured only for
    /Movies/Cartoons.  Historically-known torrents must not bypass the scope
    boundary and manufacture TORRENT_PATH_NOT_FOUND rows.
    """

    headers = _login(client)
    _configure_tmdb(client, headers)
    root = Path.cwd() / f"qbit-cartoons-{os.urandom(8).hex()}"
    root.mkdir(parents=True)
    try:
        configured_root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "cartoons only", "path": str(root), "media_type": "movies"},
        )
        assert configured_root.status_code == 201, configured_root.text
        _initialize_root(client, headers, configured_root.json()["id"])
        configured = _create_client(client, headers, tags=[])
        missing_inside_root = root / "Missing.Movie.2026"
        behavior = FakeQBitBehavior(
            torrents=[
                _torrent(
                    "scopedhash",
                    "Missing Movie 2026 1080p WEB-DL",
                    progress=1,
                    state="uploading",
                    save_path=str(root),
                    content_path=str(missing_inside_root),
                    tags=(),
                )
            ]
        )
        _install_fake(client, behavior)
        try:
            first = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert first["relevant"] == 1

            open_paths = client.get(
                "/api/v1/problems?status=open&reason=TORRENT_PATH_NOT_FOUND",
            )
            assert open_paths.status_code == 200, open_paths.text
            assert open_paths.json()["total"] == 1

            behavior.torrents = [
                _torrent(
                    "scopedhash",
                    "Missing Movie 2026 1080p WEB-DL",
                    progress=1,
                    state="uploading",
                    save_path="/Movies/Movies",
                    content_path="/Movies/Movies/Missing.Movie.2026",
                    tags=(),
                )
            ]
            second = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert second["relevant"] == 0
            assert second["ignored"] == 1

            open_paths = client.get(
                "/api/v1/problems?status=open&reason=TORRENT_PATH_NOT_FOUND",
            )
            assert open_paths.status_code == 200, open_paths.text
            assert open_paths.json()["total"] == 0
        finally:
            client.app.dependency_overrides.clear()
    finally:
        if root.exists():
            root.rmdir()


def test_manual_externally_added_torrent_is_observed_without_touching_media(client: TestClient) -> None:
    headers = _login(client)
    _configure_tmdb(client, headers)
    root = Path.cwd() / f"qbit-manual-{os.urandom(8).hex()}"
    root.mkdir(parents=True)
    sentinel = root / "already-present.mkv"
    sentinel.write_bytes(b"do not change")
    try:
        configured_root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "qbit manual movies", "path": str(root), "media_type": "movies"},
        )
        assert configured_root.status_code == 201, configured_root.text
        _initialize_root(client, headers, configured_root.json()["id"])
        configured = _create_client(client, headers, category=None, tags=[])
        behavior = FakeQBitBehavior(
            torrents=[
                _torrent(
                    "manualhash",
                    "Arrival 2016 1080p BluRay REMUX",
                    progress=1,
                    state="uploading",
                    save_path=str(root),
                    content_path=str(root / "Arrival 2016 1080p BluRay REMUX"),
                    category="",
                    tags=(),
                )
            ]
        )
        _install_fake(client, behavior)
        try:
            polled = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert polled["added"] == 1
            observed = client.get("/api/v1/downloads").json()["items"]
            assert len(observed) == 1
            assert observed[0]["name"] == "Arrival 2016 1080p BluRay REMUX"
            assert observed[0]["info_hash"] == "manualhash"
            assert observed[0]["progress"] == pytest.approx(1)
            assert observed[0]["is_present"] is True
            assert sentinel.read_bytes() == b"do not change"
        finally:
            client.app.dependency_overrides.clear()
    finally:
        if root.exists():
            sentinel.unlink(missing_ok=True)
            root.rmdir()


def test_qbit_connectivity_health_survives_internal_processing_failure(client: TestClient, monkeypatch) -> None:
    """A Medialogue parser/reconciliation bug must not label qBit offline."""

    from app.services import qbittorrent as qbit_service

    headers = _login(client)
    _configure_tmdb(client, headers)
    root = Path.cwd() / f"qbit-health-separation-{os.urandom(8).hex()}"
    root.mkdir(parents=True)
    try:
        root_response = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": f"Health-{os.urandom(5).hex()}", "path": str(root), "media_type": "movies"},
        )
        assert root_response.status_code == 201, root_response.text
        _initialize_root(client, headers, root_response.json()["id"])
        configured = _create_client(client, headers)
        behavior = FakeQBitBehavior(
            torrents=[
                _torrent(
                    "processingfailurehash",
                    "Broken Movie 2026 2160p WEB-DL-GROUP",
                    save_path=str(root),
                    content_path=str(root / "Broken Movie 2026 2160p WEB-DL-GROUP"),
                ),
                _torrent(
                    "healthysecondhash",
                    "Healthy Movie 2026 2160p WEB-DL-GROUP",
                    save_path=str(root),
                    content_path=str(root / "Healthy Movie 2026 2160p WEB-DL-GROUP"),
                ),
            ]
        )

        original_associate = qbit_service.associate_incoming_torrent

        async def explode_one(db, torrent, **kwargs):
            if torrent.info_hash == "processingfailurehash":
                raise RuntimeError("synthetic reconciliation failure")
            return await original_associate(db, torrent, **kwargs)

        monkeypatch.setattr(qbit_service, "associate_incoming_torrent", explode_one)

        async def scenario():
            async with db_session.async_session_factory() as db:
                results = await qbit_service.poll_due_download_clients(db, client_factory=behavior.factory)
                saved = await db.get(DownloadClient, UUID(configured["id"]))
                second = await db.scalar(select(Torrent).where(Torrent.info_hash == "healthysecondhash"))
                broken = await db.scalar(select(Torrent).where(Torrent.info_hash == "processingfailurehash"))
                return results, saved.health, saved.last_success_at, saved.last_error, second, broken

        results, health, last_success_at, last_error, second, broken = asyncio.run(scenario())
        assert health == "healthy"
        assert last_success_at is not None
        assert last_error is None
        assert results[0]["status"] == "healthy"
        assert results[0]["processing_errors"] == 1
        assert "could not be processed" in str(results[0].get("message"))
        assert second is not None  # later torrents still process after one bad row
        assert broken is None  # the failed torrent SAVEPOINT is rolled back cleanly
    finally:
        if root.exists():
            root.rmdir()
