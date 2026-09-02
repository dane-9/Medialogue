import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.integration_config import DownloadClientConfig, get_integration_config_store
from app.db.base import Base
from app.db import session as db_session
from app.main import create_app
from app.models.domain import (
    AssociationType,
    DownloadClient,
    Job,
    JobStatus,
    MediaDirectory,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    ReleaseState,
    StorageRoot,
    Torrent,
    TorrentArchiveState,
)
from app.services.jobs import create_job
from app.services.recovery import (
    cleanup_expired_recovery_exports,
    recovery_bundle_path,
    run_recovery_export,
)


@pytest.fixture
def recovery_env(tmp_path):
    # Not os.getcwd(): Windows keeps the SQLite file handles open past the
    # teardown rmtree, so a cwd-based temp dir silently survives inside the
    # repository and the leftover .db files end up committed.
    root = Path(tempfile.mkdtemp(prefix="medialogue-part17-", dir=tmp_path))
    db_path = root / "test.db"
    archive = root / "torrent-archive"
    export_dir = root / "config" / "recovery-exports"
    archive.mkdir(parents=True)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        config_dir=str(root / "config"),
        bootstrap_admin=True,
        secret_key="test-secret-key-123456",
        torrent_archive_dir=str(archive),
        recovery_export_dir=str(export_dir),
    )
    engine = create_async_engine(settings.database_url)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, settings, root
    asyncio.run(db_session.engine.dispose())
    import shutil
    shutil.rmtree(root, ignore_errors=True)


def login_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert login.status_code == 200
    return {"X-CSRF-Token": login.json()["csrf_token"]}


def test_recovery_bundle_contains_sqlite_backup_archive_config_and_inventory(recovery_env) -> None:
    client, settings, root_path = recovery_env
    login_headers(client)
    info_hash = "ab" + uuid.uuid4().hex + "123456"
    torrent_path = Path(settings.torrent_archive_dir) / "torrents" / info_hash[:2] / f"{info_hash}.torrent"
    manifest_path = Path(settings.torrent_archive_dir) / "manifests" / info_hash[:2] / f"{info_hash}.json"
    torrent_path.parent.mkdir(parents=True)
    manifest_path.parent.mkdir(parents=True)
    torrent_path.write_bytes(b"d4:infod4:name4:testee")
    manifest_path.write_text(json.dumps({"schema_version": 1, "torrent_info_hash": info_hash}), encoding="utf-8")

    async def seed():
        async with db_session.async_session_factory() as db:
            root = StorageRoot(name="Movies", resolved_root_path="/media/movies", media_type=MediaType.MOVIES)
            movie = Movie(title="Inception", sort_title="inception", year=2010, tmdb_id=27205)
            client_config = get_integration_config_store().save_download_client(
                DownloadClientConfig(
                    id=uuid.uuid4(),
                    name="qbit-movies",
                    url="http://qbittorrent:8080",
                    username="admin",
                    password="super-secret-qbit-password",
                    scope=MediaType.MOVIES.value,
                )
            )
            client_row = DownloadClient(id=client_config.id)
            db.add_all([root, movie, client_row])
            await db.flush()
            release = MovieRelease(
                movie_id=movie.id,
                raw_release_name="Inception 2010 2160p BluRay REMUX-GRP",
                parsed_title="Inception",
                parsed_year=2010,
                release_state=ReleaseState.CURRENT,
            )
            torrent = Torrent(
                info_hash=info_hash,
                name="Inception 2010 2160p BluRay REMUX-GRP",
                archive_state=TorrentArchiveState.ARCHIVED,
                archive_path=str(torrent_path),
                manifest_path=str(manifest_path),
                manifest_schema_version=1,
            )
            db.add_all([release, torrent])
            await db.flush()
            db.add_all([
                MediaDirectory(
                    storage_root_id=root.id,
                    resolved_path="/media/movies/Inception 2010 2160p BluRay REMUX-GRP",
                    reported_path="/downloads/movies/Inception 2010 2160p BluRay REMUX-GRP",
                    movie_release_id=release.id,
                ),
                MovieReleaseTorrent(
                    movie_release_id=release.id,
                    torrent_id=torrent.id,
                    association_type=AssociationType.ATTACHED,
                ),
            ])
            job = await create_job(db, "recovery_export", cancellable=False, summary={"sensitive": True})
            await db.commit()
            return job.id

    job_id = asyncio.run(seed())

    asyncio.run(run_recovery_export(job_id, settings=settings))

    async def read_job():
        async with db_session.async_session_factory() as db:
            return await db.get(Job, job_id)

    job = asyncio.run(read_job())
    assert job.status == JobStatus.COMPLETED
    assert job.progress["percent"] == 100
    assert job.summary["download_ready"] is True
    bundle_path = Path(job.summary["bundle_path"])
    assert bundle_path.is_file()
    assert job.summary["bundle_sha256"] == hashlib.sha256(bundle_path.read_bytes()).hexdigest()

    with zipfile.ZipFile(bundle_path) as bundle:
        names = set(bundle.namelist())
        assert "database/medialogue.db" in names
        assert "config/application-config-export.json" in names
        assert "inventory/library-inventory.json" in names
        assert "inventory/torrent-archive-inventory.json" in names
        assert "backup-metadata.json" in names
        assert f"torrent-archive/torrents/{info_hash[:2]}/{info_hash}.torrent" in names
        assert f"manifests/{info_hash[:2]}/{info_hash}.json" in names

        config = json.loads(bundle.read("config/application-config-export.json"))
        assert config["contains_sensitive_values"] is True
        assert config["runtime_sensitive"]["secret_key"] == "test-secret-key-123456"
        assert config["runtime_sensitive"]["database_url"].startswith("sqlite+aiosqlite:///")
        passwords = config["integration_configuration"]["secrets"]["download_client_passwords"]
        assert "super-secret-qbit-password" in passwords.values()
        assert "config/live/medialogue.json" in names
        assert "config/live/secrets.enc" in names
        inventory = json.loads(bundle.read("inventory/library-inventory.json"))
        assert any(movie["tmdb_id"] == 27205 for movie in inventory["movies"])
        assert any(path["resolved_path"].startswith("/media/movies/Inception") for path in inventory["media_directories"])
        backup_meta = json.loads(bundle.read("backup-metadata.json"))
        assert backup_meta["recovery_schema_version"] == 1
        assert backup_meta["sqlite"]["database_file"] == "database/medialogue.db"
        assert backup_meta["sqlite"]["backup"]["tool"] == "sqlite3_backup"
        assert "schema_migration_revision" not in backup_meta
        assert backup_meta["torrent_manifest_schema_version"] == 1
        assert backup_meta["sensitive"] is True

        restored = root_path / "restored-medialogue.db"
        restored.write_bytes(bundle.read("database/medialogue.db"))
        with sqlite3.connect(restored) as restored_db:
            assert restored_db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert restored_db.execute("SELECT title FROM movies WHERE tmdb_id = 27205").fetchone() == ("Inception",)

    history = client.get("/api/v1/events?event_type=recovery.export_completed")
    assert history.status_code == 200
    assert history.json()["total"] == 1


