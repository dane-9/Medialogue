from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.qbittorrent import TorrentObservation
from app.main import create_app
from app.models.domain import IdentityState, Movie, Torrent, TorrentArchiveState, TorrentClientObservation
from app.services.torrent_archive import verify_torrent_archive


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
def archive_client():
    base = Path.cwd() / f"medialogue-archive-test-{uuid.uuid4().hex}"
    base.mkdir()
    db_path = base / "test.db"
    archive_dir = base / "torrent-archive"
    archive_dir.mkdir()
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(
        database_url=database_url,
        config_dir=str(base / "config"),
        bootstrap_admin=True,
        secret_key="test-secret-key-123456",
        torrent_archive_dir=str(archive_dir),
    )
    engine = create_async_engine(database_url)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, base, archive_dir
    asyncio.run(db_session.engine.dispose())
    shutil.rmtree(base, ignore_errors=True)


@dataclass
class ArchiveQBitBehavior:
    torrents: list[TorrentObservation] = field(default_factory=list)
    torrent_bytes: bytes = b"d4:infod4:name7:exampleee"
    added: list[dict] = field(default_factory=list)
    file_priorities: list[dict] = field(default_factory=list)
    recovery_snapshots: dict[str, dict] = field(default_factory=dict)
    removed: list[dict] = field(default_factory=list)
    fail_file_priorities: bool = False
    started: list[str] = field(default_factory=list)
    rechecked: list[str] = field(default_factory=list)

    def factory(self, url: str, username: str, password: str):
        return ArchiveQBitClient(self, url, username, password)


class ArchiveQBitClient:
    def __init__(self, behavior: ArchiveQBitBehavior, url: str, username: str, password: str):
        self.behavior = behavior
        self.url = url

    async def list_torrents(self):
        return list(self.behavior.torrents)

    async def export_torrent(self, info_hash: str) -> bytes:
        return self.behavior.torrent_bytes

    async def get_torrent_recovery_snapshot(self, info_hash: str):
        return self.behavior.recovery_snapshots.get(
            info_hash.lower(),
            {
                "properties": {},
                "files": [{"index": 0, "name": "example.mkv", "size": 1, "priority": 1}],
                "trackers": [],
            },
        )

    async def add_torrent(self, torrent: bytes, **kwargs):
        self.behavior.added.append({"torrent": torrent, **kwargs})

    async def get_torrent(self, info_hash: str):
        return next((item for item in self.behavior.torrents if item.info_hash.lower() == info_hash.lower()), None)

    async def set_file_priorities(self, info_hash: str, priorities: dict[int, int]):
        if self.behavior.fail_file_priorities:
            raise RuntimeError("file priority restore failed")
        self.behavior.file_priorities.append({"info_hash": info_hash, "priorities": dict(priorities)})

    async def restore_torrent_settings(self, info_hash: str, settings: dict):
        assert self.behavior.file_priorities, "Settings must follow file selection"

    async def start_torrents(self, hashes):
        assert self.behavior.added[-1]["stopped"] is True
        assert self.behavior.file_priorities, "Do not start downloads before restoring file selection"
        self.behavior.started.extend(hashes)

    async def recheck_torrents(self, hashes):
        self.behavior.rechecked.extend(hashes)

    async def remove_torrent(self, info_hash: str, *, delete_files: bool = False):
        self.behavior.removed.append({"info_hash": info_hash, "delete_files": delete_files})

    async def health(self):
        return {"status": "healthy", "version": "test"}

    async def close(self):
        return None


