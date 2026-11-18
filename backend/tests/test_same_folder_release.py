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
            # The database holds one record per exact path; a second spelling of
            # the folder is how two records of it can still arise.
            for release, path in ((untracked, FOLDER), (tracked, FOLDER + os.sep)):
                db.add(MediaDirectory(storage_root_id=root.id, resolved_path=path, reported_path=path, exists=True,
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


def test_a_worker_that_loses_the_race_for_a_new_folder_joins_the_winners_record(client, monkeypatch):  # noqa: F811
    """qBittorrent completion is mid-run when the folder watcher saves the same folder."""

    from app.services import reconciliation

    async def scenario():
        async with db_session.async_session_factory() as db:
            root, movie = await _seed(db)
            torrent = Torrent(info_hash="ef" * 20, name=NAME)
            db.add(torrent)
            await db.commit()
            ids = movie.id, root.id, torrent.id

        real_plex = reconciliation._plex_evidence_for_candidate
        watcher_ran = False

        async def plex_while_the_watcher_saves(db, title, year, candidate):
            nonlocal watcher_ran
            if not watcher_ran:
                watcher_ran = True
                async with db_session.async_session_factory() as other:
                    assert await reconcile_movie_directory(
                        other, await other.get(StorageRoot, ids[1]), _observation(FOLDER),
                        movie_hint=await other.get(Movie, ids[0])) == "matched"
                    await other.commit()
            return await real_plex(db, title, year, candidate)

        monkeypatch.setattr(reconciliation, "_plex_evidence_for_candidate", plex_while_the_watcher_saves)
        async with db_session.async_session_factory() as db:
            result = await reconcile_movie_directory(
                db, await db.get(StorageRoot, ids[1]), _observation(FOLDER),
                torrent=await db.get(Torrent, ids[2]), movie_hint=await db.get(Movie, ids[0]))
            assert result == "matched"
            await db.commit()
        assert watcher_ran

        async with db_session.async_session_factory() as db:
            releases, current, directories, problem = await _state(db, ids[0])
            assert len(current) == 1 and len(directories) == 1 and problem is None
            assert await db.scalar(select(MovieReleaseTorrent).where(
                MovieReleaseTorrent.movie_release_id == current[0].id, MovieReleaseTorrent.torrent_id == ids[2]))

    asyncio.run(scenario())


def test_a_new_folder_two_workers_stage_at_once_is_one_new_media_entry(client, monkeypatch):  # noqa: F811
    from app.models.domain import DiscoveryMode, MediaDiscovery
    from app.services import discoveries
    from app.services.tmdb import TMDBMovieIdentityResolution

    edge = "Edge.of.Tomorrow.2014.REPACK.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HEVC.HYBRID.REMUX-FraMeSToR"
    path = os.path.join(os.sep, "media", "movies", edge)
    observation = DirectoryObservation(path=path, name=edge, media_files=(f"{edge}.mkv",), has_dvd_structure=False,
                                       has_bluray_structure=False)

    async def scenario():
        async with db_session.async_session_factory() as db:
            root, _ = await _seed(db)
            root.discovery_mode = DiscoveryMode.REVIEW
            await db.commit()
            root_id = root.id

        other_ran = False

        async def lookup_while_the_other_worker_saves(db, title, year):
            nonlocal other_ran
            if not other_ran:
                other_ran = True
                async with db_session.async_session_factory() as other:
                    assert await reconcile_movie_directory(other, await other.get(StorageRoot, root_id), observation) == "review"
                    await other.commit()
            return TMDBMovieIdentityResolution(match=None, reason="no_match")

        monkeypatch.setattr(discoveries, "resolve_movie_identity_detailed", lookup_while_the_other_worker_saves)
        async with db_session.async_session_factory() as db:
            assert await reconcile_movie_directory(db, await db.get(StorageRoot, root_id), observation) == "review"
            await db.commit()
        assert other_ran

        async with db_session.async_session_factory() as db:
            entries = (await db.scalars(select(MediaDiscovery))).all()
            assert len(entries) == 1
            assert len((await db.scalars(select(MediaDirectory))).all()) == 1

    asyncio.run(scenario())
