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
from app.models.domain import (
    AssociationType,
    DownloadClient,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    Problem,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    Torrent,
    TorrentClientObservation,
)

pytestmark = pytest.mark.usefixtures("scratch_cwd")


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
    download_speed: int | None = None,
    upload_speed: int | None = None,
    eta: int | None = None,
    amount_left: int | None = None,
    ratio: float | None = None,
    seeds: int | None = None,
    leeches: int | None = None,
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
        download_speed=download_speed,
        upload_speed=upload_speed,
        eta=eta,
        amount_left=amount_left,
        ratio=ratio,
        seeds=seeds,
        leeches=leeches,
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
    from app.api import downloads as downloads_api

    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory


def test_qbit_remote_mapping_preserves_local_path_case() -> None:
    from app.services.qbittorrent_paths import _inside_local_root, resolve_remote_path

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

    updated = client.put(
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

    stale = client.put(
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
                    download_speed=2_000_000,
                    upload_speed=125_000,
                    eta=120,
                    amount_left=7_500,
                    ratio=0.25,
                    seeds=14,
                    leeches=3,
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
            assert items[0]["activity"] == "downloading"
            assert items[0]["download_speed"] == 2_000_000
            assert items[0]["upload_speed"] == 125_000
            assert items[0]["eta_seconds"] == 120
            assert items[0]["amount_left"] == 7_500
            assert items[0]["ratio"] == pytest.approx(0.25)
            assert items[0]["seeds"] == 14
            assert items[0]["leeches"] == 3
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
            # Removed observations are durable history, but Downloads is a
            # deliberately live operational view and never returns them.
            assert client.get("/api/v1/downloads?include_removed=true").json()["items"] == []
        finally:
            client.app.dependency_overrides.clear()
    finally:
        if root.exists():
            root.rmdir()


def test_downloads_excludes_historical_associations_and_supports_show_links(client: TestClient) -> None:
    headers = _login(client)
    movie_client = _create_client(client, headers, name="history-client")
    show_client = _create_client(
        client,
        headers,
        name="show-client",
        url="http://qbit-shows.test:8080",
        scope="shows",
        category="shows",
    )
    movie_id = uuid4()
    movie_release_id = uuid4()
    movie_torrent_id = uuid4()
    show_id = uuid4()
    show_release_id = uuid4()
    show_torrent_id = uuid4()

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            db.add_all(
                [
                    Movie(id=movie_id, title="Old Movie", sort_title="Old Movie"),
                    MovieRelease(
                        id=movie_release_id,
                        movie_id=movie_id,
                        raw_release_name="Old.Movie.2020.1080p.WEB-DL",
                    ),
                    Torrent(
                        id=movie_torrent_id,
                        info_hash="historicalhash",
                        name="Old.Movie.2020.1080p.WEB-DL",
                    ),
                    Show(id=show_id, title="Example Show"),
                    ShowRelease(
                        id=show_release_id,
                        show_id=show_id,
                        raw_release_name="Example.Show.S01.1080p.WEB-DL",
                    ),
                    Torrent(
                        id=show_torrent_id,
                        info_hash="showhash",
                        name="Example.Show.S01.1080p.WEB-DL",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    MovieReleaseTorrent(
                        movie_release_id=movie_release_id,
                        torrent_id=movie_torrent_id,
                        association_type=AssociationType.HISTORICAL,
                    ),
                    TorrentClientObservation(
                        torrent_id=movie_torrent_id,
                        download_client_id=UUID(movie_client["id"]),
                        state="uploading",
                        progress=1,
                        is_present=True,
                    ),
                    ShowReleaseTorrent(
                        show_release_id=show_release_id,
                        torrent_id=show_torrent_id,
                        association_type=AssociationType.ATTACHED,
                    ),
                    TorrentClientObservation(
                        torrent_id=show_torrent_id,
                        download_client_id=UUID(show_client["id"]),
                        state="uploading",
                        progress=1,
                        is_present=True,
                    ),
                ]
            )
            await db.commit()

    asyncio.run(seed())
    response = client.get("/api/v1/downloads")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["info_hash"] == "showhash"
    assert item["scope"] == "shows"
    assert item["show_id"] == str(show_id)
    assert item["movie_id"] is None
    assert item["release_id"] == str(show_release_id)
    assert item["media_title"] == "Example Show"
    assert item["reconciliation_state"] == "attached"
    assert item["activity"] == "seeding"


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


def test_completed_show_torrent_as_loose_root_file_is_ignored_without_problem(client: TestClient) -> None:
    headers = _login(client)
    _configure_tmdb(client, headers)
    root = Path.cwd() / f"qbit-loose-show-{os.urandom(8).hex()}"
    root.mkdir(parents=True)
    loose_file = root / "Example.Show.S01E01.1080p.WEB-DL.mkv"
    loose_file.write_bytes(b"video")
    try:
        configured_root = client.post(
            "/api/v1/storage-roots",
            headers=headers,
            json={"name": "qbit loose shows", "path": str(root), "media_type": "shows"},
        )
        assert configured_root.status_code == 201, configured_root.text
        _initialize_root(client, headers, configured_root.json()["id"])
        configured = _create_client(client, headers, scope="shows", category="shows", tags=[])
        behavior = FakeQBitBehavior(
            torrents=[
                _torrent(
                    "looseshowhash",
                    loose_file.stem,
                    progress=1,
                    state="uploading",
                    save_path=str(root),
                    content_path=str(loose_file),
                    category="shows",
                    tags=(),
                )
            ]
        )
        _install_fake(client, behavior)
        try:
            summary = _poll_job(client, f"/api/v1/download-clients/{configured['id']}/poll", headers)
            assert summary["processing_errors"] == 0

            async def stored_problem() -> Problem | None:
                async with db_session.async_session_factory() as db:
                    return await db.scalar(
                        select(Problem).where(Problem.reason == "TORRENT_SHOW_CONTAINER_REQUIRED")
                    )

            assert asyncio.run(stored_problem()) is None
        finally:
            client.app.dependency_overrides.clear()
    finally:
        loose_file.unlink(missing_ok=True)
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
        assert second is not None
        assert broken is None
    finally:
        if root.exists():
            root.rmdir()


def test_an_indexer_needs_a_download_client_once_one_exists(client: TestClient) -> None:
    """An indexer with nowhere to send a grab finds results it cannot act on.

    Not enforced before the first client is configured: requiring one on a
    fresh install would make it impossible to add any indexer until
    qBittorrent was set up.
    """

    headers = _login(client)
    base = {
        "name": "Nebula",
        "torznab_url": "http://indexer.test/api",
        "api_key": "indexer-key",
        "scope": "movies",
    }

    allowed = client.post("/api/v1/indexers", headers=headers, json=base)
    assert allowed.status_code == 201, allowed.text
    client.delete(f"/api/v1/indexers/{allowed.json()['id']}", headers=headers)

    download_client = _create_client(client, headers)

    refused = client.post("/api/v1/indexers", headers=headers, json=base)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "DOWNLOAD_CLIENT_REQUIRED"

    created = client.post(
        "/api/v1/indexers",
        headers=headers,
        json={**base, "download_client_ids": [download_client["id"]]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["download_client_ids"] == [download_client["id"]]

    cleared = client.put(
        f"/api/v1/indexers/{created.json()['id']}",
        headers=headers,
        json={"download_client_ids": [], "expected_revision": created.json()["revision"]},
    )
    assert cleared.status_code == 422, cleared.text
    assert cleared.json()["error"]["code"] == "DOWNLOAD_CLIENT_REQUIRED"


def test_download_pages_are_stable_and_filters_match_exact_states_and_hosts(client: TestClient) -> None:
    """Equal sort values must not reshuffle pages; tracker/moving filters are exact."""

    from datetime import datetime, timedelta, timezone

    headers = _login(client)
    qbit = _create_client(client, headers, name="paging-client")
    client_id = UUID(qbit["id"])
    now = datetime.now(timezone.utc)

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            for index in range(9):
                # Every row shares one name and progress: only a tie-breaker
                # can give them a deterministic order.
                torrent = Torrent(
                    info_hash=f"{index:040x}",
                    name="Same.Name.2024.1080p",
                    tracker_summary={"tracker": "https://tracker.abc.org:443/announce" if index < 2 else "https://tracker.notabc.org/announce"},
                )
                db.add(torrent)
                await db.flush()
                db.add(TorrentClientObservation(
                    torrent_id=torrent.id,
                    download_client_id=client_id,
                    state="moving" if index == 0 else "uploading",
                    progress=1,
                    is_present=True,
                    last_seen_at=now if index < 8 else now - timedelta(hours=2),
                    download_speed=1000,
                ))
            await db.commit()

    asyncio.run(seed())

    seen: list[str] = []
    for page in range(1, 4):
        response = client.get(f"/api/v1/downloads?client_id={client_id}&sort=name&page={page}&page_size=3")
        assert response.status_code == 200, response.text
        seen.extend(item["id"] for item in response.json()["items"])
    assert len(seen) == len(set(seen)) == 9
    again = [
        item["id"]
        for page in range(1, 4)
        for item in client.get(f"/api/v1/downloads?client_id={client_id}&sort=name&page={page}&page_size=3").json()["items"]
    ]
    assert again == seen

    by_host = client.get(f"/api/v1/downloads?client_id={client_id}&filters=tracker:tracker.abc.org&page_size=50").json()
    facets = client.get(f"/api/v1/downloads/facets?client_id={client_id}").json()
    assert by_host["total"] == facets["counts"]["tracker:tracker.abc.org"] == 2

    moving = client.get(f"/api/v1/downloads?client_id={client_id}&filters=moving&page_size=50").json()
    assert moving["total"] == facets["counts"]["moving"] == 1
    assert moving["items"][0]["activity"] == "moving"

    items = client.get(f"/api/v1/downloads?client_id={client_id}&page_size=50").json()["items"]
    stale = [item for item in items if item["stale"]]
    assert len(stale) == 1 and "has not reported" in stale[0]["stale_reason"]


def test_download_action_reports_partial_failure_instead_of_failing_everything(client: TestClient) -> None:
    headers = _login(client)
    good = _create_client(client, headers, name="good-client")
    bad = _create_client(client, headers, name="bad-client", url="http://qbit-bad.test:8080")
    ids: dict[str, str] = {}

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            for name, qbit in (("good", good), ("bad", bad)):
                torrent = Torrent(info_hash=f"{name}hash".ljust(40, "0"), name=f"{name}.torrent")
                db.add(torrent)
                await db.flush()
                observation = TorrentClientObservation(
                    torrent_id=torrent.id, download_client_id=UUID(qbit["id"]),
                    state="uploading", progress=1, is_present=True,
                )
                db.add(observation)
                await db.flush()
                ids[name] = str(observation.id)
            await db.commit()

    asyncio.run(seed())

    class Adapter:
        def __init__(self, url: str, *_args, **_kwargs):
            self.url = url

        async def stop_torrents(self, hashes):
            if "bad" in self.url:
                raise RuntimeError("connection refused")

        async def get_torrent(self, info_hash):
            return None

        async def list_torrents(self):
            return []

        async def close(self):
            return None

    from app.api import downloads as downloads_api
    import app.api.downloads as downloads_module

    async def no_refresh(*_args, **_kwargs):
        return None

    original_refresh = downloads_module.refresh_observations_after_action
    downloads_module.refresh_observations_after_action = no_refresh
    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: Adapter
    try:
        response = client.post(
            "/api/v1/downloads/actions",
            headers=headers,
            json={"download_ids": [ids["good"], ids["bad"]], "action": "stop"},
        )
    finally:
        downloads_module.refresh_observations_after_action = original_refresh
        client.app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == 1
    assert [item["client_name"] for item in body["failures"]] == ["bad-client"]
    assert "connection refused" in body["failures"][0]["error"]


def test_cross_seeds_are_grouped_paged_together_and_removable_individually(client: TestClient) -> None:
    from app.services.payload_groups import refresh_payload_groups

    headers = _login(client)
    first = _create_client(client, headers, name="seed-a")
    second = _create_client(client, headers, name="seed-b", url="http://qbit-b.test:8080")
    observation_ids: dict[str, str] = {}

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            rows = [
                ("a", first, "/media/Movies/Shared.Film.2024", "uploading"),
                ("b", second, "/media/Movies/Shared.Film.2024", "uploading"),
                ("c", first, "/media/Movies/Other.Film.2024", "missingFiles"),
            ]
            for key, qbit, path, state in rows:
                torrent = Torrent(info_hash=key * 40, name="Same.Name" if key != "c" else "Other",
                                  metadata_json={"content_path": path})
                db.add(torrent)
                await db.flush()
                observation = TorrentClientObservation(
                    torrent_id=torrent.id, download_client_id=UUID(qbit["id"]), state=state,
                    progress=1 if key != "c" else 0.5, is_present=True, resolved_save_path=path,
                )
                db.add(observation)
                await db.flush()
                observation_ids[key] = str(observation.id)
            await db.commit()
            await refresh_payload_groups(db)
            await db.commit()

    asyncio.run(seed())

    # Two payloads in total; a one-payload page still carries both cross-seeds.
    listing = client.get("/api/v1/downloads?sort=name&page=1&page_size=1").json()
    assert listing["total"] == 2 and listing["pages"] == 2
    assert {item["id"] for item in listing["items"]} == {observation_ids["c"]}
    second_page = client.get("/api/v1/downloads?sort=name&page=2&page_size=1").json()
    assert {item["id"] for item in second_page["items"]} == {observation_ids["a"], observation_ids["b"]}
    assert len({item["payload_group_id"] for item in second_page["items"]}) == 1

    # Missing files is its own state but still counts as an error.
    missing = listing["items"][0]
    assert missing["activity"] == "missing"
    assert client.get("/api/v1/downloads/facets").json()["counts"]["error"] == 1

    removed: list[tuple[str, bool]] = []

    class Adapter:
        def __init__(self, *_args, **_kwargs):
            pass

        async def remove_torrent(self, info_hash, *, delete_files=False):
            removed.append((info_hash, delete_files))

        async def close(self):
            return None

    from app.api import downloads as downloads_api

    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: Adapter
    try:
        response = client.post(
            "/api/v1/downloads/actions", headers=headers,
            json={"download_ids": [observation_ids["b"]], "action": "remove"},
        )
    finally:
        client.app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    assert removed == [("b" * 40, False)]
    remaining = client.get("/api/v1/downloads?page_size=50").json()["items"]
    assert observation_ids["b"] not in {item["id"] for item in remaining}
    assert observation_ids["a"] in {item["id"] for item in remaining}


def test_torrent_list_tolerates_fields_in_an_unexpected_shape() -> None:
    """qBittorrent 5 names share_limit_action; one odd field must not fail the poll."""

    from app.integrations.qbittorrent import _TORRENT_LIST

    items = _TORRENT_LIST.validate_python([{
        "hash": "abc", "name": "Movie", "progress": 0.5, "share_limit_action": "Default",
        "share_limits_mode": "Default", "dlspeed": "fast", "eta": None,
    }])
    assert items[0].share_limit_action == "Default"
    assert items[0].dlspeed is None
    assert items[0].progress == 0.5
    with pytest.raises(Exception):
        _TORRENT_LIST.validate_python([{"name": "no hash"}])


def test_one_failed_poll_is_retried_before_the_client_is_unavailable(client: TestClient) -> None:
    from app.models.domain import Event
    from app.services import qbittorrent as qbit_service
    from app.services.integration_state import get_configured_download_client

    headers = _login(client)
    configured = _create_client(client, headers)
    client_id = UUID(configured["id"])
    healthy = FakeQBitBehavior()

    class Unreachable(FakeQBitClient):
        async def list_torrents(self):
            raise RuntimeError("connection dropped")

    def unreachable(url, username, password):
        return Unreachable(healthy, url, username, password)

    async def poll(factory):
        async with db_session.async_session_factory() as db:
            target = await get_configured_download_client(db, client_id)
            await qbit_service.poll_download_client(db, target, client_factory=factory)
            await db.commit()
            saved = await get_configured_download_client(db, client_id)
            events = (await db.scalars(select(Event).where(
                Event.event_type == "qbittorrent.health", Event.entity_id == client_id))).all()
            return saved.health, saved.last_error, saved.last_polled_at, len(events)

    health, error, _, events = asyncio.run(poll(healthy.factory))
    assert (health, error, events) == ("healthy", None, 1)

    # The first failure keeps it healthy and makes it due again within seconds.
    health, error, polled_at, events = asyncio.run(poll(unreachable))
    assert (health, error, events) == ("healthy", "connection dropped", 1)
    from datetime import datetime, timezone
    polled_at = polled_at if polled_at.tzinfo else polled_at.replace(tzinfo=timezone.utc)
    assert (datetime.now(timezone.utc) - polled_at).total_seconds() >= 15 - 5 - 1

    # A second in a row is real, and says so live.
    health, error, _, events = asyncio.run(poll(unreachable))
    assert (health, error, events) == ("unavailable", "connection dropped", 2)

    # Recovery is announced too.
    health, error, _, events = asyncio.run(poll(healthy.factory))
    assert (health, error, events) == ("healthy", None, 3)
