"""Browsing the filesystem to pick a Root Storage folder.

The folder you navigate into is the one being chosen, so this listing is the
only thing standing between an operator and a wrong path.
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


def browse(client: TestClient, path: str = ""):
    return client.get("/api/v1/filesystem", params={"path": path})


def test_browsing_lists_only_folders(client: TestClient, tmp_path):
    login(client)
    root = tmp_path / "library"
    (root / "movies").mkdir(parents=True)
    (root / "shows").mkdir(parents=True)
    (root / "readme.txt").write_text("x", encoding="utf-8")

    body = browse(client, str(root)).json()
    assert [entry["name"] for entry in body["directories"]] == ["movies", "shows"]
    assert body["parent"] == str(tmp_path)


def test_browsing_is_case_insensitively_sorted(client: TestClient, tmp_path):
    login(client)
    parent = tmp_path / "sorted"
    for name in ("zeta", "Alpha", "beta"):
        (parent / name).mkdir(parents=True)
    names = [entry["name"] for entry in browse(client, str(parent)).json()["directories"]]
    assert names == ["Alpha", "beta", "zeta"]


def test_browsing_with_no_path_lists_the_top_of_the_tree(client: TestClient):
    login(client)
    body = browse(client).json()
    assert body["directories"], "expected at least one mount point"
    assert body["parent"] is None


def test_browsing_a_missing_folder_explains_rather_than_fails(client: TestClient, tmp_path):
    login(client)
    response = browse(client, str(tmp_path / "nope"))
    assert response.status_code == 200, response.text
    assert response.json()["problem"] == "This folder does not exist."


def test_browsing_rejects_a_relative_path(client: TestClient):
    login(client)
    assert browse(client, "media/movies").json()["problem"] == "Enter an absolute path."


def test_browsing_requires_an_admin(client: TestClient):
    assert browse(client).status_code in (401, 403)


@pytest.mark.skipif(os.name != "nt", reason="drive letters only exist on Windows")
def test_a_windows_drive_root_goes_back_to_the_drive_list(client: TestClient):
    login(client)
    assert browse(client, "C:" + os.sep).json()["parent"] == ""


@pytest.mark.skipif(os.name == "nt", reason="posix only")
def test_the_posix_root_has_nowhere_to_go_back_to(client: TestClient):
    login(client)
    assert browse(client, "/").json()["parent"] is None
