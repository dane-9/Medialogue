from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.qbittorrent import TorrentObservation
from app.integrations.tmdb import TMDBEpisodeMetadata, TMDBSeasonMetadata, TMDBShowDetails, TMDBShowMatch
from app.main import create_app


class FakeTMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def health(self):
        return {"status": "healthy"}

    async def search_show(self, title: str, year: int | None = None):
        return [TMDBShowMatch(194764, "Dollface", "Dollface", 2019, "A test show.", None)]

    async def get_show(self, tmdb_id: int):
        return TMDBShowDetails(
            tmdb_id=194764,
            title="Dollface",
            original_title="Dollface",
            year=2019,
            overview="A test show.",
            poster_path=None,
            tvdb_id=361563,
            seasons=(TMDBSeasonMetadata(1, "Season 1", 3),),
        )

    async def get_season(self, tmdb_id: int, season_number: int):
        return [
            TMDBEpisodeMetadata(1001, season_number, 1, "Episode One", None, "One"),
            TMDBEpisodeMetadata(1002, season_number, 2, "Episode Two", None, "Two"),
            TMDBEpisodeMetadata(1003, season_number, 3, "Episode Three", None, "Three"),
        ]

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def fake_tmdb(monkeypatch):
    import app.services.tmdb as tmdb_service
    import app.api.tmdb as tmdb_api

    monkeypatch.setattr(tmdb_service, "TMDBClient", FakeTMDBClient)
    monkeypatch.setattr(tmdb_api, "TMDBClient", FakeTMDBClient)


@pytest.fixture
def client():
    db_path = tempfile.mktemp(prefix="medialogue-show-packs-", suffix=".db", dir=os.getcwd())
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


def configure(client: TestClient, headers: dict[str, str]) -> None:
    tmdb = client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key": "test", "enabled": True})
    assert tmdb.status_code == 200, tmdb.text
    operations = client.put("/api/v1/operations", headers=headers, json={"enabled": True})
    assert operations.status_code == 200, operations.text


def create_show_root(client: TestClient, headers: dict[str, str], root: Path, name: str = "Shows") -> dict:
    response = client.post(
        "/api/v1/storage-roots",
        headers=headers,
        json={"name": name, "path": str(root), "media_type": "shows", "missing_grace_checks": 2},
    )
    assert response.status_code == 201, response.text
    return response.json()


