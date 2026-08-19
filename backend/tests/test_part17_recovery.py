import asyncio
import hashlib
import json
import os
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
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
    _pg_connection_args,
    cleanup_expired_recovery_exports,
    recovery_bundle_path,
    run_recovery_export,
)


@pytest.fixture
def recovery_env():
    root = Path(tempfile.mkdtemp(prefix="medialogue-part17-", dir=os.getcwd()))
    db_path = root / "test.db"
    archive = root / "torrent-archive"
    export_dir = root / "config" / "recovery-exports"
    archive.mkdir(parents=True)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        bootstrap_admin=True,
        secret_key="test-secret-key-123456",
        torrent_archive_dir=str(archive),
        recovery_export_dir=str(export_dir),
        pg_basebackup_bin=str(root / "missing-pg-basebackup"),
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


def test_recovery_bundle_contains_physical_backup_archive_config_and_inventory(recovery_env) -> None:
    client, settings, _ = recovery_env
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
            client_row = DownloadClient(
                name="qbit-movies",
                url="http://qbittorrent:8080",
                username="admin",
                password="super-secret-qbit-password",
                scope=MediaType.MOVIES,
            )
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

    async def fake_base_backup(destination: Path, _settings: Settings, progress):
        destination.mkdir(parents=True)
        (destination / "PG_VERSION").write_text("16\n", encoding="utf-8")
        (destination / "global").mkdir()
        (destination / "global" / "pg_control").write_bytes(b"physical-backup-evidence")
        if progress:
            await progress(50, "50/100 kB (50%)")
            await progress(100, "100/100 kB (100%)")
        return {"tool": "pg_basebackup", "format": "plain", "wal_method": "stream", "test": True}

    metadata = {
        "backend": "postgresql",
        "server_version": "16.10",
        "server_version_num": "160010",
        "server_major": 16,
        "migration_revision": "0009_shows_seasons_episodes",
        "custom_tablespaces": [],
    }
    asyncio.run(
        run_recovery_export(
            job_id,
            base_backup_runner=fake_base_backup,
            settings=settings,
            database_metadata_override=metadata,
        )
    )

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
        assert "database/physical-base-backup/PG_VERSION" in names
        assert "database/physical-base-backup/global/pg_control" in names
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
        assert config["download_clients"][0]["password"] == "super-secret-qbit-password"
        inventory = json.loads(bundle.read("inventory/library-inventory.json"))
        assert any(movie["tmdb_id"] == 27205 for movie in inventory["movies"])
        assert any(path["resolved_path"].startswith("/media/movies/Inception") for path in inventory["media_directories"])
        backup_meta = json.loads(bundle.read("backup-metadata.json"))
        assert backup_meta["postgresql"]["major_version"] == 16
        assert backup_meta["schema_migration_revision"] == "0009_shows_seasons_episodes"
        assert backup_meta["torrent_manifest_schema_version"] == 1
        assert backup_meta["sensitive"] is True

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

    # The endpoint requires an authenticated admin session.
    client.cookies.clear()
    blocked = client.get(f"/api/v1/recovery/exports/{job_id}/download")
    assert blocked.status_code == 401


def test_recovery_export_api_refuses_non_postgres_database(recovery_env) -> None:
    client, _, _ = recovery_env
    headers = login_headers(client)
    capability = client.get("/api/v1/recovery/capabilities")
    assert capability.status_code == 200
    assert capability.json()["supported"] is False
    assert capability.json()["database_backend"] == "sqlite"
    started = client.post("/api/v1/recovery/export", headers=headers)
    assert started.status_code == 409
    assert started.json()["error"]["code"] == "RECOVERY_EXPORT_UNAVAILABLE"


def test_database_password_is_not_put_on_pg_basebackup_command_line() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://backup_user:p%40ssword@postgres:5432/medialogue?sslmode=require",
        secret_key="test-secret-key-123456",
    )
    args, env = _pg_connection_args(settings)
    rendered = " ".join(args)
    assert "p@ssword" not in rendered
    assert "p%40ssword" not in rendered
    assert env["PGPASSWORD"] == "p@ssword"
    assert env["PGSSLMODE"] == "require"
    assert args == ["--host", "postgres", "--port", "5432", "--username", "backup_user", "--dbname", "medialogue"]


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
            "database_backend": "postgresql",
            "postgres_server_version": "16.10",
            "postgres_server_major": 16,
            "pg_basebackup_available": True,
            "pg_basebackup_version": "pg_basebackup (PostgreSQL) 16.10",
            "pg_basebackup_major": 16,
            "migration_revision": "0009_shows_seasons_episodes",
            "custom_tablespaces": [],
            "torrent_archive_readable": True,
            "export_directory_writable": True,
            "export_directory": "/tmp",
            "retention_hours": 24,
            "reasons": [],
        }

    import app.api.recovery as recovery_api
    monkeypatch.setattr(recovery_api, "recovery_capabilities", supported_capabilities)

    async def no_op_runner(_job_id):
        # Deliberately leave the job queued so a second request sees it as active.
        return None

    client.app.dependency_overrides[recovery_api.get_recovery_export_runner] = lambda: no_op_runner
    try:
        started = client.post("/api/v1/recovery/export", headers=headers)
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

        duplicate = client.post("/api/v1/recovery/export", headers=headers)
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "RECOVERY_EXPORT_ALREADY_RUNNING"
        assert duplicate.json()["error"]["details"]["job_id"] == job_id
    finally:
        client.app.dependency_overrides.pop(recovery_api.get_recovery_export_runner, None)