def _login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_archive_verification_rejects_tampered_torrent_bytes(tmp_path: Path) -> None:
    payload = b"d4:infod4:name7:exampleee"
    torrent_path = tmp_path / "example.torrent"
    manifest_path = tmp_path / "example.json"
    torrent_path.write_bytes(payload)
    info_hash = "f" * 40
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "torrent_info_hash": info_hash,
                "archive": {"torrent_sha256": hashlib.sha256(payload).hexdigest()},
                "recovery": {
                    "properties": {},
                    "files": [{"index": 0, "name": "example.mkv", "size": 1, "priority": 1}],
                    "trackers": [],
                },
            }
        ),
        encoding="utf-8",
    )
    torrent = Torrent(
        info_hash=info_hash,
        name="Example",
        archive_state=TorrentArchiveState.ARCHIVED,
        archive_path=str(torrent_path),
        manifest_path=str(manifest_path),
    )
    assert verify_torrent_archive(torrent) == (True, None)

    torrent_path.write_bytes(payload + b"tampered")
    valid, reason = verify_torrent_archive(torrent)
    assert valid is False
    assert reason == "torrent export checksum does not match the manifest"


def test_archive_verification_requires_per_file_selection(tmp_path: Path) -> None:
    payload = b"d4:infod4:name7:exampleee"
    torrent_path = tmp_path / "example.torrent"
    manifest_path = tmp_path / "example.json"
    torrent_path.write_bytes(payload)
    info_hash = "e" * 40
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "torrent_info_hash": info_hash,
                "archive": {"torrent_sha256": hashlib.sha256(payload).hexdigest()},
                "recovery": {"properties": {}, "files": [], "trackers": []},
            }
        ),
        encoding="utf-8",
    )
    torrent = Torrent(
        info_hash=info_hash,
        name="Example",
        archive_state=TorrentArchiveState.ARCHIVED,
        archive_path=str(torrent_path),
        manifest_path=str(manifest_path),
    )

    valid, reason = verify_torrent_archive(torrent)
    assert valid is False
    assert reason == "recovery file selection is missing"


def _install_fake(client: TestClient, behavior: ArchiveQBitBehavior) -> None:
    from app.api import downloads as downloads_api
    from app.api import recovery as recovery_api
    from app.api import torrent_archive as archive_api

    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    client.app.dependency_overrides[archive_api.get_torrent_archive_qbit_factory] = lambda: behavior.factory
    client.app.dependency_overrides[recovery_api.get_recovery_qbit_factory] = lambda: behavior.factory


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
    raise AssertionError(f"job did not reach a terminal state within {timeout}s: {payload}")


