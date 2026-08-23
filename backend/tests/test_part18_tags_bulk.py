import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import bulk as bulk_api
from app.core.config import Settings
from app.core.integration_config import get_integration_config_store
from app.db import session as db_session
from app.db.base import Base
from app.integrations.plex import PlexMediaMatch
from app.main import create_app
from app.models.domain import (
    IdentityState,
    MediaDirectory,
    MediaFile,
    MediaRole,
    MediaProfileOverride,
    MediaType,
    Movie,
    MovieRelease,
    ParseEvidence,
    PlexConfiguration,
    QualityProfile,
    ReleaseState,
    SourceType,
    StorageRoot,
    AccessMode,
)


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(database_url=database_url, bootstrap_admin=True, config_dir=f"{db_path}.config", secret_key="part18-secret-key-123456789")
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


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def db_run(fn):
    async def run():
        async with db_session.async_session_factory() as db:
            value = await fn(db)
            await db.commit()
            return value

    return asyncio.run(run())


def wait_job(client: TestClient, job_id: str, *, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    payload: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"job did not reach a terminal state within {timeout}s: {payload}")


async def seed_movies(db):
    movies = []
    for tmdb_id, title, year in ((27205, "Inception", 2010), (329865, "Arrival", 2016)):
        movie = Movie(
            title=title,
            sort_title=title.casefold(),
            year=year,
            tmdb_id=tmdb_id,
            identity_state=IdentityState.MATCHED,
            monitored=True,
        )
        db.add(movie)
        await db.flush()
        movies.append(movie)
    return movies


