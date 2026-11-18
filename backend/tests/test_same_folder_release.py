"""One folder is one release, however many workers notice it at once."""

import asyncio
import os

from sqlalchemy import select

from app.db import session as db_session
from app.integrations.filesystem import DirectoryObservation
from app.models.domain import (
    AccessMode,
    AssociationType,
    Event,
    MediaDirectory,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    Problem,
    ProblemStatus,
    ReleaseState,
    StorageRoot,
    Torrent,
)
from app.services.reconciliation import reconcile_movie_directory, refresh_movie_duplicate_problem
from tests.test_upgrade_candidates import client  # noqa: F401

NAME = "Backrooms.2026.Hybrid.2160p.UHD.Blu-ray.Remux.DV.HDR10P.HEVC.TrueHD.Atmos.7.1-CiNEPHiLES"
FOLDER = os.path.join(os.sep, "media", "movies", NAME)


def _observation(path: str) -> DirectoryObservation:
    return DirectoryObservation(path=path, name=NAME, media_files=(f"{NAME}.mkv",), has_dvd_structure=False,
                                has_bluray_structure=False)


async def _seed(db) -> tuple[StorageRoot, Movie]:
    from datetime import datetime, timezone

    root = StorageRoot(name="Movies", resolved_root_path=os.path.join(os.sep, "media", "movies"),
                       media_type=MediaType.MOVIES, access_mode=AccessMode.READ_WRITE,
                       last_scan_at=datetime.now(timezone.utc))
    movie = Movie(title="Backrooms", sort_title="backrooms", year=2026, tmdb_id=1_000_001, monitored=True)
    db.add_all([root, movie])
    await db.flush()
    return root, movie


async def _state(db, movie_id):
    releases = (await db.scalars(select(MovieRelease).where(MovieRelease.movie_id == movie_id))).all()
    current = [item for item in releases if item.release_state in {ReleaseState.CURRENT, ReleaseState.DUPLICATE}]
    directories = (await db.scalars(select(MediaDirectory).where(MediaDirectory.resolved_path.like(f"%{NAME}%")))).all()
    problem = await db.scalar(select(Problem).where(
        Problem.reason == "DUPLICATE_PHYSICAL_RELEASE", Problem.status == ProblemStatus.OPEN))
    return releases, current, directories, problem


def test_the_watcher_and_qbittorrent_attaching_one_folder_make_one_release(client):  # noqa: F811
    async def scenario():
        async with db_session.async_session_factory() as db:
            root, movie = await _seed(db)
            torrent = Torrent(info_hash="ab" * 20, name=NAME)
            incoming = MovieRelease(movie_id=movie.id, raw_release_name=NAME, release_state=ReleaseState.CURRENT,
                                    parse_snapshot={"incoming": True}, tracker_title=NAME)
            db.add_all([torrent, incoming])
            await db.flush()
            db.add(MovieReleaseTorrent(movie_release_id=incoming.id, torrent_id=torrent.id,
                                       association_type=AssociationType.INCOMING))
            await db.commit()
            ids = movie.id, root.id, torrent.id, incoming.id

        # The folder watcher gets there first and attaches the folder on its own.
        async with db_session.async_session_factory() as db:
            root = await db.get(StorageRoot, ids[1])
            movie = await db.get(Movie, ids[0])
            assert await reconcile_movie_directory(db, root, _observation(FOLDER), movie_hint=movie) == "matched"
            await db.commit()

        # qBittorrent's completion follows with a spelling of the same folder
        # its first lookup does not match, as a worker racing the watcher would.
        async with db_session.async_session_factory() as db:
            root = await db.get(StorageRoot, ids[1])
            movie = await db.get(Movie, ids[0])
            result = await reconcile_movie_directory(
                db, root, _observation(FOLDER + os.sep), torrent=await db.get(Torrent, ids[2]), movie_hint=movie,
                incoming_release=await db.get(MovieRelease, ids[3]),
            )
            assert result == "matched"
            await db.commit()

        async with db_session.async_session_factory() as db:
            releases, current, directories, problem = await _state(db, ids[0])
            assert len(current) == 1 and current[0].release_state == ReleaseState.CURRENT
            assert len(directories) == 1 and directories[0].movie_release_id == current[0].id
            assert problem is None
            link = await db.scalar(select(MovieReleaseTorrent).where(
                MovieReleaseTorrent.movie_release_id == current[0].id, MovieReleaseTorrent.torrent_id == ids[2]))
            assert link is not None and link.association_type == AssociationType.ATTACHED
            # The grab's details follow the torrent, and the provisional release is retired.
            assert current[0].tracker_title == NAME
            assert (await db.get(MovieRelease, ids[3])).release_state == ReleaseState.REMOVED

    asyncio.run(scenario())


def test_two_releases_already_recorded_for_one_folder_are_merged(client):  # noqa: F811
    async def scenario():
        async with db_session.async_session_factory() as db:
            root, movie = await _seed(db)
            untracked = MovieRelease(movie_id=movie.id, raw_release_name=NAME, release_state=ReleaseState.DUPLICATE,
                                     parse_snapshot={})
            tracked = MovieRelease(movie_id=movie.id, raw_release_name=NAME, release_state=ReleaseState.DUPLICATE,
                                   parse_snapshot={})
            torrent = Torrent(info_hash="cd" * 20, name=NAME)
            db.add_all([untracked, tracked, torrent])
            await db.flush()
            for release in (untracked, tracked):
                db.add(MediaDirectory(storage_root_id=root.id, resolved_path=FOLDER, reported_path=FOLDER, exists=True,
                                      movie_release_id=release.id))
            db.add(MovieReleaseTorrent(movie_release_id=tracked.id, torrent_id=torrent.id,
                                       association_type=AssociationType.ATTACHED))
            await db.commit()
            ids = movie.id, untracked.id, tracked.id

        async with db_session.async_session_factory() as db:
            await refresh_movie_duplicate_problem(db, ids[0])
            await db.commit()

        async with db_session.async_session_factory() as db:
            releases, current, directories, problem = await _state(db, ids[0])
            assert [item.id for item in current] == [ids[2]]
            assert current[0].release_state == ReleaseState.CURRENT
            assert (await db.get(MovieRelease, ids[1])).release_state == ReleaseState.REMOVED
            assert len(directories) == 1 and directories[0].movie_release_id == ids[2]
            assert problem is None
            assert await db.scalar(select(Event).where(Event.event_type == "release.merged")) is not None

    asyncio.run(scenario())