def test_tracked_torrent_is_archived_with_manifest_and_survives_qbit_removal(archive_client) -> None:
    client, base, archive_dir = archive_client
    headers = _login(client)
    client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
    media_root = base / "movies"
    media_root.mkdir()
    root = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": "Movies", "path": str(media_root), "media_type": "movies"},
    ).json()
    scan = client.post(f"/api/v1/storage-roots/{root['id']}/scan", headers=headers)
    assert scan.status_code == 202, scan.text
    assert _wait_job(client, scan.json()["job_id"])["status"] == "completed"
    configured = client.post(
        "/api/v1/download-clients",
        headers=headers,
        json={
            "name": "qbit-movies",
            "url": "http://qbit.test:8080",
            "username": "media",
            "password": "secret",
            "scope": "movies",
            "category": "client-default",
            "tags": ["client-default"],
            "enabled": True,
        },
    ).json()

    async def seed_movie():
        async with db_session.async_session_factory() as db:
            db.add(Movie(title="Inception", sort_title="Inception", year=2010, tmdb_id=27205, identity_state=IdentityState.MATCHED))
            await db.commit()

    asyncio.run(seed_movie())

    info_hash = "a" * 40
    release_name = "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM"
    behavior = ArchiveQBitBehavior(
        torrents=[
            TorrentObservation(
                info_hash=info_hash,
                name=release_name,
                progress=0.5,
                state="downloading",
                save_path=str(media_root),
                content_path=str(media_root / release_name),
                category="movies",
                tags=("managed",),
                tracker="https://tracker.example/announce",
                total_size=123456789,
                added_at=1_700_000_000,
                completed_at=None,
            )
        ]
    )
    _install_fake(client, behavior)
    try:
        polled = client.post(f"/api/v1/download-clients/{configured['id']}/poll", headers=headers)
        assert polled.status_code == 202, polled.text
        poll_job = _wait_job(client, polled.json()["job_id"])
        assert poll_job["status"] == "completed", poll_job
        assert poll_job["summary"]["relevant"] == 1

        listing = client.get("/api/v1/torrent-archive")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1
        item = listing.json()["items"][0]
        assert item["archive_state"] == "archived"
        assert item["media_title"] == "Inception"
        assert item["tmdb_id"] == 27205
        assert item["original_download_client"] == "qbit-movies"
        assert item["qbit_present"] is True

        torrent_path = Path(item["archive_path"])
        manifest_path = Path(item["manifest_path"])
        assert torrent_path.read_bytes() == behavior.torrent_bytes
        manifest = json.loads(manifest_path.read_text())
        assert manifest["schema_version"] == 1
        assert manifest["torrent_info_hash"] == info_hash
        primary = manifest["media_associations"][0]
        original_client = manifest["download_client_observations"][0]
        assert primary["title"] == "Inception"
        assert primary["release_name"] == release_name
        assert original_client["download_client_name"] == "qbit-movies"
        assert original_client["resolved_save_path"].startswith(str(media_root))
        assert manifest["archive"]["torrent_sha256"]
        assert isinstance(manifest["recovery"]["captured_at"], str)
        assert manifest["recovery"] == {
            "captured_at": manifest["recovery"]["captured_at"],
            "files": [{"index": 0, "name": "example.mkv", "priority": 1, "size": 1}],
            "properties": {},
            "settings": {"content_path": str(media_root / release_name), "scope": "movies"},
            "trackers": [],
        }

        async def remove_movie_record():
            async with db_session.async_session_factory() as db:
                movie = await db.scalar(select(Movie).where(Movie.tmdb_id == 27205))
                assert movie is not None
                await db.delete(movie)
                await db.commit()

        asyncio.run(remove_movie_record())
        torrent_path.unlink()
        assert not torrent_path.exists()
        refreshed = client.post(f"/api/v1/download-clients/{configured['id']}/poll", headers=headers)
        assert refreshed.status_code == 202, refreshed.text
        refreshed_job = _wait_job(client, refreshed.json()["job_id"])
        assert refreshed_job["status"] == "completed", refreshed_job
        assert torrent_path.read_bytes() == behavior.torrent_bytes
        preserved_manifest = json.loads(manifest_path.read_text())
        preserved_primary = preserved_manifest["media_associations"][0]
        assert preserved_primary["title"] == "Inception"
        assert preserved_primary["tmdb_id"] == 27205
        assert preserved_primary["release_name"] == release_name

        behavior.torrents = []
        removed = client.post(f"/api/v1/download-clients/{configured['id']}/poll", headers=headers)
        assert removed.status_code == 202, removed.text
        removed_job = _wait_job(client, removed.json()["job_id"])
        assert removed_job["status"] == "completed", removed_job
        assert removed_job["summary"]["removed"] == 1
        after = client.get(f"/api/v1/torrent-archive/{item['id']}").json()
        assert after["archive_state"] == "archived"
        assert after["qbit_present"] is False
        assert torrent_path.is_file()
        assert manifest_path.is_file()

    finally:
        client.app.dependency_overrides.clear()


