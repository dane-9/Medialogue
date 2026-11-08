"""Deleting a download from Downloads: the plan, the safeguards, and the library afterwards."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.api import downloads as downloads_api
from app.db import session as db_session
from app.models.domain import (AccessMode, AssociationType, IdentityState, MediaDirectory, MediaFile, Movie, MovieRelease,
                               MovieReleaseTorrent, ReleaseState, StorageRoot, Torrent, TorrentClientObservation)
from tests.test_torrent_archive import ArchiveQBitBehavior, _login, archive_client  # noqa: F401


def _setup(client, base, *, writable=True):
    headers = _login(client)
    root = base / "movies"
    root.mkdir()
    assert client.post("/api/v1/storage-roots", headers=headers,
                       json={"name": "Movies", "path": str(root), "media_type": "movies"}).status_code == 201
    qbit = client.post("/api/v1/download-clients", headers=headers, json={
        "name": "qbit", "url": "http://qbit.test:8080", "username": "u", "password": "p", "scope": "movies", "enabled": True,
    }).json()

    async def seed():
        async with db_session.async_session_factory() as db:
            storage = await db.scalar(select(StorageRoot))
            storage.access_mode = AccessMode.READ_WRITE if writable else AccessMode.READ_ONLY
            ids = {}
            for name in ("Keep", "Gone"):
                folder = root / f"{name} (2020)"
                folder.mkdir()
                (folder / f"{name}.2020.1080p.mkv").write_bytes(b"video")
                (folder / f"{name}.2020.1080p.srt").write_text("added by hand")
                movie = Movie(title=name, sort_title=name, year=2020, identity_state=IdentityState.MATCHED)
                db.add(movie); await db.flush()
                release = MovieRelease(movie_id=movie.id, raw_release_name=f"{name}.2020.1080p", release_state=ReleaseState.CURRENT)
                db.add(release); await db.flush()
                directory = MediaDirectory(storage_root_id=storage.id, resolved_path=str(folder), movie_release_id=release.id)
                db.add(directory); await db.flush()
                db.add(MediaFile(media_directory_id=directory.id, relative_path=f"{name}.2020.1080p.mkv", filename=f"{name}.2020.1080p.mkv"))
                torrent = Torrent(info_hash=(name[0] * 40).lower(), name=f"{name}.2020.1080p")
                cross = Torrent(info_hash=("x" if name == "Keep" else "y") * 40, name=f"{name}.2020.1080p cross-seed")
                db.add_all([torrent, cross]); await db.flush()
                db.add(MovieReleaseTorrent(movie_release_id=release.id, torrent_id=torrent.id, association_type=AssociationType.ATTACHED))
                rows = []
                for item in (torrent, cross):
                    row = TorrentClientObservation(torrent_id=item.id, download_client_id=uuid.UUID(qbit["id"]), is_present=True,
                                                   resolved_save_path=str(folder), last_seen_at=datetime.now(timezone.utc))
                    db.add(row); rows.append(row)
                await db.flush()
                ids[name] = {"movie": movie.id, "release": release.id, "download": rows[0].id, "cross": rows[1].id,
                             "folder": folder, "hashes": {torrent.info_hash, cross.info_hash}}
            await db.commit()
            return ids

    return headers, asyncio.run(seed())


def test_plan_lists_the_movie_folder_and_cross_seeds_then_keeps_the_title_as_missing(archive_client):
    client, base, _ = archive_client
    headers, ids = _setup(client, base)
    gone = ids["Gone"]

    plan = client.post("/api/v1/downloads/delete-plan", headers=headers, json={"download_ids": [str(gone["download"])]})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["blocked"] == []
    assert body["folders"] == [str(gone["folder"])]
    assert {(item["name"], item["selected"]) for item in body["torrents"]} == {
        ("Gone.2020.1080p", True), ("Gone.2020.1080p cross-seed", False)}
    assert body["titles"] == [{"kind": "movie", "title": "Gone", "year": 2020, "season_numbers": []}]

    behavior = ArchiveQBitBehavior()
    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    try:
        done = client.post("/api/v1/downloads/delete", headers=headers,
                           json={"download_ids": [str(gone["download"])], "library": "replace"})
        assert done.status_code == 200, done.text
    finally:
        client.app.dependency_overrides.clear()

    # The whole folder went, hand-added subtitles included; the other movie stayed.
    assert not gone["folder"].exists()
    assert ids["Keep"]["folder"].exists()
    assert {item["info_hash"] for item in behavior.removed} == gone["hashes"]
    assert all(item["delete_files"] is False for item in behavior.removed)

    async def check():
        async with db_session.async_session_factory() as db:
            release = await db.get(MovieRelease, gone["release"])
            assert release.release_state == ReleaseState.MISSING
            torrents = (await db.scalars(select(Torrent).where(Torrent.info_hash.in_(gone["hashes"])))).all()
            assert {item.readd_state for item in torrents} == {"removed"}
    asyncio.run(check())


def test_forget_removes_the_movie_from_medialogue(archive_client):
    client, base, _ = archive_client
    headers, ids = _setup(client, base)
    behavior = ArchiveQBitBehavior()
    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    try:
        done = client.post("/api/v1/downloads/delete", headers=headers,
                           json={"download_ids": [str(ids["Gone"]["download"])], "library": "forget"})
        assert done.status_code == 200, done.text
    finally:
        client.app.dependency_overrides.clear()

    async def check():
        async with db_session.async_session_factory() as db:
            assert await db.get(Movie, ids["Gone"]["movie"]) is None
            assert await db.get(Movie, ids["Keep"]["movie"]) is not None
    asyncio.run(check())

    # It is still on record in the Movies History.
    history = client.get("/api/v1/library/history?media_type=movies", headers=headers)
    assert history.status_code == 200, history.text
    assert [(item["title"], item["year"], item["release_name"], item["reason"]) for item in history.json()] == [
        ("Gone", 2020, "Gone.2020.1080p", "Deleted from Downloads")]
    assert client.get("/api/v1/library/history?media_type=shows", headers=headers).json() == []


def test_read_only_media_folder_blocks_and_deletes_nothing(archive_client):
    client, base, _ = archive_client
    headers, ids = _setup(client, base, writable=False)
    plan = client.post("/api/v1/downloads/delete-plan", headers=headers, json={"download_ids": [str(ids["Gone"]["download"])]})
    assert any("may only read" in item for item in plan.json()["blocked"])
    behavior = ArchiveQBitBehavior()
    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    try:
        done = client.post("/api/v1/downloads/delete", headers=headers,
                           json={"download_ids": [str(ids["Gone"]["download"])], "library": "replace"})
        assert done.status_code == 409
    finally:
        client.app.dependency_overrides.clear()
    assert ids["Gone"]["folder"].exists()
    assert behavior.removed == []


def test_a_torrent_saved_straight_into_the_media_folder_is_blocked(archive_client):
    client, base, _ = archive_client
    headers, ids = _setup(client, base)

    async def loose():
        async with db_session.async_session_factory() as db:
            torrent = Torrent(info_hash="z" * 40, name="Loose")
            db.add(torrent); await db.flush()
            row = TorrentClientObservation(torrent_id=torrent.id, download_client_id=(await db.scalar(select(TorrentClientObservation))).download_client_id,
                                           is_present=True, resolved_save_path=str(base / "movies"), last_seen_at=datetime.now(timezone.utc))
            db.add(row); await db.commit()
            return row.id

    download = asyncio.run(loose())
    plan = client.post("/api/v1/downloads/delete-plan", headers=headers, json={"download_ids": [str(download)]}).json()
    assert any("media folder itself" in item for item in plan["blocked"])


def _delete(client, headers, download, library):
    behavior = ArchiveQBitBehavior()
    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    try:
        return client.post("/api/v1/downloads/delete", headers=headers,
                           json={"download_ids": [str(download)], "library": library})
    finally:
        client.app.dependency_overrides.clear()


def test_a_folder_that_cannot_be_deleted_still_settles_the_library_and_reports_it(archive_client, monkeypatch):
    from app.models.domain import Event
    from app.services import download_deletion

    client, base, _ = archive_client
    headers, ids = _setup(client, base)

    def refuse(path):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(download_deletion, "_delete_path", refuse)
    done = _delete(client, headers, ids["Gone"]["download"], "forget")
    assert done.status_code == 500, done.text
    assert done.json()["error"]["code"] == "DELETE_INCOMPLETE"

    async def check():
        async with db_session.async_session_factory() as db:
            # Forget falls back to keeping the title while files may remain.
            assert await db.get(Movie, ids["Gone"]["movie"]) is not None
            release = await db.get(MovieRelease, ids["Gone"]["release"])
            assert release.release_state == ReleaseState.MISSING
            event = await db.scalar(select(Event).where(Event.event_type == "media.deleted"))
            assert event is not None and event.details["failed_folders"]
    asyncio.run(check())


def test_a_folder_inside_another_movies_folder_holding_its_files_is_blocked(archive_client):
    client, base, _ = archive_client
    headers, ids = _setup(client, base)
    keep = ids["Keep"]["folder"]
    (keep / "Sub").mkdir()
    (keep / "Sub" / "Keep.part2.mkv").write_bytes(b"video")

    async def nested():
        async with db_session.async_session_factory() as db:
            directory = await db.scalar(select(MediaDirectory).where(MediaDirectory.resolved_path == str(keep)))
            db.add(MediaFile(media_directory_id=directory.id, relative_path="Sub/Keep.part2.mkv", filename="Keep.part2.mkv"))
            client_id = (await db.scalar(select(TorrentClientObservation))).download_client_id
            torrent = Torrent(info_hash="n" * 40, name="Nested")
            db.add(torrent); await db.flush()
            row = TorrentClientObservation(torrent_id=torrent.id, download_client_id=client_id, is_present=True,
                                           resolved_save_path=str(keep / "Sub"), last_seen_at=datetime.now(timezone.utc))
            db.add(row); await db.commit()
            return row.id

    download = asyncio.run(nested())
    plan = client.post("/api/v1/downloads/delete-plan", headers=headers, json={"download_ids": [str(download)]}).json()
    assert any("also holds Keep" in item for item in plan["blocked"]), plan


def test_deleting_a_season_folder_inside_the_show_folder_marks_its_episodes_missing(archive_client):
    from app.models.domain import (Episode, EpisodeMediaMap, PresenceState, Season, Show, ShowRelease,
                                   ShowReleaseTorrent)

    client, base, _ = archive_client
    headers, _ids = _setup(client, base)
    shows_root = base / "shows"
    shows_root.mkdir()
    assert client.post("/api/v1/storage-roots", headers=headers,
                       json={"name": "Shows", "path": str(shows_root), "media_type": "shows"}).status_code == 201
    show_folder = shows_root / "Example (2020)"
    for season in (1, 2):
        (show_folder / f"Season {season}").mkdir(parents=True)
        (show_folder / f"Season {season}" / f"Example.S0{season}E01.mkv").write_bytes(b"video")

    async def seed():
        async with db_session.async_session_factory() as db:
            root = await db.scalar(select(StorageRoot).where(StorageRoot.name == "Shows"))
            root.access_mode = AccessMode.READ_WRITE
            show = Show(title="Example", year=2020)
            db.add(show); await db.flush()
            directory = MediaDirectory(storage_root_id=root.id, resolved_path=str(show_folder))
            db.add(directory); await db.flush()
            client_id = (await db.scalar(select(TorrentClientObservation))).download_client_id
            out = {}
            for number in (1, 2):
                season = Season(show_id=show.id, season_number=number)
                db.add(season); await db.flush()
                episode = Episode(show_id=show.id, season_id=season.id, season_number=number, episode_number=1,
                                  presence_state=PresenceState.PRESENT)
                release = ShowRelease(show_id=show.id, season_id=season.id, raw_release_name=f"Example.S0{number}",
                                      release_state=ReleaseState.CURRENT)
                media_file = MediaFile(media_directory_id=directory.id, relative_path=f"Season {number}/Example.S0{number}E01.mkv",
                                       filename=f"Example.S0{number}E01.mkv")
                db.add_all([episode, release, media_file]); await db.flush()
                db.add(EpisodeMediaMap(episode_id=episode.id, media_file_id=media_file.id, show_release_id=release.id))
                torrent = Torrent(info_hash=str(number) * 40, name=f"Example.S0{number}")
                db.add(torrent); await db.flush()
                db.add(ShowReleaseTorrent(show_release_id=release.id, torrent_id=torrent.id))
                row = TorrentClientObservation(torrent_id=torrent.id, download_client_id=client_id, is_present=True,
                                               resolved_save_path=str(show_folder / f"Season {number}"),
                                               last_seen_at=datetime.now(timezone.utc))
                db.add(row); await db.flush()
                out[number] = {"episode": episode.id, "file": media_file.id, "download": row.id}
            await db.commit()
            return out

    seeded = asyncio.run(seed())
    done = _delete(client, headers, seeded[1]["download"], "replace")
    assert done.status_code == 200, done.text
    assert not (show_folder / "Season 1").exists()
    assert (show_folder / "Season 2").exists()

    async def check():
        async with db_session.async_session_factory() as db:
            assert (await db.get(MediaFile, seeded[1]["file"])).exists is False
            assert (await db.get(MediaFile, seeded[2]["file"])).exists is True
            assert (await db.get(Episode, seeded[1]["episode"])).presence_state == PresenceState.MISSING
            assert (await db.get(Episode, seeded[2]["episode"])).presence_state == PresenceState.PRESENT
    asyncio.run(check())


def test_a_read_only_mount_blocks_deleting_and_saving_a_read_write_media_folder(archive_client, monkeypatch):
    from app.api import storage as storage_api
    from app.services import download_deletion

    client, base, _ = archive_client
    headers, ids = _setup(client, base)
    # Docker ":ro" (or a read-only share): the folder is there but cannot be changed.
    monkeypatch.setattr(download_deletion, "folder_writable", lambda path: False)
    monkeypatch.setattr(storage_api, "folder_writable", lambda path: False)

    plan = client.post("/api/v1/downloads/delete-plan", headers=headers, json={"download_ids": [str(ids["Gone"]["download"])]})
    assert any("mounted read-only" in item for item in plan.json()["blocked"])
    assert _delete(client, headers, ids["Gone"]["download"], "replace").status_code == 409
    assert ids["Gone"]["folder"].exists()

    root = client.get("/api/v1/storage-roots", headers=headers).json()["items"][0]
    refused = client.patch(f"/api/v1/storage-roots/{root['id']}", headers=headers, json={"access_mode": "read_write"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "MEDIA_FOLDER_NOT_WRITABLE"
    # Read only is always allowed.
    assert client.patch(f"/api/v1/storage-roots/{root['id']}", headers=headers, json={"access_mode": "read_only"}).status_code == 200
    (base / "shows").mkdir()
    created = client.post("/api/v1/storage-roots", headers=headers, json={
        "name": "Shows", "path": str(base / "shows"), "media_type": "shows", "access_mode": "read_write"})
    assert created.status_code == 409
