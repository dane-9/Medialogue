import asyncio
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.main import create_app
from app.models.domain import (
    IdentityState,
    MediaDirectory,
    MediaType,
    Movie,
    MovieRelease,
    ReleaseState,
    SourceType,
    StorageRoot,
    Tag,
)


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(database_url=database_url, bootstrap_admin=True, config_dir=f"{db_path}.config", secret_key="part19-secret-key-123456789")
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
    Path(db_path).unlink(missing_ok=True)


def login(client: TestClient, password: str = "adminadmin") -> tuple[str, str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": password})
    assert response.status_code == 200, response.text
    session = client.cookies.get("medialogue_session")
    csrf_cookie = client.cookies.get("medialogue_csrf")
    assert session and csrf_cookie
    return session, csrf_cookie, response.json()["csrf_token"]


def db_run(fn):
    async def run():
        async with db_session.async_session_factory() as db:
            value = await fn(db)
            await db.commit()
            return value

    return asyncio.run(run())


def test_api_docs_and_schema_require_admin_session(client: TestClient) -> None:
    for path in ("/api/docs", "/api/redoc", "/api/openapi.json"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    login(client)
    docs = client.get("/api/docs")
    schema = client.get("/api/openapi.json")
    assert docs.status_code == 200
    assert "Swagger UI" in docs.text
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "Medialogue API"
    assert schema.headers["cache-control"] == "no-store"


def test_security_headers_are_applied_to_api_and_health_responses(client: TestClient) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["referrer-policy"] == "no-referrer"

    login(client)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.headers["cache-control"] == "no-store"


def test_csrf_token_is_bound_to_the_session_that_created_it(client: TestClient) -> None:
    _session_one, csrf_cookie_one, csrf_one = login(client)
    session_two, csrf_cookie_two, csrf_two = login(client)

    # Keep session two but present session one's matching header+cookie pair.
    # The double-submit values match each other, but not the server-side CSRF
    # digest belonging to session two, so the write must still be rejected.
    client.cookies.set("medialogue_session", session_two)
    client.cookies.set("medialogue_csrf", csrf_cookie_one)
    blocked = client.put(
        "/api/v1/integrations/tmdb",
        headers={"X-CSRF-Token": csrf_one},
        json={"api_key": "test", "enabled": True},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "CSRF_INVALID"

    client.cookies.set("medialogue_csrf", csrf_cookie_two)
    allowed = client.put(
        "/api/v1/integrations/tmdb",
        headers={"X-CSRF-Token": csrf_two},
        json={"api_key": "test", "enabled": True},
    )
    assert allowed.status_code == 200


def test_password_change_revokes_other_sessions_but_keeps_current_session(client: TestClient) -> None:
    session_one, _csrf_cookie_one, _csrf_one = login(client)
    session_two, csrf_cookie_two, csrf_two = login(client)

    changed = client.post(
        "/api/v1/auth/password",
        headers={"X-CSRF-Token": csrf_two},
        json={"current_password": "adminadmin", "new_password": "part19-new-password"},
    )
    assert changed.status_code == 200, changed.text

    client.cookies.set("medialogue_session", session_one)
    revoked = client.get("/api/v1/auth/me")
    assert revoked.status_code == 401

    client.cookies.set("medialogue_session", session_two)
    client.cookies.set("medialogue_csrf", csrf_cookie_two)
    current = client.get("/api/v1/auth/me")
    assert current.status_code == 200

    # Old credentials are no longer accepted; the changed password is.
    old_login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert old_login.status_code == 401
    new_login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "part19-new-password"})
    assert new_login.status_code == 200


def test_movie_collection_pagination_sort_query_tag_and_state_filters(client: TestClient) -> None:
    login(client)

    async def seed(db):
        root = StorageRoot(
            name="Part 19 Movies",
            resolved_root_path="/media/part19",
            media_type=MediaType.MOVIES,
            enabled=True,
        )
        tag = Tag(name="Reference Quality")
        db.add_all([root, tag])
        await db.flush()

        titles = ["Zulu", "Alpha", "Echo", "Bravo", "Delta", "Charlie"]
        for index, title in enumerate(titles):
            movie = Movie(
                title=title,
                sort_title=title.casefold(),
                year=2000 + index,
                tmdb_id=910000 + index,
                identity_state=IdentityState.MATCHED,
            )
            if title in {"Alpha", "Delta"}:
                movie.tags.append(tag)
            db.add(movie)
            await db.flush()
            if title == "Bravo":
                release = MovieRelease(
                    movie_id=movie.id,
                    raw_release_name="Bravo 2003 1080p BluRay REMUX-GROUP",
                    release_state=ReleaseState.CURRENT,
                )
                db.add(release)
                await db.flush()
                db.add(
                    MediaDirectory(
                        storage_root_id=root.id,
                        movie_release_id=release.id,
                        reported_path="/media/part19/Bravo 2003",
                        resolved_path="/media/part19/Bravo 2003",
                        exists=True,
                        source_type=SourceType.FILESYSTEM,
                    )
                )

    db_run(seed)

    first = client.get("/api/v1/movies?page=1&page_size=2&sort=title")
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["total"] == 6
    assert payload["pages"] == 3
    assert [item["title"] for item in payload["items"]] == ["Alpha", "Bravo"]

    second = client.get("/api/v1/movies?page=2&page_size=2&sort=title").json()
    assert [item["title"] for item in second["items"]] == ["Charlie", "Delta"]

    descending = client.get("/api/v1/movies?page_size=2&sort=-year").json()
    assert [item["year"] for item in descending["items"]] == [2005, 2004]

    queried = client.get("/api/v1/movies?query=del").json()
    assert [item["title"] for item in queried["items"]] == ["Delta"]

    tagged = client.get("/api/v1/movies?tag=Reference%20Quality").json()
    assert {item["title"] for item in tagged["items"]} == {"Alpha", "Delta"}

    present = client.get("/api/v1/movies?state=present").json()
    assert [item["title"] for item in present["items"]] == ["Bravo"]

    too_large = client.get("/api/v1/movies?page_size=251")
    assert too_large.status_code == 422
    assert too_large.json()["error"]["code"] == "VALIDATION_ERROR"
