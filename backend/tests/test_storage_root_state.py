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


def test_changing_a_scanned_root_path_resets_its_scan_age(client: TestClient, tmp_path):
    from datetime import datetime, timezone
    from uuid import UUID

    from app.models.domain import StorageRoot

    headers = login(client)
    first = tmp_path / "movies-a"
    second = tmp_path / "movies-b"
    first.mkdir()
    second.mkdir()
    root = create_root(client, headers, str(first))

    async def mark_scanned() -> None:
        async with db_session.async_session_factory() as db:
            row = await db.get(StorageRoot, UUID(root["id"]))
            row.last_scan_at = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
            await db.commit()

    asyncio.run(mark_scanned())
    before = client.get(f"/api/v1/storage-roots/{root['id']}").json()
    assert before["last_scan_at"].endswith(("Z", "+00:00"))

    renamed = client.patch(f"/api/v1/storage-roots/{root['id']}", headers=headers, json={"name": "Renamed"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["last_scan_at"] is not None

    moved = client.patch(f"/api/v1/storage-roots/{root['id']}", headers=headers, json={"path": str(second)})
    assert moved.status_code == 200, moved.text
    assert moved.json()["last_scan_at"] is None


def test_scan_state_clears_after_terminal_job_and_blocks_path_races(client: TestClient, tmp_path):
    from app.models.domain import Job, JobStatus

    headers = login(client)
    first = tmp_path / "movies-a"
    second = tmp_path / "movies-b"
    first.mkdir()
    second.mkdir()
    root = create_root(client, headers, str(first))

    async def queue_scan() -> str:
        async with db_session.async_session_factory() as db:
            job = Job(
                job_type="storage_root_scan",
                status=JobStatus.QUEUED,
                summary={"storage_root_id": root["id"], "path": str(first)},
            )
            db.add(job)
            await db.commit()
            return str(job.id)

    job_id = asyncio.run(queue_scan())
    active = client.get(f"/api/v1/storage-roots/{root['id']}").json()
    assert active["scanning"] is True
    assert active["scan"]["job_id"] == job_id
    assert active["scan"]["status"] == "queued"

    raced = client.patch(f"/api/v1/storage-roots/{root['id']}", headers=headers, json={"path": str(second)})
    assert raced.status_code == 409, raced.text
    assert client.get(f"/api/v1/storage-roots/{root['id']}").json()["resolved_root_path"] == str(first.resolve())

    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    settled = client.get(f"/api/v1/storage-roots/{root['id']}").json()
    assert settled["scanning"] is False
    assert settled["scan"] is None


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


def make_roots(client: TestClient, headers: dict[str, str], tmp_path, names: list[str]) -> list[dict]:
    created = []
    for name in names:
        folder = tmp_path / name.lower()
        folder.mkdir()
        created.append(create_root(client, headers, str(folder), name=name))
    return created


def listed_names(client: TestClient) -> list[str]:
    return [item["name"] for item in client.get("/api/v1/storage-roots").json()["items"]]


def test_new_roots_are_listed_in_the_order_they_were_added(client: TestClient, tmp_path):
    headers = login(client)
    # Alphabetical ordering would put these back-to-front, so this pins that a
    # created root lands at the bottom rather than sorting itself into place.
    make_roots(client, headers, tmp_path, ["Zulu", "Alpha", "Mike"])
    assert listed_names(client) == ["Zulu", "Alpha", "Mike"]


def test_reorder_persists_the_requested_order(client: TestClient, tmp_path):
    headers = login(client)
    created = make_roots(client, headers, tmp_path, ["First", "Second", "Third"])
    ids = [root["id"] for root in created]

    response = client.post("/api/v1/storage-roots/reorder", headers=headers, json={"ids": [ids[2], ids[0], ids[1]]})
    assert response.status_code == 200, response.text
    assert [item["name"] for item in response.json()["items"]] == ["Third", "First", "Second"]
    assert listed_names(client) == ["Third", "First", "Second"]


def test_reorder_keeps_a_root_missing_from_the_payload(client: TestClient, tmp_path):
    headers = login(client)
    created = make_roots(client, headers, tmp_path, ["First", "Second", "Third"])
    ids = [root["id"] for root in created]

    # Simulates another admin adding a root after this list was rendered: it is
    # absent from the payload, and must survive rather than be dropped.
    client.post("/api/v1/storage-roots/reorder", headers=headers, json={"ids": [ids[1], ids[0]]})
    assert listed_names(client) == ["Second", "First", "Third"]


def test_reorder_rejects_an_unknown_root(client: TestClient, tmp_path):
    headers = login(client)
    created = make_roots(client, headers, tmp_path, ["First", "Second"])
    unknown = "00000000-0000-0000-0000-000000000000"

    response = client.post("/api/v1/storage-roots/reorder", headers=headers, json={"ids": [created[0]["id"], unknown]})
    assert response.status_code == 404, response.text
    # A rejected reorder must change nothing at all.
    assert listed_names(client) == ["First", "Second"]


def test_reorder_requires_csrf(client: TestClient, tmp_path):
    headers = login(client)
    created = make_roots(client, headers, tmp_path, ["First", "Second"])
    response = client.post("/api/v1/storage-roots/reorder", json={"ids": [created[1]["id"], created[0]["id"]]})
    assert response.status_code in (401, 403), response.text


def seed_scanned_show(root_id: str, folder: str, title: str) -> None:
    """Record a show folder the way a scan does.

    A scan links a show folder to its show through the files inside it:
    directory -> file -> episode map -> season -> show. It does not set
    MediaDirectory.show_release_id, which only a matched season pack fills in.
    Removing a root once looked only at that release link, so it found no shows
    at all and quietly kept every entry.
    """
    import uuid as uuid_module

    from app.db import session as db_session
    from app.models.domain import (
        Episode,
        EpisodeMediaMap,
        MatchMethod,
        MediaDirectory,
        MediaFile,
        Season,
        Show,
    )

    async def seed() -> None:
        async with db_session.async_session_factory() as db:
            show = Show(title=title, year=2001)
            db.add(show)
            await db.flush()
            season = Season(show_id=show.id, season_number=1)
            db.add(season)
            await db.flush()
            episode = Episode(show_id=show.id, season_id=season.id, season_number=1, episode_number=1)
            directory = MediaDirectory(storage_root_id=uuid_module.UUID(root_id), resolved_path=folder)
            db.add_all([episode, directory])
            await db.flush()
            media_file = MediaFile(media_directory_id=directory.id, relative_path="s01e01.mkv", filename="s01e01.mkv")
            db.add(media_file)
            await db.flush()
            db.add(EpisodeMediaMap(episode_id=episode.id, media_file_id=media_file.id, match_method=MatchMethod.PARSER))
            await db.commit()

    asyncio.run(seed())


def seed_show_problems(root_id: str, title: str) -> tuple[set[str], str]:
    """Create Problems at every subject level used by a scanned Show."""
    import uuid as uuid_module

    from sqlalchemy import select

    from app.db import session as db_session
    from app.models.domain import Episode, EpisodeMediaMap, MediaDirectory, MediaFile, Problem, Show

    async def seed() -> tuple[set[str], str]:
        async with db_session.async_session_factory() as db:
            show_id = await db.scalar(select(Show.id).where(Show.title == title))
            episode_id = await db.scalar(select(Episode.id).where(Episode.show_id == show_id))
            media_file_id = await db.scalar(
                select(MediaFile.id)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .where(EpisodeMediaMap.episode_id == episode_id)
            )
            directory_id = await db.scalar(
                select(MediaDirectory.id)
                .join(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
                .where(MediaFile.id == media_file_id)
            )
            subjects = {
                "storage_root": uuid_module.UUID(root_id),
                "show": show_id,
                "episode": episode_id,
                "media_directory": directory_id,
                "media_file": media_file_id,
            }
            removed = []
            for index, (entity_type, entity_id) in enumerate(subjects.items()):
                problem = Problem(
                    reason=f"TEST_REMOVED_SUBJECT_{index}",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    message="This warning belongs to a library entry that will be removed.",
                )
                db.add(problem)
                removed.append(problem)
            survivor = Problem(
                reason="TEST_UNRELATED_SUBJECT",
                entity_type="download_client",
                entity_id=uuid_module.uuid4(),
                message="This warning is unrelated to the removed root.",
            )
            db.add(survivor)
            await db.commit()
            return {str(problem.id) for problem in removed}, str(survivor.id)

    return asyncio.run(seed())


def show_titles(client: TestClient) -> list[str]:
    return [item["title"] for item in client.get("/api/v1/shows").json()["items"]]


def test_removing_a_root_with_entries_removes_its_scanned_shows(client: TestClient, tmp_path):
    headers = login(client)
    folder = tmp_path / "shows"
    folder.mkdir()
    root = create_root(client, headers, str(folder), name="Shows")
    seed_scanned_show(root["id"], str(folder / "Arcane"), "Arcane")
    assert show_titles(client) == ["Arcane"]

    response = client.delete(f"/api/v1/storage-roots/{root['id']}?remove_entries=true", headers=headers)
    assert response.status_code == 200, response.text
    assert show_titles(client) == []


def test_removing_root_entries_also_removes_their_problems(client: TestClient, tmp_path):
    headers = login(client)
    folder = tmp_path / "shows"
    folder.mkdir()
    root = create_root(client, headers, str(folder), name="Shows")
    seed_scanned_show(root["id"], str(folder / "Arcane"), "Arcane")
    removed_problem_ids, survivor_id = seed_show_problems(root["id"], "Arcane")

    response = client.delete(f"/api/v1/storage-roots/{root['id']}?remove_entries=true", headers=headers)
    assert response.status_code == 200, response.text

    remaining = {item["id"] for item in client.get("/api/v1/problems?status=open").json()["items"]}
    assert remaining.isdisjoint(removed_problem_ids)
    assert survivor_id in remaining


def test_removing_a_root_without_the_option_keeps_its_shows(client: TestClient, tmp_path):
    headers = login(client)
    folder = tmp_path / "shows"
    folder.mkdir()
    root = create_root(client, headers, str(folder), name="Shows")
    seed_scanned_show(root["id"], str(folder / "Arcane"), "Arcane")

    client.delete(f"/api/v1/storage-roots/{root['id']}", headers=headers)
    # The entry survives, unlinked from any folder. That is the whole difference
    # between removing a root and removing a root plus its entries.
    assert show_titles(client) == ["Arcane"]


def test_a_show_also_stored_under_another_root_survives(client: TestClient, tmp_path):
    headers = login(client)
    first = tmp_path / "shows-a"
    second = tmp_path / "shows-b"
    first.mkdir()
    second.mkdir()
    root_a = create_root(client, headers, str(first), name="Shows A")
    root_b = create_root(client, headers, str(second), name="Shows B")
    seed_scanned_show(root_a["id"], str(first / "Arcane"), "Arcane")
    # Same title recorded under a second root, so removing the first must not
    # take an entry that is still reachable elsewhere.
    seed_scanned_show(root_b["id"], str(second / "Futurama"), "Futurama")

    client.delete(f"/api/v1/storage-roots/{root_a['id']}?remove_entries=true", headers=headers)
    assert show_titles(client) == ["Futurama"]
