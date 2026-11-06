"""Google Drive copy of the torrent archive: mirroring, database copies, restore, coverage."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.models.domain import MediaType
from tests.test_part17_recovery import login_headers, recovery_env  # noqa: F401 - fixture

INFO_HASH = "ab" + "c" * 38


def _archive_fixture(root: Path, info_hash: str = INFO_HASH, *, priority: int = 0) -> tuple[Path, Path]:
    payload = b"d4:infod4:name7:exampleee"
    manifest = {"schema_version": 1, "torrent_info_hash": info_hash, "torrent_name": "Example",
                "archive": {"torrent_sha256": hashlib.sha256(payload).hexdigest()},
                "recovery": {"captured_at": "2026-01-01T00:00:00+00:00", "properties": {"save_path": "/data/movies"},
                             "files": [{"index": 0, "priority": priority, "progress": 0.5}], "trackers": []},
                "media_associations": [{"media_type": "movies", "title": "Example"}]}
    torrent = root / "torrents" / info_hash[:2] / f"{info_hash}.torrent"
    metadata = root / "manifests" / info_hash[:2] / f"{info_hash}.json"
    torrent.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    torrent.write_bytes(payload)
    metadata.write_text(json.dumps(manifest))
    return torrent, metadata


class FakeDrive:
    """In-memory Drive with the calls the backup makes."""

    files: dict[str, dict] = {}

    def __init__(self, token: str):
        self.client = None

    async def close(self):
        pass

    @classmethod
    def reset(cls):
        cls.files = {}

    @classmethod
    def _row(cls, file_id: str) -> dict:
        item = cls.files[file_id]
        return {"id": file_id, "name": item["name"], "size": str(len(item.get("data", b""))),
                "sha256Checksum": hashlib.sha256(item.get("data", b"")).hexdigest(),
                "appProperties": item["properties"], "createdTime": item["created"], "trashed": item["trashed"]}

    @classmethod
    def live(cls, parent_name: str) -> list[dict]:
        parent = next((key for key, item in cls.files.items() if item["name"] == parent_name and item.get("folder")), None)
        return [cls._row(key) for key, item in cls.files.items() if item.get("parent") == parent and not item["trashed"]]

    def _add(self, name: str, parent: str | None, *, data: bytes = b"", properties: dict | None = None, folder: bool = False) -> str:
        file_id = f"f{len(self.files) + 1}"
        created = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=len(self.files))).isoformat()
        self.files[file_id] = {"name": name, "parent": parent, "data": data, "properties": properties or {},
                               "created": created, "trashed": False, "folder": folder}
        return file_id

    async def children(self, folder_id: str) -> list[dict]:
        return [self._row(key) for key, item in self.files.items() if item["parent"] == folder_id and not item["trashed"]]

    async def folder(self, name: str, parent: str | None) -> str:
        for key, item in self.files.items():
            if item["folder"] and item["name"] == name and item["parent"] == parent and not item["trashed"]:
                return key
        return self._add(name, parent, folder=True)

    async def exists(self, file_id: str) -> bool:
        return file_id in self.files and not self.files[file_id]["trashed"]

    async def put_small(self, data, *, name, parent, properties, file_id=None):
        from app.services.remote_backups import DriveMissing, MARK
        if file_id:
            if file_id not in self.files:
                raise DriveMissing(file_id)
            self.files[file_id].update(data=data, properties={**MARK, **properties})
            return self._row(file_id)
        return self._row(self._add(name, parent, data=data, properties={**MARK, **properties}))

    async def put_large(self, path, *, name, parent, properties):
        return await self.put_small(path.read_bytes(), name=name, parent=parent, properties=properties)

    async def download(self, file_id: str) -> bytes:
        return self.files[file_id]["data"]

    async def trash(self, file_id: str) -> None:
        self.files[file_id]["trashed"] = True

    async def request(self, method, url, **kwargs):
        return httpx.Response(200, json=self._row(url.rsplit("/", 1)[1]))


@pytest.fixture
def drive(monkeypatch):
    from app.services import remote_backups as remote

    FakeDrive.reset()
    monkeypatch.setattr(remote, "DriveBackups", FakeDrive)

    async def token():
        return "token"

    monkeypatch.setattr(remote, "access_token", token)
    remote.update_backup_settings({"refresh_token": "refresh"})
    return remote


def _run(job_type: str, *, manual: bool = True):
    from app.db import session as db_session
    from app.models.domain import Job
    from app.services import remote_backups as remote
    from app.services.jobs import create_job

    async def go():
        async with db_session.async_session_factory() as db:
            job = await create_job(db, job_type, cancellable=False, summary={"trigger": "manual" if manual else "schedule"})
            await db.commit()
            job_id = job.id
        await (remote.run_remote_backup if job_type == "remote_backup" else remote.run_drive_restore)(job_id)
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            return job.status.value, dict(job.summary or {}), job.error

    return asyncio.run(go())


def test_torrents_go_up_once_and_only_restore_relevant_changes_go_again(recovery_env, drive):
    client, settings, _ = recovery_env
    torrent, manifest = _archive_fixture(Path(settings.torrent_archive_dir))

    status, summary, _ = _run("remote_backup")
    assert status == "completed", summary
    assert (summary["uploaded"], summary["failed"]) == (2, 0)
    assert summary["database"]["file"].startswith("medialogue-database-")
    assert {row["name"] for row in FakeDrive.live("torrents")} == {torrent.name}
    assert {row["name"] for row in FakeDrive.live("manifests")} == {manifest.name}
    assert len(FakeDrive.live("database")) == 1
    state = drive.read_backup_settings()
    assert state["last_sync_at"] and state["last_database_at"] and state["last_error"] is None

    # A scheduled run with nothing new uploads nothing, and the database is not due.
    status, summary, _ = _run("remote_backup", manual=False)
    assert (status, summary["uploaded"], summary["updated"], summary["database"]) == ("completed", 0, 0, None)

    # Telemetry in a manifest (progress, capture time) is not worth a re-upload...
    data = json.loads(manifest.read_text())
    data["recovery"]["captured_at"] = "2026-02-01T00:00:00+00:00"
    data["recovery"]["files"][0]["progress"] = 0.9
    manifest.write_text(json.dumps(data))
    status, summary, _ = _run("remote_backup", manual=False)
    assert (summary["uploaded"], summary["updated"]) == (0, 0)

    # ...but a change a restore would use is.
    data["recovery"]["files"][0]["priority"] = 1
    manifest.write_text(json.dumps(data))
    status, summary, _ = _run("remote_backup", manual=False)
    assert (summary["uploaded"], summary["updated"]) == (0, 1)
    stored = json.loads(FakeDrive.files[FakeDrive.live("manifests")[0]["id"]]["data"])
    assert stored["recovery"]["files"][0]["priority"] == 1


def test_lost_local_state_reuses_what_drive_already_holds(recovery_env, drive):
    _, settings, _ = recovery_env
    _archive_fixture(Path(settings.torrent_archive_dir))
    assert _run("remote_backup")[0] == "completed"
    drive.update_backup_settings({"mirror": {}})
    status, summary, _ = _run("remote_backup", manual=False)
    assert (status, summary["uploaded"], summary["updated"]) == ("completed", 0, 0)


def test_a_deleted_drive_folder_is_recreated_and_filled_again(recovery_env, drive):
    _, settings, _ = recovery_env
    _archive_fixture(Path(settings.torrent_archive_dir))
    assert _run("remote_backup")[0] == "completed"
    for item in FakeDrive.files.values():
        item["trashed"] = True
    status, summary, _ = _run("remote_backup", manual=False)
    assert (status, summary["uploaded"]) == ("completed", 2)


def test_only_the_newest_database_copies_are_kept(recovery_env, drive):
    drive.update_backup_settings({"keep_database": 2})
    for _ in range(3):
        assert _run("remote_backup")[0] == "completed"
    assert len(FakeDrive.live("database")) == 2


def test_restore_fetches_drive_files_and_lists_them_without_touching_qbittorrent(recovery_env, drive):
    client, settings, _ = recovery_env
    headers = login_headers(client)
    torrent, manifest = _archive_fixture(Path(settings.torrent_archive_dir))
    assert _run("remote_backup")[0] == "completed"
    torrent.unlink()
    manifest.unlink()

    status, summary, error = _run("drive_restore")
    assert status == "completed", error
    assert (summary["downloaded"], summary["catalogue_imported"]) == (2, 1)
    assert torrent.is_file() and manifest.is_file()
    rows = client.get("/api/v1/torrent-archive", headers=headers).json()["items"]
    assert [(row["info_hash"], row["media_title"], row["archive_verified"], row["qbit_present"]) for row in rows] == [
        (INFO_HASH, "Example", True, False)]
    assert rows[0]["included_in_remote_backup"] is True

    # Running it again changes nothing.
    status, summary, _ = _run("drive_restore")
    assert (summary["downloaded"], summary["present"], summary["catalogue_imported"]) == (0, 2, 0)


def test_rebuild_lists_archived_torrents_without_inventing_live_presence(recovery_env):
    client, settings, _ = recovery_env
    headers = login_headers(client)
    _archive_fixture(Path(settings.torrent_archive_dir))
    response = client.post("/api/v1/torrent-archive/rebuild", headers=headers)
    assert response.json() == {"imported": 1, "skipped": 0, "errors": []}
    rows = client.get("/api/v1/torrent-archive").json()["items"]
    assert (rows[0]["qbit_present"], rows[0]["source_state"], rows[0]["included_in_remote_backup"]) == (False, "unknown", False)
    assert client.post("/api/v1/torrent-archive/rebuild", headers=headers).json()["skipped"] == 1


def test_coverage_counts_each_release_by_how_restorable_it_is(recovery_env, drive):
    from app.db import session as db_session
    from app.models.domain import Movie, MovieRelease, MovieReleaseTorrent, ReleaseState, Torrent, TorrentArchiveState

    client, _, _ = recovery_env
    login_headers(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            rows = {}
            for name, state in (("Protected", TorrentArchiveState.ARCHIVED), ("Local", TorrentArchiveState.ARCHIVED),
                                ("Unarchived", TorrentArchiveState.FAILED), ("Bare", None)):
                movie = Movie(title=name, sort_title=name.lower(), year=2020)
                db.add(movie)
                await db.flush()
                release = MovieRelease(movie_id=movie.id, raw_release_name=f"{name}.2020.1080p", release_state=ReleaseState.CURRENT)
                db.add(release)
                await db.flush()
                if state is not None:
                    info_hash = uuid4().hex + "00000000"
                    torrent = Torrent(info_hash=info_hash, name=name, archive_state=state)
                    db.add(torrent)
                    await db.flush()
                    db.add(MovieReleaseTorrent(movie_release_id=release.id, torrent_id=torrent.id))
                    rows[name] = info_hash
            await db.commit()
            return rows

    hashes = asyncio.run(seed())
    key = hashes["Protected"]
    drive.update_backup_settings({"mirror": {f"torrents/{key}.torrent": {"id": "a"}, f"manifests/{key}.json": {"id": "b"}}})
    result = client.get("/api/v1/backups/coverage").json()
    assert (result["total"], result["protected"], result["local_only"], result["not_archived"], result["no_torrent"]) == (4, 1, 1, 1, 1)
    assert [gap["title"] for gap in result["gaps"]] == ["Bare", "Unarchived", "Local"]


def test_reimport_uses_the_only_client_for_the_media_type_on_a_fresh_install():
    from app.services.recovery import _client_for_manifest

    movies = SimpleNamespace(id=uuid4(), name="New qBit", enabled=True, scope=MediaType.MOVIES)
    shows = SimpleNamespace(id=uuid4(), name="Shows qBit", enabled=True, scope=MediaType.SHOWS)
    manifest = {"download_client_observations": [{"download_client_id": str(uuid4()), "download_client_name": "Old qBit",
                                                  "category": "movies"}]}
    client, observation, warnings = _client_for_manifest(manifest, [movies, shows], MediaType.MOVIES)
    assert client is movies and observation["category"] == "movies" and warnings
    second = SimpleNamespace(id=uuid4(), name="Other", enabled=True, scope=MediaType.MOVIES)
    assert _client_for_manifest(manifest, [movies, second], MediaType.MOVIES)[0] is None


@pytest.mark.asyncio
async def test_large_upload_resumes_after_network_interruption(tmp_path, monkeypatch):
    from app.services.remote_backups import DriveBackups

    path = tmp_path / "database.zip"
    path.write_bytes(b"x" * (5 * 1024 * 1024))
    drive = DriveBackups("token")
    await drive.client.aclose()
    calls = []
    interrupted = False

    def handler(request):
        nonlocal interrupted
        calls.append(request.headers.get("Content-Range"))
        if request.method == "POST":
            return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/session"})
        if request.headers["Content-Range"].startswith("bytes */"):
            return httpx.Response(308, headers={"Range": "bytes=0-4194303"})
        if not interrupted:
            interrupted = True
            raise httpx.ReadError("lost response", request=request)
        return httpx.Response(200, json={"id": "uploaded"})

    drive.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def no_sleep(_):
        pass

    monkeypatch.setattr("app.services.remote_backups.asyncio.sleep", no_sleep)
    try:
        assert (await drive.put_large(path, name="database.zip", parent="folder", properties={}))["id"] == "uploaded"
        assert calls[-1] == f"bytes 4194304-{path.stat().st_size - 1}/{path.stat().st_size}"
    finally:
        await drive.close()
