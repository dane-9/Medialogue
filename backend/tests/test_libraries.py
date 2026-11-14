"""Libraries: named groups of media folders, like Plex's.

A folder is in at most one library, and a folder in none is invisible to
Medialogue. A folder never leaves its library while titles are recorded in it,
and moving it to another library takes its titles along.
"""

import asyncio
import os
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
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


def libraries(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/v1/libraries")
    assert response.status_code == 200, response.text
    return {item["name"]: item for item in response.json()}


def create_root(client, headers, path, name, media_type="movies", **extra) -> dict:
    os.makedirs(path, exist_ok=True)
    response = client.post("/api/v1/storage-roots", headers=headers, json={
        "name": name, "path": str(path), "media_type": media_type, **extra,
    })
    assert response.status_code == 201, response.text
    return response.json()


def create_library(client, headers, name, kind="movies") -> dict:
    response = client.post("/api/v1/libraries", headers=headers, json={"name": name, "kind": kind})
    assert response.status_code == 201, response.text
    return response.json()


def add_movie_in(root_id: str, path: str, title: str = "Toy Story") -> UUID:
    from app.models.domain import MediaDirectory, Movie, MovieRelease

    async def run() -> UUID:
        async with db_session.async_session_factory() as db:
            movie = Movie(title=title, sort_title=title.lower())
            release = MovieRelease(movie=movie, raw_release_name=f"{title} 1995 1080p")
            db.add_all([movie, release])
            await db.flush()
            db.add(MediaDirectory(storage_root_id=UUID(root_id), resolved_path=os.path.join(path, title), movie_release_id=release.id))
            await db.commit()
            return movie.id

    return asyncio.run(run())


def movie_library(movie_id: UUID) -> UUID:
    from app.models.domain import Movie

    async def run() -> UUID:
        async with db_session.async_session_factory() as db:
            return (await db.get(Movie, movie_id)).library_id

    return asyncio.run(run())


def test_a_new_database_has_one_library_of_each_kind(client):
    login(client)
    items = libraries(client)
    assert {name: item["kind"] for name, item in items.items()} == {"Movies": "movies", "Shows": "shows"}


def test_a_folder_joins_the_first_library_of_its_type_unless_told_otherwise(client, tmp_path):
    headers = login(client)
    defaulted = create_root(client, headers, tmp_path / "a", "A", media_type="shows")
    assert defaulted["library_name"] == "Shows"

    none = create_root(client, headers, tmp_path / "b", "B", library_id=None)
    assert none["library_id"] is None and none["library_name"] is None

    cartoons = create_library(client, headers, "Cartoon Movies")
    chosen = create_root(client, headers, tmp_path / "c", "C", library_id=cartoons["id"], category="  cartoons ")
    assert chosen["library_name"] == "Cartoon Movies"
    assert chosen["category"] == "cartoons"


def test_a_folder_cannot_join_a_library_of_another_type(client, tmp_path):
    headers = login(client)
    shows = libraries(client)["Shows"]
    response = client.post("/api/v1/storage-roots", headers=headers, json={
        "name": "A", "path": str(tmp_path), "media_type": "movies", "library_id": shows["id"],
    })
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LIBRARY_KIND_MISMATCH"

    root = create_root(client, headers, tmp_path / "m", "M")
    moved = client.put(f"/api/v1/libraries/{shows['id']}/folders", headers=headers, json={"storage_root_ids": [root["id"]]})
    assert moved.status_code == 409
    assert moved.json()["error"]["code"] == "LIBRARY_KIND_MISMATCH"


def test_checking_a_folder_in_another_library_moves_it_and_its_titles(client, tmp_path):
    headers = login(client)
    root = create_root(client, headers, tmp_path / "cartoons", "Cartoons")
    movie_id = add_movie_in(root["id"], str(tmp_path / "cartoons"))
    cartoons = create_library(client, headers, "Cartoon Movies")

    response = client.put(f"/api/v1/libraries/{cartoons['id']}/folders", headers=headers, json={"storage_root_ids": [root["id"]]})
    assert response.status_code == 200, response.text
    assert response.json()["folder_count"] == 1
    assert response.json()["title_count"] == 1
    assert client.get(f"/api/v1/storage-roots/{root['id']}").json()["library_name"] == "Cartoon Movies"
    assert str(movie_library(movie_id)) == cartoons["id"]
    assert libraries(client)["Movies"]["title_count"] == 0


def test_a_folder_holding_titles_cannot_leave_its_library(client, tmp_path):
    headers = login(client)
    movies = libraries(client)["Movies"]
    root = create_root(client, headers, tmp_path / "movies", "Movies")
    add_movie_in(root["id"], str(tmp_path / "movies"))

    response = client.put(f"/api/v1/libraries/{movies['id']}/folders", headers=headers, json={"storage_root_ids": []})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STORAGE_ROOT_HAS_TITLES"
    assert client.get(f"/api/v1/storage-roots/{root['id']}").json()["library_id"] == movies["id"]

    patched = client.patch(f"/api/v1/storage-roots/{root['id']}", headers=headers, json={"library_id": None})
    assert patched.status_code == 409


def test_an_empty_folder_can_leave_its_library_and_is_then_not_scanned(client, tmp_path):
    headers = login(client)
    movies = libraries(client)["Movies"]
    root = create_root(client, headers, tmp_path / "movies", "Movies")

    response = client.put(f"/api/v1/libraries/{movies['id']}/folders", headers=headers, json={"storage_root_ids": []})
    assert response.status_code == 200, response.text
    assert client.get(f"/api/v1/storage-roots/{root['id']}").json()["library_id"] is None

    scan = client.post(f"/api/v1/storage-roots/{root['id']}/scan", headers=headers)
    assert scan.status_code == 409
    assert scan.json()["error"]["code"] == "STORAGE_ROOT_NOT_IN_LIBRARY"

    from app.models.domain import StorageRoot

    async def active_ids() -> list[UUID]:
        async with db_session.async_session_factory() as db:
            return list((await db.scalars(select(StorageRoot.id).where(StorageRoot.active))).all())

    assert asyncio.run(active_ids()) == []


def test_a_library_with_folders_or_titles_cannot_be_deleted(client, tmp_path):
    headers = login(client)
    cartoons = create_library(client, headers, "Cartoon Movies")
    root = create_root(client, headers, tmp_path / "cartoons", "Cartoons", library_id=cartoons["id"])

    refused = client.delete(f"/api/v1/libraries/{cartoons['id']}", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "LIBRARY_NOT_EMPTY"

    client.put(f"/api/v1/libraries/{cartoons['id']}/folders", headers=headers, json={"storage_root_ids": []}).raise_for_status()
    deleted = client.delete(f"/api/v1/libraries/{cartoons['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert "Cartoon Movies" not in libraries(client)
    assert client.get(f"/api/v1/storage-roots/{root['id']}").json()["library_id"] is None


def test_a_library_keeps_its_type_once_it_has_folders(client, tmp_path):
    headers = login(client)
    empty = create_library(client, headers, "Anything")
    changed = client.patch(f"/api/v1/libraries/{empty['id']}", headers=headers, json={"kind": "shows", "name": "Cartoon Shows"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["kind"] == "shows" and changed.json()["name"] == "Cartoon Shows"

    create_root(client, headers, tmp_path / "s", "S", media_type="shows", library_id=empty["id"])
    refused = client.patch(f"/api/v1/libraries/{empty['id']}", headers=headers, json={"kind": "movies"})
    assert refused.status_code == 409


def test_library_names_are_unique(client):
    headers = login(client)
    response = client.post("/api/v1/libraries", headers=headers, json={"name": "Movies", "kind": "movies"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LIBRARY_EXISTS"


def test_libraries_reorder(client):
    headers = login(client)
    cartoons = create_library(client, headers, "Cartoon Movies")
    response = client.post("/api/v1/libraries/reorder", headers=headers, json={"ids": [cartoons["id"]]})
    assert response.status_code == 200, response.text
    assert [item["name"] for item in response.json()] == ["Cartoon Movies", "Movies", "Shows"]
    assert list(libraries(client)) == ["Cartoon Movies", "Movies", "Shows"]


def test_the_last_library_of_a_type_stays(client):
    headers = login(client)
    shows = libraries(client)["Shows"]
    refused = client.delete(f"/api/v1/libraries/{shows['id']}", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "LIBRARY_LAST_OF_KIND"
    retyped = client.patch(f"/api/v1/libraries/{shows['id']}", headers=headers, json={"kind": "movies"})
    assert retyped.status_code == 409
    assert retyped.json()["error"]["code"] == "LIBRARY_LAST_OF_KIND"

    create_library(client, headers, "Cartoon Shows", kind="shows")
    assert client.delete(f"/api/v1/libraries/{shows['id']}", headers=headers).status_code == 200


def test_library_names_are_unique_whatever_their_case(client):
    headers = login(client)
    assert client.post("/api/v1/libraries", headers=headers, json={"name": "movies", "kind": "movies"}).status_code == 409
    cartoons = create_library(client, headers, "Cartoons")
    renamed = client.patch(f"/api/v1/libraries/{cartoons['id']}", headers=headers, json={"name": "SHOWS"})
    assert renamed.status_code == 409
    assert client.patch(f"/api/v1/libraries/{cartoons['id']}", headers=headers, json={"name": "CARTOONS"}).status_code == 200


def test_the_movie_list_shows_one_library(client, tmp_path):
    headers = login(client)
    cartoons = create_library(client, headers, "Cartoon Movies")
    plain = create_root(client, headers, tmp_path / "movies", "Movies")
    toons = create_root(client, headers, tmp_path / "cartoons", "Cartoons", library_id=cartoons["id"])
    add_movie_in(plain["id"], str(tmp_path / "movies"), "Heat")
    toy = add_movie_in(toons["id"], str(tmp_path / "cartoons"), "Toy Story")

    from app.models.domain import Movie

    async def file_under_cartoons() -> None:
        async with db_session.async_session_factory() as db:
            (await db.get(Movie, toy)).library_id = UUID(cartoons["id"])
            await db.commit()

    asyncio.run(file_under_cartoons())
    listed = client.get("/api/v1/movies", params={"library_id": cartoons["id"]}).json()["items"]
    assert [(item["title"], item["library_id"]) for item in listed] == [("Toy Story", cartoons["id"])]
    assert client.get("/api/v1/movies").json()["total"] == 2