def test_restore_rejects_destination_outside_configured_roots(archive_client) -> None:
    client, base, _ = archive_client
    headers = _login(client)
    client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
    media_root = base / "movies"
    media_root.mkdir()
    root = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": "Movies", "path": str(media_root), "media_type": "movies"},
    ).json()
    scan = client.post(f"/api/v1/storage-roots/{root['id']}/scan", headers=headers)
    assert scan.status_code == 202, scan.text
    assert _wait_job(client, scan.json()["job_id"])["status"] == "completed"
    configured = client.post(
        "/api/v1/download-clients",
        headers=headers,
        json={"name": "qbit", "url": "http://qbit.test:8080", "password": "secret", "scope": "movies", "enabled": True},
    ).json()

    behavior = ArchiveQBitBehavior(
        torrents=[
            TorrentObservation(
                info_hash="b" * 40,
                name="Unknown Movie 2026 1080p WEB-DL-GROUP",
                progress=0.4,
                state="downloading",
                save_path=str(media_root),
                content_path=str(media_root / "Unknown Movie 2026 1080p WEB-DL-GROUP"),
                category="",
                tags=(),
                tracker=None,
                total_size=100,
                added_at=None,
                completed_at=None,
            )
        ]
    )
    _install_fake(client, behavior)
    try:
        client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
        polled = client.post(f"/api/v1/download-clients/{configured['id']}/poll", headers=headers)
        assert polled.status_code == 202, polled.text
        assert _wait_job(client, polled.json()["job_id"])["status"] == "completed"
        item = client.get("/api/v1/torrent-archive").json()["items"][0]

        # The check every re-add runs before contacting qBittorrent.
        from uuid import UUID
        from app.core.errors import AppError
        from app.db import session as db_session
        from app.services.torrent_archive import prepare_torrent_restore

        async def outside() -> str:
            async with db_session.async_session_factory() as db:
                try:
                    await prepare_torrent_restore(db, UUID(item["id"]), UUID(configured["id"]), "/unmanaged/location")
                except AppError as exc:
                    return exc.code
            return "accepted"

        assert asyncio.run(outside()) == "RESTORE_PATH_OUTSIDE_CONFIGURED_ROOTS"
        assert behavior.added == []
    finally:
        client.app.dependency_overrides.clear()


def test_failed_archive_is_visible_and_manual_retry_can_recover(archive_client) -> None:
    client, base, _ = archive_client
    headers = _login(client)
    client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
    media_root = base / "movies"
    media_root.mkdir()
    root = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": "Movies", "path": str(media_root), "media_type": "movies"},
    ).json()
    scan = client.post(f"/api/v1/storage-roots/{root['id']}/scan", headers=headers)
    assert scan.status_code == 202, scan.text
    assert _wait_job(client, scan.json()["job_id"])["status"] == "completed"
    configured = client.post(
        "/api/v1/download-clients",
        headers=headers,
        json={"name": "qbit", "url": "http://qbit.test:8080", "password": "secret", "scope": "movies", "enabled": True},
    ).json()
    behavior = ArchiveQBitBehavior(
        torrent_bytes=b"not-a-torrent",
        torrents=[
            TorrentObservation(
                info_hash="c" * 40,
                name="Example Movie 2026 1080p WEB-DL-GROUP",
                progress=0.25,
                state="downloading",
                save_path=str(media_root),
                content_path=str(media_root / "Example Movie 2026 1080p WEB-DL-GROUP"),
                category="",
                tags=(),
                tracker=None,
                total_size=100,
                added_at=None,
                completed_at=None,
            )
        ],
    )
    _install_fake(client, behavior)
    try:
        response = client.post(f"/api/v1/download-clients/{configured['id']}/poll", headers=headers)
        assert response.status_code == 202, response.text
        assert _wait_job(client, response.json()["job_id"])["status"] == "completed"
        item = client.get("/api/v1/torrent-archive").json()["items"][0]
        assert item["archive_state"] == "failed"
        assert item["manifest_path"]
        assert Path(item["manifest_path"]).is_file()

        behavior.torrent_bytes = b"d4:infod4:name7:exampleee"
        retried = client.post(f"/api/v1/torrent-archive/{item['id']}/retry", headers=headers)
        assert retried.status_code == 202, retried.text
        retried_job = _wait_job(client, retried.json()["job_id"])
        assert retried_job["status"] == "completed", retried_job
        assert retried_job["summary"]["archive_state"] == "archived"
        assert Path(retried_job["summary"]["archive_path"]).read_bytes() == behavior.torrent_bytes
    finally:
        client.app.dependency_overrides.clear()


