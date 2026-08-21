from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.main import create_app
from app.models.domain import Event, MediaDirectory, MediaFile, MediaRole, MediaType, Movie, Problem, ProblemStatus, Severity, StorageRoot


@pytest.fixture
def client():
    db_path = tempfile.mktemp(prefix="medialogue-v9-usability-", suffix=".db", dir=os.getcwd())
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


def _headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert login.status_code == 200, login.text
    return {"X-CSRF-Token": login.json()["csrf_token"]}


def test_storage_root_can_be_removed_without_deleting_observed_inventory(client: TestClient) -> None:
    headers = _headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            root = StorageRoot(
                name=f"Delete-{uuid.uuid4().hex[:8]}",
                resolved_root_path="/movies",
                media_type=MediaType.MOVIES,
            )
            db.add(root)
            await db.flush()
            directory = MediaDirectory(storage_root_id=root.id, resolved_path="/movies/Example Movie (2026)")
            db.add(directory)
            await db.commit()
            return str(root.id), directory.id

    root_id, directory_id = asyncio.run(seed())
    response = client.delete(f"/api/v1/storage-roots/{root_id}", headers=headers)
    assert response.status_code == 200, response.text

    async def inspect_state():
        async with db_session.async_session_factory() as db:
            return await db.get(StorageRoot, uuid.UUID(root_id)), await db.get(MediaDirectory, directory_id)

    root, directory = asyncio.run(inspect_state())
    assert root is None
    assert directory is not None
    assert directory.storage_root_id is None
    assert directory.resolved_path == "/movies/Example Movie (2026)"


def test_problem_queue_paginates_and_supports_single_and_bulk_deletion(client: TestClient) -> None:
    headers = _headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            rows = []
            for index in range(275):
                rows.append(
                    Problem(
                        reason="TMDB_MOVIE_IDENTITY_UNRESOLVED" if index % 2 == 0 else "DUPLICATE_PHYSICAL_RELEASE",
                        status=ProblemStatus.OPEN,
                        severity=Severity.WARNING,
                        entity_type="media_directory",
                        entity_id=uuid.uuid4(),
                        message=f"Problem {index}",
                        details={"index": index},
                    )
                )
            db.add_all(rows)
            await db.commit()

    asyncio.run(seed())

    first = client.get("/api/v1/problems?status=open&page=1&page_size=100")
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 275
    assert first.json()["pages"] == 3
    assert len(first.json()["items"]) == 100

    # Friendly UI priority aliases must map to the stored severity enum.
    medium = client.get("/api/v1/problems?status=open&severity=medium&page_size=250")
    assert medium.status_code == 200, medium.text
    assert medium.json()["total"] == 275
    assert client.get("/api/v1/problems?status=open&severity=high&page_size=250").json()["total"] == 0

    third = client.get("/api/v1/problems?status=open&page=3&page_size=100")
    assert third.status_code == 200
    assert len(third.json()["items"]) == 75

    clamped = client.get("/api/v1/problems?status=open&page=999&page_size=100")
    assert clamped.status_code == 200, clamped.text
    assert clamped.json()["page"] == 3
    assert len(clamped.json()["items"]) == 75

    problem_id = first.json()["items"][0]["id"]
    removed = client.delete(f"/api/v1/problems/{problem_id}", headers=headers)
    assert removed.status_code == 200, removed.text
    assert client.get("/api/v1/problems/count?status=open").json()["count"] == 274

    duplicates = client.get("/api/v1/problems?status=open&category=duplicates&page_size=250")
    duplicate_count = duplicates.json()["total"]
    cleared = client.delete("/api/v1/problems?status=open&category=duplicates", headers=headers)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["deleted"] == duplicate_count
    remaining = client.get("/api/v1/problems?status=open&category=duplicates&page_size=250")
    assert remaining.json()["total"] == 0


def test_media_file_problem_subject_is_human_readable(client: TestClient) -> None:
    _headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            directory = MediaDirectory(resolved_path="/shows/Example Show/Season 01")
            db.add(directory)
            await db.flush()
            media_file = MediaFile(
                media_directory_id=directory.id,
                relative_path="Example.Show.S01E01.mkv",
                filename="Example.Show.S01E01.mkv",
                media_role=MediaRole.EPISODE_VIDEO,
            )
            db.add(media_file)
            await db.flush()
            db.add(
                Problem(
                    reason="EPISODE_MAPPING_UNRESOLVED",
                    entity_type="media_file",
                    entity_id=media_file.id,
                    message="Episode needs review.",
                )
            )
            await db.commit()

    asyncio.run(seed())
    response = client.get("/api/v1/problems?status=open&reason=EPISODE_MAPPING_UNRESOLVED")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["subject"] == "Example.Show.S01E01.mkv · /shows/Example Show/Season 01"



def test_movie_summary_keeps_tmdb_poster_reference(client: TestClient) -> None:
    _headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            movie = Movie(
                title="Inception",
                sort_title="Inception",
                year=2010,
                tmdb_id=27205,
                poster_ref="/test-poster.jpg",
            )
            db.add(movie)
            await db.commit()

    asyncio.run(seed())
    response = client.get("/api/v1/movies?page_size=100")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["poster_ref"] == "/test-poster.jpg"

def test_event_history_supports_single_and_filtered_bulk_deletion(client: TestClient) -> None:
    headers = _headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            db.add_all(
                [
                    Event(event_type="scan.completed", severity=Severity.INFO, entity_type="storage_root", message="scan one", details={}),
                    Event(event_type="scan.completed", severity=Severity.INFO, entity_type="storage_root", message="scan two", details={}),
                    Event(event_type="plex.matched", severity=Severity.INFO, entity_type="movie", message="plex", details={}),
                ]
            )
            await db.commit()

    asyncio.run(seed())
    listing = client.get("/api/v1/events?page_size=100")
    assert listing.status_code == 200
    assert listing.json()["total"] == 3

    event_id = listing.json()["items"][0]["id"]
    removed = client.delete(f"/api/v1/events/{event_id}", headers=headers)
    assert removed.status_code == 200, removed.text

    cleared = client.delete("/api/v1/events?event_type=scan.completed", headers=headers)
    assert cleared.status_code == 200, cleared.text

    async def count_events():
        async with db_session.async_session_factory() as db:
            return int(await db.scalar(select(func.count()).select_from(Event)) or 0)

    # Depending on which newest row was individually removed, only the
    # non-scan event can remain after the filtered clear.
    assert asyncio.run(count_events()) <= 1