def test_completed_recovery_bundle_download_is_authenticated_and_no_store(recovery_env) -> None:
    client, settings, _ = recovery_env
    headers = login_headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            job = await create_job(db, "recovery_export", cancellable=False)
            job.status = JobStatus.COMPLETED
            bundle = recovery_bundle_path(job.id, settings)
            bundle.parent.mkdir(parents=True, exist_ok=True)
            bundle.write_bytes(b"PK\x03\x04fake")
            job.summary = {"bundle_path": str(bundle), "bundle_filename": "recovery-test.zip"}
            await db.commit()
            return job.id

    job_id = asyncio.run(seed())
    response = client.get(f"/api/v1/recovery/exports/{job_id}/download")
    assert response.status_code == 200
    assert response.content == b"PK\x03\x04fake"
    assert response.headers["cache-control"] == "no-store"
    assert "recovery-test.zip" in response.headers["content-disposition"]

    client.cookies.clear()
    blocked = client.get(f"/api/v1/recovery/exports/{job_id}/download")
    assert blocked.status_code == 401


def test_recovery_capabilities_accept_file_backed_sqlite(recovery_env) -> None:
    client, settings, _ = recovery_env
    login_headers(client)
    capability = client.get("/api/v1/recovery/capabilities")
    assert capability.status_code == 200
    assert capability.json()["supported"] is True
    assert capability.json()["database_backend"] == "sqlite"
    assert capability.json()["database_readable"] is True
    assert capability.json()["database_path"] == str(Path(settings.database_url.removeprefix("sqlite+aiosqlite:///")))


def test_settings_reject_non_sqlite_database_urls() -> None:
    with pytest.raises(ValueError, match="SQLite databases only"):
        Settings(database_url="postgresql://example.invalid/medialogue", secret_key="test-secret-key-123456")


def test_expired_recovery_exports_are_cleaned_up(recovery_env) -> None:
    _, settings, _ = recovery_env
    output = Path(settings.recovery_export_dir)
    output.mkdir(parents=True, exist_ok=True)
    old = output / f"medialogue-recovery-{uuid.uuid4()}.zip"
    new = output / f"medialogue-recovery-{uuid.uuid4()}.zip"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    expired = datetime.now(timezone.utc) - timedelta(hours=settings.recovery_export_retention_hours + 2)
    os.utime(old, (expired.timestamp(), expired.timestamp()))
    cleanup_expired_recovery_exports(settings)
    assert not old.exists()
    assert new.exists()

def test_recovery_export_api_creates_persistent_job_and_prevents_overlap(recovery_env, monkeypatch) -> None:
    client, _, _ = recovery_env
    headers = login_headers(client)

    async def supported_capabilities(_db):
        return {
            "supported": True,
            "database_backend": "sqlite",
            "sqlite_version": sqlite3.sqlite_version,
            "database_path": "/config/medialogue.db",
            "database_readable": True,
            "torrent_archive_readable": True,
            "export_directory_writable": True,
            "export_directory": "/tmp",
            "retention_hours": 24,
            "reasons": [],
        }

    import app.api.recovery as recovery_api
    monkeypatch.setattr(recovery_api, "recovery_capabilities", supported_capabilities)

    async def no_op_runner(_job_id):
        return None

    client.app.dependency_overrides[recovery_api.get_recovery_export_runner] = lambda: no_op_runner
    try:
        started = client.post(
            "/api/v1/recovery/export",
            headers=headers,
            json={"current_password": "adminadmin"},
        )
        assert started.status_code == 202, started.text
        job_id = started.json()["job_id"]
        persisted = client.get(f"/api/v1/jobs/{job_id}")
        assert persisted.status_code == 200
        assert persisted.json()["job_type"] == "recovery_export"
        assert persisted.json()["status"] == "queued"
        assert persisted.json()["cancellable"] is False

        history = client.get("/api/v1/events?event_type=recovery.export_started")
        assert history.status_code == 200
        assert history.json()["total"] == 1

        duplicate = client.post(
            "/api/v1/recovery/export",
            headers=headers,
            json={"current_password": "adminadmin"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "RECOVERY_EXPORT_ALREADY_RUNNING"
        assert duplicate.json()["error"]["details"]["job_id"] == job_id
    finally:
        client.app.dependency_overrides.pop(recovery_api.get_recovery_export_runner, None)
