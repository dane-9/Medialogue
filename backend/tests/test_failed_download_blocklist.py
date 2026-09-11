import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.domain import (
    DownloadClient,
    Event,
    IdentityState,
    InteractiveSearchResult,
    Job,
    MediaType,
    Movie,
    ReleaseBlocklist,
    Torrent,
    TorrentClientObservation,
)
from app.services.blocklist import handle_failed_download, matching_blocklist_entry
from app.api.blocklist import delete_blocklist_entry


class RemovalRecorder:
    def __init__(self):
        self.removals: list[tuple[str, bool]] = []

    async def remove_torrent(self, info_hash: str, *, delete_files: bool = False):
        self.removals.append((info_hash, delete_files))


class FailOnceRemovalRecorder(RemovalRecorder):
    async def remove_torrent(self, info_hash: str, *, delete_files: bool = False):
        self.removals.append((info_hash, delete_files))
        if len(self.removals) == 1:
            raise RuntimeError("temporary qBittorrent error")


def test_failed_exclusive_download_deletes_partial_files_and_creates_removable_policy(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'failure.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            movie = Movie(title="Failure Film", sort_title="Failure Film", year=2026, identity_state=IdentityState.MATCHED)
            client = DownloadClient(health="healthy")
            job = Job(job_type="interactive_search", summary={
                "target": {"media_type": "movies", "entity_type": "movie", "entity_id": None,
                           "profile_entity_id": None, "quality_profile_id": None, "title": "Failure Film",
                           "year": 2026, "tmdb_id": None, "overview": None, "poster_ref": None,
                           "season": None, "episode": None},
            })
            db.add_all([movie, client, job])
            await db.flush()
            job.summary = {**job.summary, "target": {**job.summary["target"], "entity_id": str(movie.id), "profile_entity_id": str(movie.id)}}
            result = InteractiveSearchResult(
                job_id=job.id, indexer_name="Tracker", media_type=MediaType.MOVIES,
                target_entity_type="movie", target_entity_id=movie.id, guid="failure-guid",
                title="Failure.Film.2026.1080p.WEB-DL-GRP", quality="1080p WEB-DL",
                submitted_info_hash="a" * 40, selected_download_client_id=client.id,
                selected_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            torrent = Torrent(info_hash="a" * 40, name=result.title, metadata_json={"completed": False})
            db.add_all([result, torrent])
            await db.flush()
            observation = TorrentClientObservation(
                torrent_id=torrent.id, download_client_id=client.id, state="error", progress=.25,
                resolved_save_path="/downloads/failure-film", is_present=True,
            )
            db.add(observation)
            await db.flush()
            adapter = RemovalRecorder()

            await handle_failed_download(
                db, torrent=torrent, observation=observation, client_name="Movies", adapter=adapter
            )
            await db.flush()
            blocked = await matching_blocklist_entry(
                db, media_type=MediaType.MOVIES, target_entity_type="movie", target_entity_id=movie.id,
                source_title=result.title, indexer_name="Tracker", info_hash=torrent.info_hash,
            )
            event_count = int(await db.scalar(select(func.count()).select_from(Event).where(Event.event_type == "download.failed")) or 0)
            assert adapter.removals == [(torrent.info_hash, True)]
            assert observation.is_present is False
            assert blocked is not None
            assert event_count == 1
            deleted = await delete_blocklist_entry(blocked.id, _=object(), db=db)
            assert deleted.deleted == 1
            assert int(await db.scalar(select(func.count()).select_from(ReleaseBlocklist)) or 0) == 0
            assert int(await db.scalar(select(func.count()).select_from(Event).where(Event.event_type == "download.failed")) or 0) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_failed_download_preserves_files_when_another_torrent_uses_same_path(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            movie = Movie(title="Shared Film", sort_title="Shared Film", identity_state=IdentityState.MATCHED)
            client = DownloadClient(health="healthy")
            job = Job(job_type="interactive_search", summary={"target": {
                "media_type": "movies", "entity_type": "movie", "entity_id": None,
                "profile_entity_id": None, "quality_profile_id": None, "title": "Shared Film",
                "year": None, "tmdb_id": None, "overview": None, "poster_ref": None,
                "season": None, "episode": None,
            }})
            db.add_all([movie, client, job])
            await db.flush()
            job.summary = {**job.summary, "target": {**job.summary["target"], "entity_id": str(movie.id), "profile_entity_id": str(movie.id)}}
            failed = Torrent(info_hash="b" * 40, name="Shared.Film.failed", metadata_json={"completed": False})
            survivor = Torrent(info_hash="c" * 40, name="Shared.Film.cross-seed", metadata_json={"completed": False})
            db.add_all([failed, survivor])
            await db.flush()
            result = InteractiveSearchResult(
                job_id=job.id, indexer_name="Tracker A", media_type=MediaType.MOVIES,
                target_entity_type="movie", target_entity_id=movie.id, guid="shared-guid",
                title=failed.name, submitted_info_hash=failed.info_hash,
                selected_download_client_id=client.id, selected_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            failed_observation = TorrentClientObservation(
                torrent_id=failed.id, download_client_id=client.id, state="missingFiles", progress=.4,
                resolved_save_path="/downloads/shared", is_present=True,
            )
            survivor_observation = TorrentClientObservation(
                torrent_id=survivor.id, download_client_id=client.id, state="downloading", progress=.6,
                resolved_save_path="/downloads/shared/", is_present=True,
            )
            db.add_all([result, failed_observation, survivor_observation])
            await db.flush()
            adapter = RemovalRecorder()

            await handle_failed_download(
                db, torrent=failed, observation=failed_observation, client_name="Movies", adapter=adapter
            )
            assert adapter.removals == [(failed.info_hash, False)]
            assert failed_observation.is_present is False
            assert survivor_observation.is_present is True
            assert int(await db.scalar(select(func.count()).select_from(ReleaseBlocklist)) or 0) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_failed_cleanup_retries_without_duplicate_failure_records(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            movie = Movie(title="Retry Film", sort_title="Retry Film", identity_state=IdentityState.MATCHED)
            client = DownloadClient(health="healthy")
            job = Job(job_type="interactive_search", summary={})
            db.add_all([movie, client, job])
            await db.flush()
            result = InteractiveSearchResult(
                job_id=job.id, indexer_name="Tracker", media_type=MediaType.MOVIES,
                target_entity_type="movie", target_entity_id=movie.id, guid="retry-guid",
                title="Retry.Film.2026.1080p", submitted_info_hash="d" * 40,
                selected_download_client_id=client.id, selected_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            torrent = Torrent(info_hash="d" * 40, name=result.title, metadata_json={"completed": False})
            db.add_all([result, torrent])
            await db.flush()
            observation = TorrentClientObservation(
                torrent_id=torrent.id, download_client_id=client.id, state="error", progress=.1,
                resolved_save_path="/downloads/retry", is_present=True,
            )
            db.add(observation)
            await db.flush()
            adapter = FailOnceRemovalRecorder()

            await handle_failed_download(
                db, torrent=torrent, observation=observation, client_name="Movies", adapter=adapter
            )
            assert observation.is_present is True
            await handle_failed_download(
                db, torrent=torrent, observation=observation, client_name="Movies", adapter=adapter
            )

            assert adapter.removals == [(torrent.info_hash, True), (torrent.info_hash, True)]
            assert observation.is_present is False
            assert int(await db.scalar(select(func.count()).select_from(ReleaseBlocklist)) or 0) == 1
            assert int(await db.scalar(select(func.count()).select_from(Event).where(Event.event_type == "download.failed")) or 0) == 1
        await engine.dispose()

    asyncio.run(scenario())