def test_bulk_recovery_plan_requires_preview_and_restores_saved_file_selection(archive_client) -> None:
    client, base, archive_dir = archive_client
    headers = _login(client)
    media_root = base / "movies"
    media_root.mkdir()
    root_response = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": "Movies", "path": str(media_root), "media_type": "movies"},
    )
    assert root_response.status_code == 201, root_response.text
    configured = client.post(
        "/api/v1/download-clients",
        headers=headers,
        json={
            "name": "qbit-recovery",
            "url": "http://qbit.test:8080",
            "username": "media",
            "password": "secret",
            "scope": "movies",
            "category": "movies-default",
            "tags": ["client-default"],
            "enabled": True,
        },
    ).json()

    ready_hash = "d" * 40
    present_hash = "e" * 40
    blocked_hash = "f" * 40
    payload = b"d4:infod4:name7:exampleee"

    async def seed_archives() -> None:
        async with db_session.async_session_factory() as db:
            for info_hash, name, save_path in (
                (ready_hash, "Ready Movie 2026 1080p WEB-DL-GRP", str(media_root)),
                (present_hash, "Present Movie 2026 1080p WEB-DL-GRP", str(media_root)),
                (blocked_hash, "Blocked Movie 2026 1080p WEB-DL-GRP", str(base / "outside")),
            ):
                torrent_path = archive_dir / "torrents" / info_hash[:2] / f"{info_hash}.torrent"
                manifest_path = archive_dir / "manifests" / info_hash[:2] / f"{info_hash}.json"
                torrent_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                torrent_path.write_bytes(payload)
                manifest_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "torrent_info_hash": info_hash,
                            "torrent_name": name,
                            "media_associations": [
                                {
                                    "media_type": "movies",
                                    "title": name.split(" 2026", 1)[0],
                                    "release_name": name,
                                    "paths": [],
                                }
                            ],
                            "download_client_observations": [
                                {
                                    "download_client_id": configured["id"],
                                    "download_client_name": "qbit-recovery",
                                    "scope": "movies",
                                    "reported_save_path": save_path,
                                    "resolved_save_path": save_path,
                                    "category": "movies-recovery",
                                    "tags": ["restore-me", "private"],
                                }
                            ],
                            "recovery": {
                                "properties": {"save_path": save_path},
                                "files": [
                                    {"index": 0, "name": "sample.mkv", "size": 100, "priority": 0},
                                    {"index": 1, "name": "movie.mkv", "size": 1000, "priority": 7},
                                ],
                                "trackers": [],
                                "settings": {},
                            },
                            "archive": {"torrent_sha256": hashlib.sha256(payload).hexdigest()},
                        }
                    ),
                    encoding="utf-8",
                )
                db.add(
                    Torrent(
                        info_hash=info_hash,
                        name=name,
                        archive_state=TorrentArchiveState.ARCHIVED,
                        archive_path=str(torrent_path),
                        manifest_path=str(manifest_path),
                        manifest_schema_version=1,
                    )
                )
            await db.commit()

    asyncio.run(seed_archives())

    behavior = ArchiveQBitBehavior(
        torrents=[
            TorrentObservation(
                info_hash=present_hash,
                name="Present Movie 2026 1080p WEB-DL-GRP",
                progress=1.0,
                state="uploading",
                save_path=str(media_root),
                content_path=str(media_root / "Present Movie 2026 1080p WEB-DL-GRP"),
                category="movies-recovery",
                tags=("restore-me", "private"),
                tracker=None,
                total_size=1000,
                added_at=None,
                completed_at=None,
            )
        ],
        torrent_bytes=payload,
    )
    _install_fake(client, behavior)
    try:
        plan_response = client.get("/api/v1/recovery/reimport/plan")
        assert plan_response.status_code == 200, plan_response.text
        plan = plan_response.json()
        assert plan["total"] == 3
        assert plan["ready"] == 1
        assert plan["already_present"] == 1
        assert plan["blocked"] == 1

        by_hash = {item["info_hash"]: item for item in plan["items"]}
        ready = by_hash[ready_hash]
        assert ready["status"] == "ready"
        assert ready["download_client_id"] == configured["id"]
        assert ready["save_path"] == str(media_root)
        assert ready["resolved_save_path"] == str(media_root)
        assert ready["category"] == "movies-recovery"
        assert ready["tags"] == ["restore-me", "private"]
        assert ready["selected_file_count"] == 1
        assert ready["skipped_file_count"] == 1
        assert by_hash[present_hash]["status"] == "already_present"
        assert by_hash[blocked_hash]["status"] == "blocked"
        assert "enabled media folder" in " ".join(by_hash[blocked_hash]["reasons"])

        invalid = client.post(
            "/api/v1/recovery/reimport",
            headers=headers,
            json={"confirmation_token": "not-a-plan", "torrent_ids": [ready["torrent_id"]]},
        )
        assert invalid.status_code == 409
        assert invalid.json()["error"]["code"] == "INVALID_CONFIRMATION"
        assert behavior.added == []

        started = client.post(
            "/api/v1/recovery/reimport",
            headers=headers,
            json={"confirmation_token": plan["confirmation_token"], "torrent_ids": [ready["torrent_id"]]},
        )
        assert started.status_code == 202, started.text
        job = _wait_job(client, started.json()["job_id"])
        assert job["status"] == "completed", job
        assert job["summary"]["submitted"] == 1
        assert job["summary"]["failed"] == 0
        assert behavior.added[-1]["save_path"] == str(media_root)
        assert behavior.added[-1]["category"] == "movies-recovery"
        assert behavior.added[-1]["tags"] == ("restore-me", "private")
        assert behavior.file_priorities[-1] == {
            "info_hash": ready_hash,
            "priorities": {0: 0, 1: 7},
        }

        events = client.get("/api/v1/events?event_type=recovery.reimport_completed")
        assert events.status_code == 200
        assert events.json()["total"] == 1
    finally:
        client.app.dependency_overrides.clear()


