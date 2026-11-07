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
        await remote.run_remote_backup(job_id)
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


def test_database_copies_are_thinned_by_the_retention_rules(recovery_env, drive):
    # Every copy in these tests is made the same day, so only "recent" keeps more than one.
    drive.update_backup_settings({"keep_recent": 2})
    for _ in range(3):
        assert _run("remote_backup")[0] == "completed"
    assert len(FakeDrive.live("database")) == 2
    drive.update_backup_settings({"keep_recent": 0})
    assert _run("remote_backup")[0] == "completed"
    assert len(FakeDrive.live("database")) == 1


def test_the_old_keep_setting_becomes_the_daily_count():
    from app.services.remote_backups import retention_settings

    assert retention_settings({"keep_database": 14}) == {"keep_recent": 0, "keep_daily": 14, "keep_weekly": 4, "keep_monthly": 6}
    assert retention_settings({"keep_database": 14, "keep_daily": 3})["keep_daily"] == 3
    assert retention_settings({}) == {"keep_recent": 0, "keep_daily": 7, "keep_weekly": 4, "keep_monthly": 6}


def test_retention_keeps_every_recent_copy_and_one_per_week_and_month_after():
    from app.services.remote_backups import copies_to_keep

    start = datetime(2026, 11, 8, 3, tzinfo=timezone.utc)
    # A copy every day for a year, newest first, plus a second copy today.
    copies = [{"id": f"d{day}", "createdTime": (start - timedelta(days=day)).isoformat()} for day in range(365)]
    copies.append({"id": "today-early", "createdTime": (start - timedelta(hours=2)).isoformat()})
    copies.append({"id": "undated", "createdTime": ""})
    keep = copies_to_keep(copies, keep_recent=0, keep_daily=7, keep_weekly=4, keep_monthly=6)

    assert "undated" in keep  # never deleted when its age is unknown
    assert keep["d0"][0] == "newest"
    assert "today-early" not in keep  # same day as a newer copy
    assert {f"d{day}" for day in range(7)} <= set(keep)  # the last seven days
    weekly = {key for key, rules in keep.items() if "weekly" in rules}
    monthly = {key for key, rules in keep.items() if "monthly" in rules}
    assert len(weekly) == 4 and len(monthly) == 6
    # Six calendar months, this one included: the oldest is the last copy of
    # the month five before this one, and nothing older.
    oldest = max(int(key[1:]) for key in keep if key.startswith("d"))
    assert oldest == (start - datetime(2026, 6, 30, 3, tzinfo=timezone.utc)).days
    assert len(keep) <= 1 + 7 + 4 + 6


def _drive_download_zip(torrent: Path, manifest: Path) -> bytes:
    """The backup folder as Google Drive zips it on download."""

    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(f"Medialogue Backups/torrents/{torrent.name}", torrent.read_bytes())
        bundle.writestr(f"Medialogue Backups/manifests/{manifest.name}", manifest.read_bytes())
        bundle.writestr("Medialogue Backups/database/medialogue-database-20260101T000000Z.zip", b"not used")
        bundle.writestr("Medialogue Backups/torrents/../../escape.torrent", b"d4:infod4:name1:xee")
        bundle.writestr("Medialogue Backups/torrents/notahash!.torrent", b"d4:infod4:name1:xee")
    return buffer.getvalue()


def test_uploaded_backup_zip_restores_the_archive_without_touching_qbittorrent(recovery_env):
    client, settings, root = recovery_env
    headers = login_headers(client)
    torrent, manifest = _archive_fixture(Path(settings.torrent_archive_dir))
    upload = _drive_download_zip(torrent, manifest)
    torrent.unlink()
    manifest.unlink()

    response = client.post("/api/v1/backups/restore/upload", headers={**headers, "Content-Type": "application/zip"}, content=upload)
    assert response.status_code == 200, response.text
    assert (response.json()["written"], response.json()["listed"], response.json()["skipped"]) == (2, 1, 1)
    assert torrent.is_file() and manifest.is_file()
    assert not (Path(settings.torrent_archive_dir).parent / "escape.torrent").exists()
    rows = client.get("/api/v1/torrent-archive").json()["items"]
    assert [(row["info_hash"], row["media_title"], row["archive_verified"], row["qbit_present"]) for row in rows] == [
        (INFO_HASH, "Example", True, False)]

    # The same ZIP again changes nothing.
    again = client.post("/api/v1/backups/restore/upload", headers=headers, content=upload).json()
    assert (again["written"], again["present"], again["listed"]) == (0, 2, 0)