def test_tag_crud_assignment_filter_and_case_insensitive_uniqueness(client: TestClient) -> None:
    headers = login(client)
    movies = db_run(seed_movies)

    created = client.post("/api/v1/tags", headers=headers, json={"name": "Reference Quality"})
    assert created.status_code == 201, created.text
    tag = created.json()
    duplicate = client.post("/api/v1/tags", headers=headers, json={"name": "reference   quality"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "TAG_NAME_EXISTS"

    attached = client.post(f"/api/v1/movies/27205/tags/{tag['id']}", headers=headers)
    assert attached.status_code == 200, attached.text
    assert [item["name"] for item in attached.json()] == ["Reference Quality"]

    movie = client.get("/api/v1/movies/27205")
    assert movie.status_code == 200
    assert movie.json()["tags"][0]["name"] == "Reference Quality"

    filtered = client.get("/api/v1/movies", params={"tag": "reference quality"})
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["tmdb_id"] == 27205

    renamed = client.patch(f"/api/v1/tags/{tag['id']}", headers=headers, json={"name": "Keep Forever"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Keep Forever"

    detached = client.delete(f"/api/v1/movies/27205/tags/{tag['id']}", headers=headers)
    assert detached.status_code == 200
    assert detached.json() == []

    deleted = client.delete(f"/api/v1/tags/{tag['id']}", headers=headers)
    assert deleted.status_code == 204
    assert client.get("/api/v1/tags").json() == []


def test_bulk_monitor_and_tags_are_idempotent(client: TestClient) -> None:
    headers = login(client)
    db_run(seed_movies)
    tag = client.post("/api/v1/tags", headers=headers, json={"name": "Favorite"}).json()

    monitor = client.post(
        "/api/v1/movies/bulk",
        headers=headers,
        json={"movie_ids": ["27205", "329865"], "action": "unmonitor"},
    )
    assert monitor.status_code == 200, monitor.text
    assert monitor.json()["requested"] == 2
    assert monitor.json()["updated"] == 2

    repeat = client.post(
        "/api/v1/movies/bulk",
        headers=headers,
        json={"movie_ids": ["27205", "329865"], "action": "unmonitor"},
    )
    assert repeat.status_code == 200
    assert repeat.json()["updated"] == 0

    added = client.post(
        "/api/v1/movies/bulk",
        headers=headers,
        json={"movie_ids": ["27205", "329865"], "action": "add_tags", "tag_ids": [tag["id"]]},
    )
    assert added.status_code == 200, added.text
    assert added.json()["updated"] == 2
    assert all(item["monitored"] is False for item in client.get("/api/v1/movies?page_size=250").json()["items"])
    assert all(item["tags"][0]["name"] == "Favorite" for item in client.get("/api/v1/movies?page_size=250").json()["items"])

    removed = client.post(
        "/api/v1/movies/bulk",
        headers=headers,
        json={"movie_ids": ["27205", "329865"], "action": "remove_tags", "tag_ids": [tag["id"]]},
    )
    assert removed.status_code == 200
    assert removed.json()["updated"] == 2


def test_bulk_profile_change_preserves_title_overrides(client: TestClient) -> None:
    headers = login(client)
    movies = db_run(seed_movies)

    async def seed_profile(db):
        profile_a = QualityProfile(name=f"Profile A {uuid.uuid4().hex}")
        profile_b = QualityProfile(name=f"Profile B {uuid.uuid4().hex}")
        db.add_all([profile_a, profile_b])
        await db.flush()
        assignment = MediaProfileOverride(
            media_type=MediaType.MOVIES,
            movie_id=movies[0].id,
            quality_profile_id=profile_a.id,
            override_definition={"minimum_quality_definition_id": None, "custom_format_scores": {"example": 123}},
        )
        db.add(assignment)
        await db.flush()
        return profile_b.id, assignment.id

    profile_b_id, assignment_id = db_run(seed_profile)
    response = client.post(
        "/api/v1/movies/bulk",
        headers=headers,
        json={"movie_ids": ["27205"], "action": "change_profile", "quality_profile_id": str(profile_b_id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 1

    async def read_assignment(db):
        row = await db.get(MediaProfileOverride, assignment_id)
        return row.quality_profile_id, row.override_definition, row.revision

    profile_id, definition, revision = db_run(read_assignment)
    assert profile_id == profile_b_id
    assert definition["custom_format_scores"]["example"] == 123
    assert revision == 2


def test_bulk_parser_reparse_updates_technical_fields_but_preserves_manual_edition(client: TestClient) -> None:
    headers = login(client)
    movie = db_run(seed_movies)[0]

    async def seed_release(db):
        release = MovieRelease(
            movie_id=movie.id,
            raw_release_name="Inception 2010 Open Matte 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM",
            parsed_title="Wrong",
            parsed_year=1999,
            parsed_edition="Wrong Edition",
            manual_edition_override="Director's Cut",
            effective_edition="Director's Cut",
            release_group="WrongGroup",
            release_state=ReleaseState.CURRENT,
            parse_snapshot={"identity_confidence": 0.95, "incoming_kind": "replacement"},
        )
        db.add(release)
        await db.flush()
        return release.id

    release_id = db_run(seed_release)
    response = client.post(
        "/api/v1/movies/bulk",
        headers=headers,
        json={"movie_ids": ["27205"], "action": "reevaluate_parser"},
    )
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["job_id"])
    assert job["status"] == "completed", job
    assert job["summary"]["details"]["release_count"] == 1

    async def read(db):
        release = await db.get(MovieRelease, release_id)
        evidence = (await db.scalars(select(ParseEvidence).where(ParseEvidence.source_id == release_id))).all()
        return release, evidence

    release, evidence = db_run(read)
    assert release.parsed_title == "Inception"
    assert release.parsed_year == 2010
    assert release.parsed_edition == "Open Matte"
    assert release.effective_edition == "Director's Cut"
    assert release.release_group == "LM"
    assert release.parse_snapshot["incoming_kind"] == "replacement"
    assert release.parse_snapshot["identity_confidence"] == 0.95
    assert any(item.parse_snapshot.get("bulk_reparse") is True for item in evidence)


def test_bulk_custom_format_reevaluation_refreshes_current_score(client: TestClient) -> None:
    headers = login(client)
    movie = db_run(seed_movies)[0]

    async def seed_release(db):
        release = MovieRelease(
            movie_id=movie.id,
            raw_release_name="Inception 2010 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM",
            release_state=ReleaseState.CURRENT,
            current_custom_format_score=9999,
        )
        db.add(release)
        await db.flush()
        return release.id

    release_id = db_run(seed_release)
    response = client.post(
        "/api/v1/movies/bulk",
        headers=headers,
        json={"movie_ids": ["27205"], "action": "reevaluate_custom_formats"},
    )
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["job_id"])
    assert job["status"] == "completed", job
    release = db_run(lambda db: db.get(MovieRelease, release_id))
    assert release.current_custom_format_score == 0
    assert release.parse_snapshot["current_score_snapshot"]["total_score"] == 0


class FakePlexClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def health(self):
        return {"status": "healthy", "machine_identifier": "part18-plex"}

    async def find_exact_path(self, path: str):
        return PlexMediaMatch(
            rating_key="1",
            title="Inception",
            year=2010,
            edition=None,
            file_path=path,
        )

    async def search_title_year(self, _title: str, _year: int | None):
        return []

    async def close(self):
        return None


def test_bulk_plex_recheck_runs_without_operations_toggle(client: TestClient) -> None:
    headers = login(client)
    movie = db_run(seed_movies)[0]
    root = Path.cwd() / f"part18-plex-{uuid.uuid4().hex}"
    root.mkdir()
    media_path = root / "Inception 2010.mkv"
    media_path.write_bytes(b"movie")

    async def seed_plex(db):
        storage = StorageRoot(
            name="Movies",
            resolved_root_path=str(root),
            media_type=MediaType.MOVIES,
            access_mode=AccessMode.READ_ONLY,
            enabled=True,
        )
        release = MovieRelease(
            movie_id=movie.id,
            raw_release_name="Inception 2010 1080p BluRay REMUX AVC DTS-HD MA 5.1-LM",
            release_state=ReleaseState.CURRENT,
        )
        plex_config = get_integration_config_store().save_plex(
            url="http://plex.local:32400", token="token", enabled=True
        )
        plex = PlexConfiguration(id=plex_config.id)
        db.add_all([storage, release, plex])
        await db.flush()
        directory = MediaDirectory(
            storage_root_id=storage.id,
            movie_release_id=release.id,
            reported_path=str(root),
            resolved_path=str(root),
            exists=True,
            source_type=SourceType.FILESYSTEM,
        )
        db.add(directory)
        await db.flush()
        db.add(
            MediaFile(
                media_directory_id=directory.id,
                relative_path=media_path.name,
                filename=media_path.name,
                media_role=MediaRole.MOVIE_VIDEO,
                exists=True,
            )
        )

    db_run(seed_plex)
    client.app.dependency_overrides[bulk_api.get_plex_client_factory] = lambda: FakePlexClient
    try:
        checked = client.post(
            "/api/v1/movies/bulk",
            headers=headers,
            json={"movie_ids": ["27205"], "action": "recheck_plex"},
        )
        assert checked.status_code == 202, checked.text
        job = wait_job(client, checked.json()["job_id"])
        assert job["status"] == "completed", job
        assert job["summary"]["updated"] == 1
        assert job["summary"]["details"]["checked_releases"] == 1
    finally:
        client.app.dependency_overrides.pop(bulk_api.get_plex_client_factory, None)
        try:
            media_path.unlink()
            root.rmdir()
        except OSError:
            pass
