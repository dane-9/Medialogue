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
from app.services.recovery import cleanup_expired_recovery_exports


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

