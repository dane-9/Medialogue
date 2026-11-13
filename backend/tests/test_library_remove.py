"""Removing movies and shows from Movies and Shows: with their files, or keeping them."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.api import downloads as downloads_api
from app.db import session as db_session
from app.integrations.filesystem import FilesystemObserver
from app.models.domain import (AccessMode, Episode, EpisodeMediaMap, IgnoredFolder, MediaDirectory, MediaFile, Movie,
                               PresenceState, ReleaseState, Season, Show, ShowRelease, ShowReleaseTorrent, StorageRoot,
                               Torrent, TorrentClientObservation)
from tests.test_download_deletion import _setup
from tests.test_torrent_archive import ArchiveQBitBehavior, archive_client  # noqa: F401


def _remove(client, headers, *, mode, movie_ids=(), show_ids=()):
    behavior = ArchiveQBitBehavior()
    client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    try:
        response = client.post("/api/v1/library/remove", headers=headers, json={
            "movie_ids": [str(item) for item in movie_ids], "show_ids": [str(item) for item in show_ids], "mode": mode})
    finally:
        client.app.dependency_overrides.clear()
    return response, behavior


def test_the_plan_lists_the_movie_folder_and_every_torrent_of_it(archive_client):
    client, base, _ = archive_client
    headers, ids = _setup(client, base)
    gone = ids["Gone"]

    plan = client.post("/api/v1/library/remove-plan", headers=headers, json={"movie_ids": [str(gone["movie"])]})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["blocked"] == []
    assert body["folders"] == [str(gone["folder"])]
    assert {(item["name"], item["selected"]) for item in body["torrents"]} == {
        ("Gone.2020.1080p", True), ("Gone.2020.1080p cross-seed", False)}
    assert body["titles"] == [{"kind": "movie", "title": "Gone", "year": 2020, "season_numbers": []}]


def test_removing_with_files_deletes_the_folder_and_the_movie(archive_client):
    client, base, _ = archive_client
    headers, ids = _setup(client, base)
    gone = ids["Gone"]

    done, behavior = _remove(client, headers, mode="forget", movie_ids=[gone["movie"]])
    assert done.status_code == 200, done.text
    assert not gone["folder"].exists()
    assert ids["Keep"]["folder"].exists()
    assert {item["info_hash"] for item in behavior.removed} == gone["hashes"]

    async def check():
        async with db_session.async_session_factory() as db:
            assert await db.get(Movie, gone["movie"]) is None
            assert await db.get(Movie, ids["Keep"]["movie"]) is not None
            assert (await db.scalars(select(IgnoredFolder))).all() == []
    asyncio.run(check())
    history = client.get("/api/v1/library/history?media_type=movies", headers=headers).json()
    assert [(item["title"], item["reason"]) for item in history] == [("Gone", "Removed with its files")]


def test_a_read_only_media_folder_blocks_removing_with_files_but_not_keeping_them(archive_client):
    client, base, _ = archive_client
    headers, ids = _setup(client, base, writable=False)
    gone = ids["Gone"]

    plan = client.post("/api/v1/library/remove-plan", headers=headers, json={"movie_ids": [str(gone["movie"])]}).json()
    assert any("may only read" in item for item in plan["blocked"])
    refused, behavior = _remove(client, headers, mode="forget", movie_ids=[gone["movie"]])
    assert refused.status_code == 409
    assert gone["folder"].exists() and behavior.removed == []

    kept, behavior = _remove(client, headers, mode="keep_files", movie_ids=[gone["movie"]])
    assert kept.status_code == 200, kept.text
    assert gone["folder"].exists() and behavior.removed == []


def test_keeping_the_files_forgets_the_movie_and_scans_skip_its_folder_until_unignored(archive_client):
    from app.services.reconciliation import reconcile_movie_directory

    client, base, _ = archive_client
    headers, ids = _setup(client, base)
    gone = ids["Gone"]

    done, behavior = _remove(client, headers, mode="keep_files", movie_ids=[gone["movie"]])
    assert done.status_code == 200, done.text
    # Nothing on disk or in qBittorrent was touched.
    assert gone["folder"].exists()
    assert behavior.removed == []

    ignored = client.get("/api/v1/library/ignored-folders", headers=headers)
    assert ignored.status_code == 200, ignored.text
    assert [(item["resolved_path"], item["title"], item["media_type"]) for item in ignored.json()] == [
        (str(gone["folder"]), "Gone", "movies")]
    history = client.get("/api/v1/library/history?media_type=movies", headers=headers).json()
    assert [(item["title"], item["reason"]) for item in history] == [("Gone", "Removed, files kept")]

    async def scan():
        async with db_session.async_session_factory() as db:
            root = await db.scalar(select(StorageRoot))
            observation = FilesystemObserver().inspect_directory(Path(gone["folder"]), Path(root.resolved_root_path).resolve())
            result = await reconcile_movie_directory(db, root, observation)
            await db.commit()
            return result, await db.scalar(select(MediaDirectory).where(MediaDirectory.resolved_path == observation.path))

    async def movie_gone():
        async with db_session.async_session_factory() as db:
            return await db.get(Movie, gone["movie"]) is None

    assert asyncio.run(movie_gone())
    result, directory = asyncio.run(scan())
    assert result == "ignored" and directory is None

    folder_id = ignored.json()[0]["id"]
    assert client.delete(f"/api/v1/library/ignored-folders/{folder_id}", headers=headers).status_code == 200
    assert client.get("/api/v1/library/ignored-folders", headers=headers).json() == []

    async def still_ignored():
        from app.services.ignored_folders import folder_ignored

        async with db_session.async_session_factory() as db:
            return await folder_ignored(db, str(Path(gone["folder"]).resolve()))

    assert asyncio.run(still_ignored()) is False


def test_removing_a_show_deletes_the_whole_show_folder(archive_client):
    client, base, _ = archive_client
    headers, _ids = _setup(client, base)
    shows_root = base / "shows"
    shows_root.mkdir()
    assert client.post("/api/v1/storage-roots", headers=headers,
                       json={"name": "Shows", "path": str(shows_root), "media_type": "shows"}).status_code == 201
    show_folder = shows_root / "Example (2020)"
    for number in (1, 2):
        (show_folder / f"Season {number}").mkdir(parents=True)
        (show_folder / f"Season {number}" / f"Example.S0{number}E01.mkv").write_bytes(b"video")
    (show_folder / "poster.jpg").write_bytes(b"art")

    async def seed():
        async with db_session.async_session_factory() as db:
            root = await db.scalar(select(StorageRoot).where(StorageRoot.name == "Shows"))
            root.access_mode = AccessMode.READ_WRITE
            show = Show(title="Example", year=2020)
            db.add(show); await db.flush()
            directory = MediaDirectory(storage_root_id=root.id, resolved_path=str(show_folder))
            db.add(directory); await db.flush()
            client_id = (await db.scalar(select(TorrentClientObservation))).download_client_id
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
                db.add(TorrentClientObservation(torrent_id=torrent.id, download_client_id=client_id, is_present=True,
                                                resolved_save_path=str(show_folder / f"Season {number}"),
                                                last_seen_at=datetime.now(timezone.utc)))
            await db.commit()
            return show.id

    show_id = asyncio.run(seed())
    plan = client.post("/api/v1/library/remove-plan", headers=headers, json={"show_ids": [str(show_id)]}).json()
    assert plan["blocked"] == []
    assert plan["folders"] == [str(show_folder)]
    assert len(plan["torrents"]) == 2

    done, behavior = _remove(client, headers, mode="forget", show_ids=[show_id])
    assert done.status_code == 200, done.text
    assert not show_folder.exists()
    assert {item["info_hash"] for item in behavior.removed} == {"1" * 40, "2" * 40}

    async def check():
        async with db_session.async_session_factory() as db:
            assert await db.get(Show, show_id) is None
    asyncio.run(check())
    history = client.get("/api/v1/library/history?media_type=shows", headers=headers).json()
    assert [(item["title"], item["reason"]) for item in history] == [("Example", "Removed with its files")]


def test_nothing_to_remove_is_not_found(archive_client):
    client, base, _ = archive_client
    headers, _ids = _setup(client, base)
    response = client.post("/api/v1/library/remove-plan", headers=headers, json={"movie_ids": [], "show_ids": []})
    assert response.status_code == 404
