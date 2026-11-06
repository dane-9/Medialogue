import asyncio
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.main import create_app


@pytest.fixture
def client():
    work = Path(tempfile.mkdtemp(prefix="medialogue-part20-", dir=os.getcwd()))
    db_path = work / "part20.db"
    config_dir = work / "config"
    archive_dir = work / "torrent-archive"
    config_dir.mkdir()
    archive_dir.mkdir()
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(
        database_url=database_url,
        bootstrap_admin=True,
        secret_key="part20-secret-key-123456789",
        config_dir=str(config_dir),
        torrent_archive_dir=str(archive_dir),
    )
    engine = create_async_engine(database_url)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client, config_dir
    asyncio.run(db_session.engine.dispose())
    import shutil
    shutil.rmtree(work, ignore_errors=True)


def login(client: TestClient):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_readiness_reports_database_availability(client) -> None:
    test_client, _ = client
    response = test_client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "available"}


def test_runtime_sqlite_connections_enable_integrity_and_contention_pragmas(client) -> None:
    async def read_pragmas():
        async with db_session.engine.connect() as connection:
            foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
            journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
            busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))
            return foreign_keys, journal_mode, busy_timeout

    foreign_keys, journal_mode, busy_timeout = asyncio.run(read_pragmas())
    assert foreign_keys == 1
    assert str(journal_mode).casefold() == "wal"
    assert busy_timeout == 30_000


def test_backend_distribution_does_not_claim_alembic_namespace() -> None:
    """Migration scripts must never be packaged as the third-party alembic module."""
    import tomllib

    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    finder = config["tool"]["setuptools"]["packages"]["find"]
    assert finder["include"] == ["app*"]
    assert finder["namespaces"] is False


def test_real_alembic_dependency_exposes_version_and_cli_module() -> None:
    import alembic
    from alembic.config import main as alembic_main

    assert alembic.__version__
    assert callable(alembic_main)