def test_upload_rejects_files_that_are_not_a_backup(recovery_env):
    client, _, _ = recovery_env
    headers = login_headers(client)
    assert client.post("/api/v1/backups/restore/upload", headers=headers, content=b"plain text").json()["error"]["code"] == "BACKUP_UPLOAD_INVALID"
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("photos/cat.jpg", b"meow")
    assert client.post("/api/v1/backups/restore/upload", headers=headers, content=buffer.getvalue()).status_code == 422
    assert client.post("/api/v1/backups/restore/upload", content=buffer.getvalue()).status_code in {401, 403}


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
    # A status filter narrows the listed gaps but never the counts.
    only = client.get("/api/v1/backups/coverage?status=no_torrent").json()
    assert [gap["title"] for gap in only["gaps"]] == ["Bare"]
    assert (only["total"], only["local_only"], only["not_archived"]) == (4, 1, 1)


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


def _seed_prune_cases(archive_root: Path) -> dict[str, str]:
    """One archived torrent per situation the pruning rule has to decide."""

    from app.db import session as db_session
    from app.models.domain import (DownloadClient, Movie, MovieRelease, MovieReleaseTorrent, ReleaseState, Torrent,
                                   TorrentArchiveState, TorrentClientObservation)

    removed = datetime.now(timezone.utc) - timedelta(days=1)
    # name: (release state or None for no release, observations as (present, removed_at))
    cases = {
        "upgraded": (ReleaseState.REPLACED, [(False, removed)]),
        "unlinked_gone": (None, [(False, removed)]),
        "current_gone": (ReleaseState.CURRENT, [(False, removed)]),
        "missing_files": (ReleaseState.MISSING, [(False, removed)]),
        "removed_from_app_seeding": (None, [(True, None)]),
        "one_client_left": (ReleaseState.REPLACED, [(False, removed), (True, None)]),
        "never_seen": (ReleaseState.REPLACED, []),
        "cross_seed_a": (ReleaseState.CURRENT, [(True, None)]),
    }

    async def seed():
        hashes = {}
        async with db_session.async_session_factory() as db:
            clients = [DownloadClient(), DownloadClient()]
            db.add_all(clients)
            await db.flush()
            shared_release = None
            for index, (name, (state, observations)) in enumerate(cases.items()):
                info_hash = f"{index:02x}" + "e" * 38
                _archive_fixture(archive_root, info_hash)
                torrent_path, manifest_path = (archive_root / "torrents" / info_hash[:2] / f"{info_hash}.torrent",
                                               archive_root / "manifests" / info_hash[:2] / f"{info_hash}.json")
                torrent = Torrent(info_hash=info_hash, name=name, archive_state=TorrentArchiveState.ARCHIVED,
                                  archive_path=str(torrent_path), manifest_path=str(manifest_path))
                db.add(torrent)
                await db.flush()
                if state is not None:
                    movie = Movie(title=name, sort_title=name, year=2020)
                    db.add(movie)
                    await db.flush()
                    release = MovieRelease(movie_id=movie.id, raw_release_name=f"{name}.2020.1080p", release_state=state)
                    db.add(release)
                    await db.flush()
                    db.add(MovieReleaseTorrent(movie_release_id=release.id, torrent_id=torrent.id))
                    if name == "cross_seed_a":
                        shared_release = release
                for client, (present, removed_at) in zip(clients, observations):
                    db.add(TorrentClientObservation(torrent_id=torrent.id, download_client_id=client.id,
                                                    is_present=present, removed_at=removed_at))
                hashes[name] = info_hash
            # A cross-seed of a current release, already gone from qBittorrent.
            info_hash = "ff" + "e" * 38
            _archive_fixture(archive_root, info_hash)
            partner = Torrent(info_hash=info_hash, name="cross_seed_b", archive_state=TorrentArchiveState.ARCHIVED,
                              archive_path=str(archive_root / "torrents/ff" / f"{info_hash}.torrent"),
                              manifest_path=str(archive_root / "manifests/ff" / f"{info_hash}.json"))
            db.add(partner)
            await db.flush()
            db.add(MovieReleaseTorrent(movie_release_id=shared_release.id, torrent_id=partner.id))
            db.add(TorrentClientObservation(torrent_id=partner.id, download_client_id=clients[0].id,
                                            is_present=False, removed_at=removed))
            hashes["cross_seed_b"] = info_hash
            await db.commit()
        return hashes

    return asyncio.run(seed())


