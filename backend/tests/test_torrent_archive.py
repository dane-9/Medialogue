from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
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
from app.models.domain import IdentityState, Movie


@pytest.fixture
def archive_client():
    base = Path(tempfile.mkdtemp(prefix="medialogue-archive-test-", dir=os.getcwd()))
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

    async def add_torrent(self, torrent: bytes, **kwargs):
        self.behavior.added.append({"torrent": torrent, **kwargs})

    async def health(self):
        return {"status": "healthy", "version": "test"}

    async def close(self):
        return None


def _login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _install_fake(client: TestClient, behavior: ArchiveQBitBehavior) -> None:
    from app.api import downloads as downloads_api
    from app.api import torrent_archive as archive_api

    client.app.dependency_overrides[downloads_api.get_qbit_client_factory] = lambda: behavior.factory
    client.app.dependency_overrides[archive_api.get_torrent_archive_qbit_factory] = lambda: behavior.factory


def test_tracked_torrent_is_archived_with_manifest_and_survives_qbit_removal(archive_client) -> None:
    client, base, archive_dir = archive_client
    headers = _login(client)
    client.put("/api/v1/operations", headers=headers, json={"enabled": True})
    media_root = base / "movies"
    media_root.mkdir()
    root = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": "Movies", "path": str(media_root), "media_type": "movies"},
    ).json()
    configured = client.post(
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
        assert polled.status_code == 200, polled.text
        assert polled.json()["relevant"] == 1

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
        assert manifest["media_title"] == "Inception"
        assert manifest["release_name"] == release_name
        assert manifest["download_client_name"] == "qbit-movies"
        assert manifest["previous_resolved_path"].startswith(str(media_root))
        assert manifest["archive"]["torrent_sha256"]

        # Logical application removal must not erase the recovery identity.
        async def remove_movie_record():
            async with db_session.async_session_factory() as db:
                movie = await db.scalar(select(Movie).where(Movie.tmdb_id == 27205))
                assert movie is not None
                await db.delete(movie)
                await db.commit()

        asyncio.run(remove_movie_record())
        # If the physical archive is accidentally lost while qBittorrent still
        # has the torrent, polling repairs it rather than trusting stale DB state.
        torrent_path.unlink()
        assert not torrent_path.exists()
        refreshed = client.post(f"/api/v1/download-clients/{configured['id']}/poll", headers=headers)
        assert refreshed.status_code == 200, refreshed.text
        assert torrent_path.read_bytes() == behavior.torrent_bytes
        preserved_manifest = json.loads(manifest_path.read_text())
        assert preserved_manifest["media_title"] == "Inception"
        assert preserved_manifest["tmdb_id"] == 27205
        assert preserved_manifest["release_name"] == release_name

        # Removing the live qBit row does not touch recovery evidence.
        behavior.torrents = []
        removed = client.post(f"/api/v1/download-clients/{configured['id']}/poll", headers=headers)
        assert removed.status_code == 200, removed.text
        assert removed.json()["removed"] == 1
        after = client.get(f"/api/v1/torrent-archive/{item['id']}").json()
        assert after["archive_state"] == "archived"
        assert after["qbit_present"] is False
        assert torrent_path.is_file()
        assert manifest_path.is_file()

        restored = client.post(
            f"/api/v1/torrent-archive/{item['id']}/restore",
            headers=headers,
            json={"download_client_id": configured["id"], "save_path": str(media_root)},
        )
        assert restored.status_code == 202, restored.text
        assert restored.json()["resolved_save_path"] == str(media_root)
        assert behavior.added[-1]["torrent"] == behavior.torrent_bytes
        assert behavior.added[-1]["save_path"] == str(media_root)
        assert behavior.added[-1]["category"] == "movies"
        assert behavior.added[-1]["tags"] == ("managed",)
    finally:
        client.app.dependency_overrides.clear()


def test_restore_rejects_destination_outside_configured_roots(archive_client) -> None:
    client, base, _ = archive_client
    headers = _login(client)
    media_root = base / "movies"
    media_root.mkdir()
    client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": "Movies", "path": str(media_root), "media_type": "movies"},
    )
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
        client.put("/api/v1/operations", headers=headers, json={"enabled": True})
        client.post(f"/api/v1/download-clients/{configured['id']}/poll", headers=headers)
        item = client.get("/api/v1/torrent-archive").json()["items"][0]
        response = client.post(
            f"/api/v1/torrent-archive/{item['id']}/restore",
            headers=headers,
            json={"download_client_id": configured["id"], "save_path": "/unmanaged/location"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "RESTORE_PATH_OUTSIDE_CONFIGURED_ROOTS"
        assert behavior.added == []
    finally:
        client.app.dependency_overrides.clear()


def test_failed_archive_is_visible_and_manual_retry_can_recover(archive_client) -> None:
    client, base, _ = archive_client
    headers = _login(client)
    client.put("/api/v1/operations", headers=headers, json={"enabled": True})
    media_root = base / "movies"
    media_root.mkdir()
    client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": "Movies", "path": str(media_root), "media_type": "movies"},
    )
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
        assert response.status_code == 200, response.text
        item = client.get("/api/v1/torrent-archive").json()["items"][0]
        assert item["archive_state"] == "failed"
        assert item["manifest_path"]
        assert Path(item["manifest_path"]).is_file()

        behavior.torrent_bytes = b"d4:infod4:name7:exampleee"
        retried = client.post(f"/api/v1/torrent-archive/{item['id']}/retry", headers=headers)
        assert retried.status_code == 200, retried.text
        assert retried.json()["archive_state"] == "archived"
        assert Path(retried.json()["archive_path"]).read_bytes() == behavior.torrent_bytes
    finally:
        client.app.dependency_overrides.clear()