def test_missing_torrent_is_readded_stopped_checked_and_started_only_when_complete(archive_client, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    import app.services.replacements as replacements
    from app.models.domain import TorrentClientObservation
    from app.services.auto_readd import CHECKING, FILES_MISSING, SEEDING, readd_missing, settle_checks

    client, base, archive_dir = archive_client
    headers = _login(client)
    media_root = base / "movies"
    media_root.mkdir()
    assert client.post("/api/v1/storage-roots", headers=headers,
                       json={"name": "Movies", "path": str(media_root), "media_type": "movies"}).status_code == 201
    configured = client.post("/api/v1/download-clients", headers=headers, json={
        "name": "qbit", "url": "http://qbit.test:8080", "username": "u", "password": "p",
        "scope": "movies", "enabled": True,
    }).json()
    payload = b"d4:infod4:name7:exampleee"
    hashes = {"complete": "a" * 40, "partial": "b" * 40}

    async def seed() -> dict[str, uuid.UUID]:
        ids = {}
        async with db_session.async_session_factory() as db:
            for key, info_hash in hashes.items():
                torrent_path = archive_dir / f"{info_hash}.torrent"
                manifest_path = archive_dir / f"{info_hash}.json"
                torrent_path.write_bytes(payload)
                manifest_path.write_text(json.dumps({
                    "schema_version": 1, "torrent_info_hash": info_hash, "torrent_name": key,
                    "media_associations": [{"media_type": "movies", "title": key, "release_name": key, "paths": []}],
                    "download_client_observations": [{
                        "download_client_id": configured["id"], "download_client_name": "qbit", "scope": "movies",
                        "reported_save_path": str(media_root), "resolved_save_path": str(media_root), "state": "uploading",
                    }],
                    "recovery": {"properties": {"save_path": str(media_root)},
                                 "files": [{"index": 0, "name": "movie.mkv", "size": 10, "priority": 1}],
                                 "trackers": [], "settings": {}},
                    "archive": {"torrent_sha256": hashlib.sha256(payload).hexdigest()},
                }), encoding="utf-8")
                torrent = Torrent(info_hash=info_hash, name=key, archive_state=TorrentArchiveState.ARCHIVED,
                                  archive_path=str(torrent_path), manifest_path=str(manifest_path), manifest_schema_version=1)
                db.add(torrent)
                await db.flush()
                # Seen leaving the configured client two days ago.
                db.add(TorrentClientObservation(
                    torrent_id=torrent.id, download_client_id=uuid.UUID(configured["id"]), is_present=False,
                    removed_at=now - timedelta(days=2), last_seen_at=now - timedelta(days=2)))
                ids[key] = torrent.id
            await db.commit()
        return ids

    now = datetime.now(timezone.utc)
    ids = asyncio.run(seed())

    async def targets(db, **_):
        from sqlalchemy.orm import selectinload
        rows = (await db.scalars(select(Torrent).options(selectinload(Torrent.observations)))).all()
        return [SimpleNamespace(readdable=True, torrents=[row]) for row in rows]

    monkeypatch.setattr(replacements, "replacement_targets", targets)
    behavior = ArchiveQBitBehavior()

    async def readd() -> dict:
        async with db_session.async_session_factory() as db:
            return await readd_missing(db, client_factory=behavior.factory, now=now)

    assert asyncio.run(readd())["readded"] == 2
    # Added stopped and checked, never started before the check.
    assert all(item["stopped"] is True for item in behavior.added)
    assert sorted(behavior.rechecked) == sorted(hashes.values())
    assert behavior.started == []
    # Each torrent is re-added once; a later removal is left to the user.
    assert asyncio.run(readd())["readded"] == 0

    async def observe_and_settle() -> dict:
        async with db_session.async_session_factory() as db:
            for key, progress in (("complete", 1), ("partial", 0.4)):
                row = await db.scalar(select(TorrentClientObservation).where(TorrentClientObservation.torrent_id == ids[key]))
                row.is_present, row.removed_at = True, None
                row.state = "stoppedUP" if progress == 1 else "stoppedDL"
                row.progress, row.last_seen_at = progress, now + timedelta(minutes=5)
            await db.commit()
            counts = await settle_checks(db, client_factory=behavior.factory, now=now + timedelta(minutes=6))
            states = {row.name: row.readd_state for row in (await db.scalars(select(Torrent))).all()}
            return {"counts": counts, "states": states}

    result = asyncio.run(observe_and_settle())
    assert result["states"] == {"complete": SEEDING, "partial": FILES_MISSING}
    assert behavior.started == [hashes["complete"]]
    assert CHECKING not in result["states"].values()


def test_readd_never_uses_another_client_and_caps_each_run(archive_client, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    import app.services.auto_readd as auto_readd
    import app.services.replacements as replacements
    from app.models.domain import TorrentClientObservation

    client, base, archive_dir = archive_client
    headers = _login(client)
    (base / "movies").mkdir()
    assert client.post("/api/v1/storage-roots", headers=headers,
                       json={"name": "Movies", "path": str(base / "movies"), "media_type": "movies"}).status_code == 201
    client.post("/api/v1/download-clients", headers=headers, json={
        "name": "only", "url": "http://qbit.test:8080", "username": "u", "password": "p", "scope": "movies", "enabled": True})
    gone_client = uuid.uuid4()
    torrent = SimpleNamespace(
        id=uuid.uuid4(), info_hash="c" * 40, name="x", auto_readded_at=None, readd_state=None,
        archive_state=TorrentArchiveState.ARCHIVED, archive_path="x.torrent",
        observations=[SimpleNamespace(is_present=False, removed_at=datetime.now(timezone.utc) - timedelta(days=2),
                                      download_client_id=gone_client)])

    async def targets(db, **_):
        return [SimpleNamespace(readdable=True, torrents=[torrent])]

    monkeypatch.setattr(replacements, "replacement_targets", targets)
    behavior = ArchiveQBitBehavior()

    async def run():
        async with db_session.async_session_factory() as db:
            return await auto_readd.readd_missing(db, client_factory=behavior.factory, now=datetime.now(timezone.utc))

    # It left a client Medialogue no longer has: the only remaining client is
    # not a stand-in for it.
    assert asyncio.run(run()) == {"readded": 0, "failed": 0}
    assert behavior.added == []
    assert auto_readd.READDS_PER_RUN == 25