def scan(client: TestClient, headers: dict[str, str], root_id: str, *, timeout: float = 5.0) -> dict:
    response = client.post(f"/api/v1/storage-roots/{root_id}/scan", headers=headers)
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + timeout
    payload: dict = {}
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{response.json()['job_id']}")
        assert job.status_code == 200, job.text
        payload = job.json()
        if payload["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            break
        time.sleep(0.02)
    assert payload.get("status") == "completed", payload
    return payload


def detail(client: TestClient) -> dict:
    listing = client.get("/api/v1/shows")
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1, listing.json()
    resource = listing.json()["items"][0]["resource_id"]
    response = client.get(f"/api/v1/shows/{resource}")
    assert response.status_code == 200, response.text
    return response.json()


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


def episode_by_number(show: dict, number: int) -> dict:
    return next(item for season in show["seasons"] for item in season["episodes"] if item["episode_number"] == number)


def test_show_metadata_refresh_is_a_job(client: TestClient) -> None:
    headers = login(client)
    configure(client, headers)
    created = client.post("/api/v1/shows", headers=headers, json={"tmdb_id": 194764, "monitored": True})
    assert created.status_code == 201, created.text

    accepted = client.post(
        f"/api/v1/shows/{created.json()['id']}/metadata/refresh",
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    job = wait_job(client, accepted.json()["job_id"])

    assert job["status"] == "completed", job
    assert job["job_type"] == "tmdb_show_metadata_refresh"
    assert job["summary"]["seasons"] == 1
    assert job["summary"]["episodes"] == 3
    refreshed = client.get(f"/api/v1/shows/{created.json()['id']}")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["seasons"][0]["episodes"][0]["title"] == "Episode One"


def test_filesystem_season_pack_uses_one_release_for_many_episode_files(client: TestClient) -> None:
    root = Path.cwd() / f"season-pack-{uuid.uuid4().hex}"
    pack_name = "Dollface S01 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE"
    pack = root / pack_name
    pack.mkdir(parents=True)
    for number in (1, 2, 3):
        (pack / f"Dollface S01E{number:02d} 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE.mkv").write_bytes(b"episode")
    try:
        headers = login(client)
        configure(client, headers)
        configured = create_show_root(client, headers, root)
        result = scan(client, headers, configured["id"])
        assert result["summary"]["matched"] == 1

        show = detail(client)
        episodes = [episode_by_number(show, number) for number in (1, 2, 3)]
        assert all(item["presence_state"] == "present" for item in episodes)
        media = [item["media"][0] for item in episodes]
        assert {item["release_scope"] for item in media} == {"season_pack"}
        assert len({item["show_release_id"] for item in media}) == 1
        # The pack mapped cleanly: no member was left unresolved.
        assert client.get("/api/v1/problems?reason=EPISODE_MAPPING_UNRESOLVED").json()["total"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_multi_episode_file_maps_one_media_file_to_every_episode(client: TestClient) -> None:
    root = Path.cwd() / f"multi-episode-{uuid.uuid4().hex}"
    show_dir = root / "Dollface 2019"
    show_dir.mkdir(parents=True)
    (show_dir / "Dollface S01E01E02 1080p DSNP WEB-DL H.264-HONE.mkv").write_bytes(b"multi")
    try:
        headers = login(client)
        configure(client, headers)
        configured = create_show_root(client, headers, root)
        scan(client, headers, configured["id"])
        show = detail(client)
        e1 = episode_by_number(show, 1)
        e2 = episode_by_number(show, 2)
        e3 = episode_by_number(show, 3)
        assert e1["presence_state"] == e2["presence_state"] == "present"
        assert e3["presence_state"] == "missing"
        assert e1["media"][0]["media_file_id"] == e2["media"][0]["media_file_id"]
        assert e1["media"][0]["show_release_id"] == e2["media"][0]["show_release_id"]
        assert e1["media"][0]["release_scope"] == "multi_episode"
        assert e1["media"][0]["mapped_episode_numbers"] == [1, 2]
        assert e2["media"][0]["mapped_episode_numbers"] == [1, 2]
        # Both episodes resolved from the one file; nothing left pending.
        assert client.get("/api/v1/problems?reason=EPISODE_MAPPING_UNRESOLVED").json()["total"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_partial_multi_episode_mapping_keeps_valid_episode_and_flags_only_unresolved_number(client: TestClient) -> None:
    root = Path.cwd() / f"partial-multi-{uuid.uuid4().hex}"
    show_dir = root / "Dollface 2019"
    show_dir.mkdir(parents=True)
    (show_dir / "Dollface S01E01E99 1080p DSNP WEB-DL H.264-HONE.mkv").write_bytes(b"partial")
    try:
        headers = login(client)
        configure(client, headers)
        configured = create_show_root(client, headers, root)
        scan(client, headers, configured["id"])
        show = detail(client)
        assert episode_by_number(show, 1)["presence_state"] == "present"
        unresolved = client.get("/api/v1/problems?reason=EPISODE_MAPPING_UNRESOLVED")
        assert unresolved.status_code == 200, unresolved.text
        assert unresolved.json()["total"] == 1
        problem = unresolved.json()["items"][0]
        assert problem["details"]["mapped_episode_numbers"] == [1]
        assert problem["details"]["unresolved_episode_numbers"] == [99]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_manual_mapping_correction_changes_database_only(client: TestClient) -> None:
    root = Path.cwd() / f"manual-map-{uuid.uuid4().hex}"
    show_dir = root / "Dollface 2019"
    show_dir.mkdir(parents=True)
    media_path = show_dir / "Dollface S01E01E02 1080p DSNP WEB-DL H.264-HONE.mkv"
    media_path.write_bytes(b"do-not-touch")
    original = media_path.read_bytes()
    try:
        headers = login(client)
        configure(client, headers)
        configured = create_show_root(client, headers, root)
        scan(client, headers, configured["id"])
        before = detail(client)
        e1 = episode_by_number(before, 1)
        e2 = episode_by_number(before, 2)
        media_file_id = e1["media"][0]["media_file_id"]

        corrected = client.put(
            f"/api/v1/media-files/{media_file_id}/episode-mappings",
            headers=headers,
            json={"episode_ids": [e2["id"]]},
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["episode_numbers"] == [2]
        assert corrected.json()["manual_override"] is True

        after = detail(client)
        assert episode_by_number(after, 1)["presence_state"] == "missing"
        mapped = episode_by_number(after, 2)["media"][0]
        assert mapped["mapped_episode_numbers"] == [2]
        assert mapped["manual_mapping"] is True

        # Automatic reconciliation must not re-add E01 merely because the
        # filename still says E01E02. The manual logical mapping has higher
        # authority and the physical file remains untouched.
        scan(client, headers, configured["id"])
        rescanned = detail(client)
        assert episode_by_number(rescanned, 1)["presence_state"] == "missing"
        rescanned_e2 = episode_by_number(rescanned, 2)["media"][0]
        assert rescanned_e2["mapped_episode_numbers"] == [2]
        assert rescanned_e2["manual_mapping"] is True
        assert media_path.exists()
        assert media_path.read_bytes() == original
    finally:
        shutil.rmtree(root, ignore_errors=True)


@dataclass
class FakeQBitBehavior:
    torrents: list[TorrentObservation] = field(default_factory=list)

    def factory(self, url: str, username: str, password: str):
        return FakeQBitClient(self)


class FakeQBitClient:
    def __init__(self, behavior: FakeQBitBehavior):
        self.behavior = behavior

    async def list_torrents(self):
        return list(self.behavior.torrents)

    async def health(self):
        return {"status": "healthy", "version": "v4.6.4"}

    async def close(self):
        return None


def test_completed_qbittorrent_season_pack_attaches_one_release_without_moving_files(client: TestClient) -> None:
    root = Path.cwd() / f"qbit-season-pack-{uuid.uuid4().hex}"
    pack_name = "Dollface S01 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE"
    pack = root / pack_name
    pack.mkdir(parents=True)
    paths = []
    for number in (1, 2, 3):
        path = pack / f"Dollface S01E{number:02d} 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE.mkv"
        path.write_bytes(f"episode-{number}".encode())
        paths.append((path, path.read_bytes()))
    try:
        headers = login(client)
        configure(client, headers)
        configured_root = create_show_root(client, headers, root, name="qBit Shows")
        added_show = client.post("/api/v1/shows", headers=headers, json={"tmdb_id": 194764, "monitored": True})
        assert added_show.status_code == 201, added_show.text
        assert scan(client, headers, configured_root["id"])["status"] == "completed"
        qbit = client.post(
            "/api/v1/download-clients",
            headers=headers,
            json={
                "name": "qbit-shows-1",
                "url": "http://qbit.test:8080",
                "username": "media",
                "password": "secret",
                "scope": "shows",
                "category": "shows",
                "enabled": True,
                "poll_interval_seconds": 15,
            },
        )
        assert qbit.status_code == 201, qbit.text

        behavior = FakeQBitBehavior(torrents=[TorrentObservation(
            info_hash="showseasonpackhash",
            name=pack_name,
            progress=1.0,
            state="uploading",
            save_path=str(root),
            content_path=str(pack),
            category="shows",
            tags=("managed",),
            tracker="https://tracker.example/announce",
            total_size=30_000,
            added_at=1_700_000_000,
            completed_at=1_700_000_100,
        )])
        from app.api import downloads as downloads_api

        client.app.dependency_overrides[downloads_api.get_qbit_client_factory] = lambda: behavior.factory
        try:
            polled = client.post(f"/api/v1/download-clients/{qbit.json()['id']}/poll", headers=headers)
            assert polled.status_code == 202, polled.text
            poll_job = wait_job(client, polled.json()["job_id"])
            assert poll_job["status"] == "completed", poll_job
            assert poll_job["summary"]["added"] == 1, poll_job
            assert poll_job["summary"]["completed"] == 1, poll_job
        finally:
            client.app.dependency_overrides.clear()

        show = client.get("/api/v1/shows/194764").json()
        episodes = [episode_by_number(show, number) for number in (1, 2, 3)]
        release_ids = {episode["media"][0]["show_release_id"] for episode in episodes}
        assert len(release_ids) == 1
        assert {episode["media"][0]["release_scope"] for episode in episodes} == {"season_pack"}
        assert all(path.exists() and path.read_bytes() == original for path, original in paths)
    finally:
        client.app.dependency_overrides.clear()
        shutil.rmtree(root, ignore_errors=True)


def test_duplicate_episode_files_are_flagged_and_left_untouched(client: TestClient) -> None:
    root = Path.cwd() / f"duplicate-episode-{uuid.uuid4().hex}"
    show_dir = root / "Dollface 2019"
    show_dir.mkdir(parents=True)
    first = show_dir / "Dollface S01E01 1080p DSNP WEB-DL H.264-HONE.mkv"
    second = show_dir / "Dollface S01E01 2160p DSNP WEB-DL H.265-OTHER.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    try:
        headers = login(client)
        configure(client, headers)
        configured = create_show_root(client, headers, root)
        scan(client, headers, configured["id"])

        show = detail(client)
        e1 = episode_by_number(show, 1)
        assert e1["presence_state"] == "present"
        assert len(e1["media"]) == 2
        problems = client.get("/api/v1/problems?reason=DUPLICATE_EPISODE_RELEASE")
        assert problems.status_code == 200, problems.text
        assert problems.json()["total"] == 1
        assert first.exists() and first.read_bytes() == b"first"
        assert second.exists() and second.read_bytes() == b"second"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_nested_qbittorrent_season_pack_only_claims_its_own_members(client: TestClient) -> None:
    root = Path.cwd() / f"nested-qbit-pack-{uuid.uuid4().hex}"
    show_dir = root / "Dollface 2019"
    pack_name = "Dollface S01 2160p DSNP WEB-DL DD+ 5.1 H.265-HONE"
    pack = show_dir / pack_name
    pack.mkdir(parents=True)
    for number in (1, 2):
        (pack / f"Dollface S01E{number:02d} 2160p DSNP WEB-DL H.265-HONE.mkv").write_bytes(
            f"pack-{number}".encode()
        )
    independent = show_dir / "Dollface S01E03 1080p DSNP WEB-DL H.264-OTHER.mkv"
    independent.write_bytes(b"independent")
    try:
        headers = login(client)
        configure(client, headers)
        configured_root = create_show_root(client, headers, root, name="Nested qBit Shows")
        added_show = client.post("/api/v1/shows", headers=headers, json={"tmdb_id": 194764, "monitored": True})
        assert added_show.status_code == 201, added_show.text
        assert scan(client, headers, configured_root["id"])["status"] == "completed"
        qbit = client.post(
            "/api/v1/download-clients",
            headers=headers,
            json={
                "name": "qbit-shows-nested",
                "url": "http://qbit-nested.test:8080",
                "username": "media",
                "password": "secret",
                "scope": "shows",
                "category": "shows",
                "enabled": True,
                "poll_interval_seconds": 15,
            },
        )
        assert qbit.status_code == 201, qbit.text

        behavior = FakeQBitBehavior(torrents=[TorrentObservation(
            info_hash="nestedshowseasonpackhash",
            name=pack_name,
            progress=1.0,
            state="uploading",
            save_path=str(show_dir),
            content_path=str(pack),
            category="shows",
            tags=("managed",),
            tracker="https://tracker.example/announce",
            total_size=20_000,
            added_at=1_700_000_000,
            completed_at=1_700_000_100,
        )])
        from app.api import downloads as downloads_api

        client.app.dependency_overrides[downloads_api.get_qbit_client_factory] = lambda: behavior.factory
        try:
            polled = client.post(f"/api/v1/download-clients/{qbit.json()['id']}/poll", headers=headers)
            assert polled.status_code == 202, polled.text
            poll_job = wait_job(client, polled.json()["job_id"])
            assert poll_job["status"] == "completed", poll_job
            assert poll_job["summary"]["completed"] == 1
        finally:
            client.app.dependency_overrides.clear()

        show = client.get("/api/v1/shows/194764").json()
        e1 = episode_by_number(show, 1)
        e2 = episode_by_number(show, 2)
        e3 = episode_by_number(show, 3)
        pack_release_id = e1["media"][0]["show_release_id"]
        assert e2["media"][0]["show_release_id"] == pack_release_id
        assert e1["media"][0]["release_scope"] == "season_pack"
        assert e2["media"][0]["release_scope"] == "season_pack"
        assert e3["media"][0]["release_scope"] == "episode"
        assert e3["media"][0]["show_release_id"] != pack_release_id
        assert independent.exists() and independent.read_bytes() == b"independent"
    finally:
        client.app.dependency_overrides.clear()
        shutil.rmtree(root, ignore_errors=True)


def test_ranged_multi_episode_file_maps_every_episode_in_range(client: TestClient) -> None:
    root = Path.cwd() / f"ranged-multi-{uuid.uuid4().hex}"
    show_dir = root / "Dollface 2019"
    show_dir.mkdir(parents=True)
    media = show_dir / "Dollface S01E01-E03 1080p DSNP WEB-DL H.264-HONE.mkv"
    media.write_bytes(b"range")
    try:
        headers = login(client)
        configure(client, headers)
        configured = create_show_root(client, headers, root)
        scan(client, headers, configured["id"])
        show = detail(client)
        episodes = [episode_by_number(show, number) for number in (1, 2, 3)]
        assert all(episode["presence_state"] == "present" for episode in episodes)
        media_ids = {episode["media"][0]["media_file_id"] for episode in episodes}
        release_ids = {episode["media"][0]["show_release_id"] for episode in episodes}
        assert len(media_ids) == 1
        assert len(release_ids) == 1
        assert episodes[0]["media"][0]["mapped_episode_numbers"] == [1, 2, 3]
        assert episodes[0]["media"][0]["release_scope"] == "multi_episode"
        assert media.exists() and media.read_bytes() == b"range"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_season_folder_supplies_the_season_for_episode_only_filenames(client: TestClient) -> None:
    """``Season 1/01 - Title.mkv`` is a real layout; the folder is the evidence."""

    root = Path.cwd() / f"season-folder-{uuid.uuid4().hex}"
    show_dir = root / "Dollface 2019"
    (show_dir / "Season 1").mkdir(parents=True)
    (show_dir / "Season 1" / "01 - Pilot.mkv").write_bytes(b"episode")
    (show_dir / "Season 1" / "02 - Second.mkv").write_bytes(b"episode")
    try:
        headers = login(client)
        configure(client, headers)
        configured = create_show_root(client, headers, root)
        scan(client, headers, configured["id"])

        show = detail(client)
        for number in (1, 2):
            assert episode_by_number(show, number)["presence_state"] == "present"
        assert client.get("/api/v1/problems?reason=EPISODE_MAPPING_UNRESOLVED").json()["total"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize("folder", ["Season 1", "Season.1", "Season_1", "S01", "S1"])
def test_every_common_season_folder_spelling_is_recognised(client: TestClient, folder: str) -> None:
    root = Path.cwd() / f"season-spelling-{uuid.uuid4().hex}"
    show_dir = root / "Dollface 2019"
    (show_dir / folder).mkdir(parents=True)
    (show_dir / folder / "03 - Third.mkv").write_bytes(b"episode")
    try:
        headers = login(client)
        configure(client, headers)
        configured = create_show_root(client, headers, root)
        scan(client, headers, configured["id"])

        show = detail(client)
        assert episode_by_number(show, 3)["presence_state"] == "present"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_explicit_season_episode_marker_still_wins_over_the_folder(client: TestClient) -> None:
    """A filename that states S01E02 is trusted over a folder claiming season 5."""

    root = Path.cwd() / f"season-conflict-{uuid.uuid4().hex}"
    show_dir = root / "Dollface 2019"
    (show_dir / "Season 5").mkdir(parents=True)
    (show_dir / "Season 5" / "Dollface S01E02 1080p WEB-DL.mkv").write_bytes(b"episode")
    try:
        headers = login(client)
        configure(client, headers)
        configured = create_show_root(client, headers, root)
        scan(client, headers, configured["id"])

        show = detail(client)
        assert episode_by_number(show, 2)["presence_state"] == "present"
    finally:
        shutil.rmtree(root, ignore_errors=True)
