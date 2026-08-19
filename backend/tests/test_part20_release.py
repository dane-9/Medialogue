import asyncio
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
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
        recovery_export_dir=str(config_dir / "recovery-exports"),
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


def test_first_run_setup_is_persistent_and_never_starts_a_scan(client) -> None:
    test_client, config_dir = client
    headers = login(test_client)

    initial = test_client.get("/api/v1/setup/status")
    assert initial.status_code == 200, initial.text
    payload = initial.json()
    assert payload["wizard_required"] is True
    by_key = {step["key"]: step for step in payload["steps"]}
    assert by_key["security"]["complete"] is False
    assert by_key["first_scan"]["complete"] is False
    assert test_client.get("/api/v1/jobs").json()["total"] == 0

    complete = test_client.put("/api/v1/setup/complete", headers=headers, json={"complete": True})
    assert complete.status_code == 200, complete.text
    assert complete.json()["wizard_required"] is False
    assert (config_dir / "setup-state.json").is_file()
    assert test_client.get("/api/v1/jobs").json()["total"] == 0

    reopened = test_client.put("/api/v1/setup/complete", headers=headers, json={"complete": False})
    assert reopened.status_code == 200
    assert reopened.json()["wizard_required"] is True


def test_setup_security_step_tracks_real_password_state(client) -> None:
    test_client, _ = client
    headers = login(test_client)
    before = test_client.get("/api/v1/setup/status").json()
    assert next(step for step in before["steps"] if step["key"] == "security")["complete"] is False

    changed = test_client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"current_password": "adminadmin", "new_password": "release-ready-password"},
    )
    assert changed.status_code == 200, changed.text
    after = test_client.get("/api/v1/setup/status").json()
    assert next(step for step in after["steps"] if step["key"] == "security")["complete"] is True


def test_readiness_reports_database_availability(client) -> None:
    test_client, _ = client
    response = test_client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "available"}


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
