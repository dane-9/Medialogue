"""Root Storage state that the settings list depends on.

A row has to say what is true right now: whether a scan is running, whether the
path is still reachable, and how much is recorded under it. None of that was
observable before, health was only written by a scan, and a running scan was
indistinguishable from one that had never happened.
"""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(database_url=database_url, bootstrap_admin=True, config_dir=f"{db_path}.config", secret_key="test-secret-key-123456")
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


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def create_root(client: TestClient, headers: dict[str, str], path: str, name: str = "Movies") -> dict:
    response = client.post("/api/v1/storage-roots", headers=headers, json={
        "name": name, "path": path, "media_type": "movies",
        "access_mode": "read_only", "enabled": True, "missing_grace_checks": 2,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_a_new_root_reports_no_scan_running(client: TestClient, tmp_path):
    headers = login(client)
    root = tmp_path / "movies"
    root.mkdir()
    body = create_root(client, headers, str(root))
    assert body["scanning"] is False
    assert body["last_scan_at"] is None


def test_health_check_marks_a_missing_path_unavailable(client: TestClient, tmp_path):
    headers = login(client)
    root = tmp_path / "movies"
    root.mkdir()
    create_root(client, headers, str(root))
    root.rmdir()

    # Nothing has scanned since, so only an explicit check can notice.
    items = client.post("/api/v1/storage-roots/health", headers=headers).json()["items"]
    assert items[0]["last_health"] == "unavailable"
    assert items[0]["last_health_checked_at"] is not None


def test_health_check_restores_a_root_that_came_back(client: TestClient, tmp_path):
    headers = login(client)
    root = tmp_path / "movies"
    root.mkdir()
    create_root(client, headers, str(root))
    root.rmdir()
    assert client.post("/api/v1/storage-roots/health", headers=headers).json()["items"][0]["last_health"] == "unavailable"

    root.mkdir()
    items = client.post("/api/v1/storage-roots/health", headers=headers).json()["items"]
    assert items[0]["last_health"] == "available"


def test_health_check_leaves_a_disabled_root_alone(client: TestClient, tmp_path):
    headers = login(client)
    root = tmp_path / "movies"
    root.mkdir()
    created = create_root(client, headers, str(root))
    # Creating a root already records whether its path was there.
    assert created["last_health"] == "available"
    client.patch(f"/api/v1/storage-roots/{created['id']}", headers=headers, json={"enabled": False})
    root.rmdir()

    items = client.post("/api/v1/storage-roots/health", headers=headers).json()["items"]
    # A disabled root is not in use, so its path is not a problem to report.
    assert items[0]["last_health"] == "available"


def test_a_new_root_records_a_path_that_is_not_there(client: TestClient, tmp_path):
    headers = login(client)
    body = create_root(client, headers, str(tmp_path / "not-here"))
    assert body["last_health"] == "unavailable"


def test_health_check_requires_an_admin(client: TestClient):
    assert client.post("/api/v1/storage-roots/health").status_code in (401, 403)


def test_a_root_reports_how_much_is_recorded_under_it(client: TestClient, tmp_path):
    headers = login(client)
    root = tmp_path / "movies"
    root.mkdir()
    body = create_root(client, headers, str(root))
    # Nothing scanned yet, so nothing is recorded.
    assert body["media_count"] == 0


def test_library_summary_counts_unlinked_directories(client: TestClient, tmp_path):
    headers = login(client)
    root = tmp_path / "shows"
    (root / "Arcane").mkdir(parents=True)
    created = create_root(client, headers, str(root), name="Shows")

    before = client.get("/api/v1/library/summary").json()
    assert before["unlinked_directories"] == 0

    # Removing a root detaches its directories rather than deleting them.
    assert client.delete(f"/api/v1/storage-roots/{created['id']}", headers=headers).status_code == 200


def record_directory(root_id: str, path: str) -> None:
    """Record a directory under a root, as a scan would."""
    import asyncio
    import uuid as _uuid

    from app.db import session as db_session
    from app.models.domain import MediaDirectory

    async def work() -> None:
        async with db_session.async_session_factory() as db:
            db.add(MediaDirectory(id=_uuid.uuid4(), storage_root_id=_uuid.UUID(root_id), resolved_path=path))
            await db.commit()

    asyncio.run(work())


def test_adding_a_root_back_reconnects_its_directories(client: TestClient, tmp_path):
    headers = login(client)
    root = tmp_path / "shows"
    (root / "Arcane").mkdir(parents=True)
    first = create_root(client, headers, str(root), name="Shows")
    record_directory(first["id"], str(root / "Arcane"))

    client.delete(f"/api/v1/storage-roots/{first['id']}", headers=headers)
    assert client.get("/api/v1/library/summary").json()["unlinked_directories"] == 1

    # Adding the same folder back must reconnect it, or removal is one-way.
    create_root(client, headers, str(root), name="Shows again")
    assert client.get("/api/v1/library/summary").json()["unlinked_directories"] == 0


def test_a_new_root_does_not_take_directories_from_another_root(client: TestClient, tmp_path):
    headers = login(client)
    movies = tmp_path / "movies"
    (movies / "Inception").mkdir(parents=True)
    owner = create_root(client, headers, str(movies), name="Movies")
    record_directory(owner["id"], str(movies / "Inception"))

    # A second root over the same tree must not steal what the first owns.
    create_root(client, headers, str(tmp_path), name="Everything")
    assert client.get("/api/v1/library/summary").json()["unlinked_directories"] == 0
    roots = {item["name"]: item for item in client.get("/api/v1/storage-roots").json()["items"]}
    assert roots["Movies"]["media_count"] == 1
    assert roots["Everything"]["media_count"] == 0


def test_removing_a_root_keeps_entries_by_default(client: TestClient, tmp_path):
    headers = login(client)
    root = tmp_path / "movies"
    root.mkdir()
    created = create_root(client, headers, str(root))
    record_directory(created["id"], str(root / "Inception"))

    assert client.delete(f"/api/v1/storage-roots/{created['id']}", headers=headers).status_code == 200
    # The directory is detached, not deleted.
    assert client.get("/api/v1/library/summary").json()["unlinked_directories"] == 1


def test_removing_a_root_with_entries_deletes_its_directories(client: TestClient, tmp_path):
    headers = login(client)
    root = tmp_path / "movies"
    root.mkdir()
    created = create_root(client, headers, str(root))
    record_directory(created["id"], str(root / "Inception"))

    response = client.delete(
        f"/api/v1/storage-roots/{created['id']}?remove_entries=true", headers=headers
    )
    assert response.status_code == 200, response.text
    # Nothing is left dangling: the directories go with the entries.
    assert client.get("/api/v1/library/summary").json()["unlinked_directories"] == 0