def test_only_torrents_nothing_on_the_server_uses_are_pruned(recovery_env):
    from app.db import session as db_session
    from app.services.torrent_archive import prune_retired_archives

    client, settings, _ = recovery_env
    login_headers(client)
    archive = Path(settings.torrent_archive_dir)
    hashes = _seed_prune_cases(archive)

    async def prune():
        async with db_session.async_session_factory() as db:
            result = await prune_retired_archives(db)
            await db.commit()
            return result

    result = asyncio.run(prune())
    assert sorted(result["info_hashes"]) == sorted([hashes["upgraded"], hashes["unlinked_gone"]])
    for name, info_hash in hashes.items():
        exists = (archive / "torrents" / info_hash[:2] / f"{info_hash}.torrent").exists()
        assert exists == (name not in {"upgraded", "unlinked_gone"}), name
    listed = {row["info_hash"] for row in client.get("/api/v1/torrent-archive").json()["items"]}
    assert hashes["upgraded"] not in listed and hashes["current_gone"] in listed
    assert asyncio.run(prune())["pruned"] == 0


def test_pruned_torrents_leave_drive_only_after_the_grace_period(recovery_env, drive):
    from app.db import session as db_session
    from app.models.domain import Torrent
    from app.services.torrent_archive import prune_retired_archives
    from sqlalchemy import select

    _, settings, _ = recovery_env
    hashes = _seed_prune_cases(Path(settings.torrent_archive_dir))
    assert _run("remote_backup")[0] == "completed"
    on_drive = lambda: {row["name"].split(".")[0] for row in FakeDrive.live("torrents")}  # noqa: E731
    assert hashes["upgraded"] in on_drive()

    async def prune(age_days: int):
        async with db_session.async_session_factory() as db:
            await prune_retired_archives(db)
            for torrent in await db.scalars(select(Torrent).where(Torrent.info_hash == hashes["upgraded"])):
                stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
                torrent.metadata_json = {**torrent.metadata_json, "archive_pruned_at": stamp}
            await db.commit()

    asyncio.run(prune(1))
    assert _run("remote_backup", manual=False)[0] == "completed"
    assert hashes["upgraded"] in on_drive()  # still within the grace period

    asyncio.run(prune(31))
    # The database copy from the first run still lists it, so it stays on Drive.
    status, summary, _ = _run("remote_backup", manual=False)
    assert (status, summary["removed"]) == ("completed", 0)
    assert hashes["upgraded"] in on_drive()
    # A new copy no longer lists it and replaces the old one (same day), which
    # releases the torrent: the next run removes it.
    assert _run("remote_backup")[0] == "completed"
    status, summary, _ = _run("remote_backup", manual=False)
    assert (status, summary["removed"]) == ("completed", 2)  # its .torrent and manifest
    assert hashes["upgraded"] not in on_drive()
    assert hashes["unlinked_gone"] in on_drive()  # pruned a day ago, still kept
    assert hashes["current_gone"] in on_drive()


def test_each_database_copy_lists_its_torrents_and_keeps_its_manifests(recovery_env, drive):
    import io
    import zipfile

    _, settings, _ = recovery_env
    _archive_fixture(Path(settings.torrent_archive_dir))
    assert _run("remote_backup")[0] == "completed"
    [copy] = FakeDrive.live("database")
    with zipfile.ZipFile(io.BytesIO(FakeDrive.files[copy["id"]]["data"])) as archive:
        assert json.loads(archive.read("catalogue/torrents.json"))["torrents"] == [INFO_HASH]
        assert f"catalogue/manifests/{INFO_HASH}.json" in archive.namelist()

    # A lost local index is rebuilt from the copies on Drive.
    from app.services.remote_backups import _index_path, protected_hashes, read_backup_settings

    _index_path().unlink()

    async def protected():
        return await protected_hashes(FakeDrive("token"), read_backup_settings()["folders"])

    assert asyncio.run(protected()) == {INFO_HASH}
    assert _index_path().is_file()
