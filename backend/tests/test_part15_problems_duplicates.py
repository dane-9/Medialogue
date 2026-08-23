import asyncio
import os
import shutil
import time
import uuid
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import duplicates as duplicates_api
from app.api import problems as problems_api
from app.core.config import Settings
from app.core.integration_config import DownloadClientConfig, get_integration_config_store
from app.db import session as db_session
from app.db.base import Base
from app.integrations.tmdb import TMDBMovieMatch
from app.main import create_app
from app.models.domain import (
    AccessMode,
    AssociationType,
    DownloadClient,
    Episode,
    EpisodeMediaMap,
    IdentityState,
    MediaDirectory,
    MediaFile,
    MediaRole,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    PresenceState,
    Problem,
    ProblemStatus,
    ReleaseState,
    Season,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    Severity,
    SourceType,
    StorageRoot,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
)
from app.services.reconciliation import mark_absent_known_directories, reconcile_torrent_disagreements


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(database_url=database_url, bootstrap_admin=True, config_dir=f"{db_path}.config", secret_key="part15-secret-key-123456789")
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
    raise AssertionError(f"Job did not finish: {payload}")


def commit_duplicate(client: TestClient, movie_id: UUID, headers: dict[str, str], token: str) -> dict:
    response = client.post(
        f"/api/v1/movies/{movie_id}/duplicates/resolve",
        headers=headers,
        json={"confirmation_token": token},
    )
    assert response.status_code == 202, response.text
    return wait_job(client, response.json()["job_id"])


def db_run(fn):
    async def run():
        async with db_session.async_session_factory() as db:
            value = await fn(db)
            await db.commit()
            return value

    return asyncio.run(run())


async def seed_movie_duplicate(db, root_path: Path, *, access_mode=AccessMode.READ_WRITE):
    storage = StorageRoot(
        name=f"Movies-{uuid.uuid4().hex}",
        resolved_root_path=str(root_path),
        media_type=MediaType.MOVIES,
        access_mode=access_mode,
        enabled=True,
    )
    movie = Movie(
        title="Inception",
        sort_title="inception",
        year=2010,
        tmdb_id=27205,
        identity_state=IdentityState.MATCHED,
    )
    db.add_all([storage, movie])
    await db.flush()
    releases = []
    for suffix in ("A", "B"):
        release_name = f"Inception 2010 1080p BluRay REMUX AVC DTS-HD MA 5.1-{suffix}"
        release_dir = root_path / release_name
        release_dir.mkdir(parents=True, exist_ok=True)
        (release_dir / f"{release_name}.mkv").write_bytes(f"movie-{suffix}".encode())
        (release_dir / "English.srt").write_text("subtitle", encoding="utf-8")
        (release_dir / "poster.jpg").write_bytes(b"jpg")
        release = MovieRelease(
            movie_id=movie.id,
            raw_release_name=release_name,
            effective_edition=None,
            release_state=ReleaseState.DUPLICATE,
        )
        db.add(release)
        await db.flush()
        directory = MediaDirectory(
            storage_root_id=storage.id,
            movie_release_id=release.id,
            reported_path=str(release_dir),
            resolved_path=str(release_dir),
            exists=True,
            source_type=SourceType.FILESYSTEM,
        )
        db.add(directory)
        await db.flush()
        db.add(
            MediaFile(
                media_directory_id=directory.id,
                relative_path=f"{release_name}.mkv",
                filename=f"{release_name}.mkv",
                media_role=MediaRole.MOVIE_VIDEO,
                exists=True,
            )
        )
        releases.append(release)
    problem = Problem(
        reason="DUPLICATE_PHYSICAL_RELEASE",
        status=ProblemStatus.OPEN,
        severity=Severity.WARNING,
        entity_type="movie",
        entity_id=movie.id,
        message="Duplicate physical release detected.",
        details={"release_ids": [str(item.id) for item in releases]},
    )
    db.add(problem)
    await db.flush()
    return movie.id, releases[0].id, releases[1].id, problem.id


def test_external_duplicate_disappearance_resolves_problem_on_reconciliation(client: TestClient) -> None:
    login(client)
    root_path = Path.cwd() / f"part15-duplicate-disappears-{uuid.uuid4().hex}"
    root_path.mkdir()
    try:
        movie_id, first_id, second_id, problem_id = db_run(lambda db: seed_movie_duplicate(db, root_path))

        async def reconcile_one_missing(db):
            root = await db.scalar(select(StorageRoot).where(StorageRoot.resolved_root_path == str(root_path)))
            first_path = await db.scalar(
                select(MediaDirectory.resolved_path).where(MediaDirectory.movie_release_id == first_id)
            )
            assert root is not None and first_path is not None
            # Missing grace records the first miss, then commits it on the
            # second successful root scan. The duplicate Problem should then
            # be recomputed from current physical evidence and close itself.
            await mark_absent_known_directories(db, root, {first_path}, grace_checks=1)
            await mark_absent_known_directories(db, root, {first_path}, grace_checks=1)
            problem = await db.get(Problem, problem_id)
            first = await db.get(MovieRelease, first_id)
            second = await db.get(MovieRelease, second_id)
            return problem.status, first.release_state, second.release_state

        status, first_state, second_state = db_run(reconcile_one_missing)
        assert status == ProblemStatus.RESOLVED
        assert first_state == ReleaseState.CURRENT
        assert second_state == ReleaseState.MISSING
    finally:
        shutil.rmtree(root_path, ignore_errors=True)


def test_duplicate_preview_lists_entire_folder_and_commit_deletes_only_loser(client: TestClient) -> None:
    headers = login(client)
    root = Path.cwd() / f"part15-duplicate-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        movie_id, winner_id, loser_id, problem_id = db_run(lambda db: seed_movie_duplicate(db, root))

        preview = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={
                "winner_release_id": str(winner_id),
                "losing_release_ids": [str(loser_id)],
                "delete_media": True,
                "remove_torrents": False,
            },
        )
        assert preview.status_code == 200, preview.text
        payload = preview.json()
        files = {item["relative_path"] for item in payload["losers"][0]["directories"][0]["files"]}
        assert any(item.endswith(".mkv") for item in files)
        assert "English.srt" in files
        assert "poster.jpg" in files
        assert payload["torrent_backups_will_be_kept"] is True

        loser_path = Path(payload["losers"][0]["directories"][0]["path"])
        stale_file = loser_path / "new-after-preview.txt"
        stale_file.write_text("changed", encoding="utf-8")
        stale = commit_duplicate(client, movie_id, headers, payload["confirmation_token"])
        assert stale["status"] == "failed", stale
        assert stale["error"]["code"] == "DELETE_PREVIEW_STALE"
        stale_file.unlink()

        preview = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={
                "winner_release_id": str(winner_id),
                "losing_release_ids": [str(loser_id)],
                "delete_media": True,
                "remove_torrents": False,
            },
        ).json()
        committed = commit_duplicate(client, movie_id, headers, preview["confirmation_token"])
        assert committed["status"] == "completed", committed
        assert committed["summary"]["duplicate_resolved"] is True
        assert committed["summary"]["problem_status"] == "resolved"
        assert not loser_path.exists()

        async def states(db):
            winner = await db.get(MovieRelease, winner_id)
            loser = await db.get(MovieRelease, loser_id)
            problem = await db.get(Problem, problem_id)
            return winner.release_state, loser.release_state, problem.status

        winner_state, loser_state, status = db_run(states)
        assert winner_state == ReleaseState.CURRENT
        assert loser_state == ReleaseState.REMOVED
        assert status == ProblemStatus.RESOLVED
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_selecting_winner_without_deleting_keeps_duplicate_problem_open(client: TestClient) -> None:
    headers = login(client)
    root = Path.cwd() / f"part15-preferred-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        movie_id, winner_id, loser_id, problem_id = db_run(lambda db: seed_movie_duplicate(db, root))
        preview = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={"winner_release_id": str(winner_id), "losing_release_ids": [str(loser_id)]},
        ).json()
        committed = commit_duplicate(client, movie_id, headers, preview["confirmation_token"])
        assert committed["status"] == "completed", committed
        assert committed["summary"]["duplicate_resolved"] is False
        assert committed["summary"]["problem_status"] == "open"
        assert Path(preview["losers"][0]["directories"][0]["path"]).exists()
        problem = db_run(lambda db: db.get(Problem, problem_id))
        assert problem.status == ProblemStatus.OPEN
        assert problem.details["preferred_release_id"] == str(winner_id)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_read_only_root_blocks_duplicate_media_delete(client: TestClient) -> None:
    headers = login(client)
    root = Path.cwd() / f"part15-readonly-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        movie_id, winner_id, loser_id, _ = db_run(lambda db: seed_movie_duplicate(db, root, access_mode=AccessMode.READ_ONLY))
        preview = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={
                "winner_release_id": str(winner_id),
                "losing_release_ids": [str(loser_id)],
                "delete_media": True,
            },
        )
        assert preview.status_code == 409
        assert preview.json()["error"]["code"] == "ROOT_READ_ONLY"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_problem_actions_are_explicit_and_recheck_does_not_fake_resolution(client: TestClient) -> None:
    headers = login(client)

    async def seed(db):
        problem = Problem(
            reason="PATH_MAPPING_FAILED",
            entity_type="torrent",
            entity_id=uuid.uuid4(),
            message="Mapping failed",
        )
        db.add(problem)
        await db.flush()
        return problem.id

    problem_id = db_run(seed)
    listed = client.get("/api/v1/problems?status=open").json()["items"]
    problem = next(item for item in listed if item["id"] == str(problem_id))
    assert problem["available_actions"] == ["dismiss", "recheck"]

    result = client.post(
        f"/api/v1/problems/{problem_id}/resolve",
        headers=headers,
        json={"action": "recheck", "payload": {}},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "open"
    parent_job_id = result.json()["resolution"]["recheck_parent_job_id"]
    parent_job = wait_job(client, parent_job_id)
    assert parent_job["status"] == "completed", parent_job
    assert parent_job["summary"]["problem_id"] == str(problem_id)

    invalid = client.post(
        f"/api/v1/problems/{problem_id}/resolve",
        headers=headers,
        json={"action": "confirm_movie_match", "payload": {"tmdb_id": 27205}},
    )
    assert invalid.status_code == 409


def test_episode_duplicate_requires_manual_file_removal_then_recheck(client: TestClient) -> None:
    headers = login(client)
    root = Path.cwd() / f"part15-episode-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        async def seed(db):
            storage = StorageRoot(name=f"Shows-{uuid.uuid4().hex}", resolved_root_path=str(root), media_type=MediaType.SHOWS)
            show = __import__("app.models.domain", fromlist=["Show"]).Show(title="Dollface", year=2019, tmdb_id=194764)
            db.add_all([storage, show]); await db.flush()
            season = Season(show_id=show.id, season_number=1); db.add(season); await db.flush()
            episode = Episode(show_id=show.id, season_id=season.id, season_number=1, episode_number=1, presence_state=PresenceState.PRESENT)
            db.add(episode); await db.flush()
            directory = MediaDirectory(storage_root_id=storage.id, resolved_path=str(root), reported_path=str(root), exists=True)
            db.add(directory); await db.flush()
            files=[]
            for idx in (1,2):
                path=root/f"Dollface S01E01 copy{idx}.mkv"; path.write_bytes(str(idx).encode())
                media=MediaFile(media_directory_id=directory.id, relative_path=path.name, filename=path.name, media_role=MediaRole.EPISODE_VIDEO, exists=True)
                db.add(media); await db.flush(); db.add(EpisodeMediaMap(episode_id=episode.id, media_file_id=media.id)); files.append(media)
            problem=Problem(reason="DUPLICATE_EPISODE_RELEASE", entity_type="episode", entity_id=episode.id, message="duplicate", details={"media_file_ids":[str(item.id) for item in files]})
            db.add(problem); await db.flush(); return episode.id, files[0].id, files[1].id, problem.id
        episode_id, winner_id, loser_id, problem_id = db_run(seed)
        listed = client.get(f"/api/v1/problems/{problem_id}")
        assert listed.status_code == 200, listed.text
        assert listed.json()["available_actions"] == ["dismiss", "recheck"]

        response = client.post(
            f"/api/v1/problems/{problem_id}/resolve",
            headers=headers,
            json={"action":"choose_episode_winner","payload":{"winner_media_file_id":str(winner_id)}},
        )
        assert response.status_code == 422, response.text

        async def loser_path(db):
            media = await db.get(MediaFile, loser_id)
            assert media is not None
            return Path(root) / media.relative_path

        db_run(loser_path).unlink()
        recheck = client.post(
            f"/api/v1/problems/{problem_id}/resolve",
            headers=headers,
            json={"action": "recheck", "payload": {}},
        )
        assert recheck.status_code == 200, recheck.text
        parent_job = wait_job(client, recheck.json()["resolution"]["recheck_parent_job_id"])
        assert parent_job["status"] == "completed", parent_job

        async def mapping_state(db):
            mappings = (
                await db.scalars(
                    select(EpisodeMediaMap).where(EpisodeMediaMap.episode_id == episode_id)
                )
            ).all()
            problem = await db.get(Problem, problem_id)
            return {str(item.media_file_id): item.manual_override for item in mappings}, problem.status

        overrides, status = db_run(mapping_state)
        assert overrides == {str(winner_id): False, str(loser_id): False}
        assert status == ProblemStatus.RESOLVED
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_qbit_only_attached_releases_become_historical_without_path_problems(client: TestClient) -> None:
    async def exercise(db):
        movie = Movie(title="Gone Movie", sort_title="gone movie", year=2020, identity_state=IdentityState.MATCHED)
        show = Show(title="Gone Show", year=2020)
        movie_torrent = Torrent(info_hash="a" * 40, name="Gone.Movie.2020")
        show_torrent = Torrent(info_hash="b" * 40, name="Gone.Show.S01")
        db.add_all([movie, show, movie_torrent, show_torrent])
        await db.flush()

        movie_release = MovieRelease(
            movie_id=movie.id,
            raw_release_name=movie_torrent.name,
            release_state=ReleaseState.MISSING,
        )
        season = Season(show_id=show.id, season_number=1)
        db.add_all([movie_release, season])
        await db.flush()
        show_release = ShowRelease(
            show_id=show.id,
            season_id=season.id,
            raw_release_name=show_torrent.name,
            release_state=ReleaseState.MISSING,
        )
        db.add(show_release)
        await db.flush()

        movie_link = MovieReleaseTorrent(
            movie_release_id=movie_release.id,
            torrent_id=movie_torrent.id,
            association_type=AssociationType.ATTACHED,
        )
        show_link = ShowReleaseTorrent(
            show_release_id=show_release.id,
            torrent_id=show_torrent.id,
            association_type=AssociationType.ATTACHED,
        )
        db.add_all([
            movie_link,
            show_link,
            Problem(
                reason="TORRENT_PATH_NOT_FOUND",
                entity_type="movie_release",
                entity_id=movie_release.id,
                message="stale movie warning",
            ),
            Problem(
                reason="TORRENT_PATH_NOT_FOUND",
                entity_type="show_release",
                entity_id=show_release.id,
                message="stale show warning",
            ),
        ])
        await db.flush()

        await reconcile_torrent_disagreements(db, movie_torrent, qbit_present=True)
        await reconcile_torrent_disagreements(db, show_torrent, qbit_present=True)
        await db.flush()

        open_path_problems = int(
            await db.scalar(
                select(func.count())
                .select_from(Problem)
                .where(Problem.reason == "TORRENT_PATH_NOT_FOUND", Problem.status == ProblemStatus.OPEN)
            )
            or 0
        )
        return movie_link.association_type, show_link.association_type, open_path_problems

    movie_state, show_state, open_count = db_run(exercise)
    assert movie_state is AssociationType.HISTORICAL
    assert show_state is AssociationType.HISTORICAL
    assert open_count == 0


class ManualTMDBClient:
    def __init__(self, api_key: str): self.api_key = api_key
    async def get_movie(self, tmdb_id: int): return TMDBMovieMatch(tmdb_id, "Inception", "Inception", 2010, "Dreams", None)
    async def close(self): return None


def test_low_confidence_directory_can_be_manually_matched_without_renaming(client: TestClient) -> None:
    headers = login(client)
    root = Path.cwd() / f"part15-manual-{uuid.uuid4().hex}"
    directory_path = root / "Odd Folder"
    directory_path.mkdir(parents=True)
    media_path = directory_path / "video.mkv"
    media_path.write_bytes(b"unchanged")
    try:
        configured = client.put("/api/v1/integrations/tmdb", headers=headers, json={"api_key":"test-key","enabled":True})
        assert configured.status_code == 200, configured.text
        async def seed(db):
            storage=StorageRoot(name=f"Movies-{uuid.uuid4().hex}", resolved_root_path=str(root), media_type=MediaType.MOVIES)
            db.add(storage); await db.flush()
            directory=MediaDirectory(storage_root_id=storage.id, reported_path=str(directory_path), resolved_path=str(directory_path), exists=True)
            db.add(directory); await db.flush()
            problem=Problem(reason="LOW_CONFIDENCE_MATCH", entity_type="media_directory", entity_id=directory.id, message="low")
            db.add(problem); await db.flush(); return problem.id
        problem_id=db_run(seed)
        client.app.dependency_overrides[problems_api.get_tmdb_client_factory] = lambda: (lambda key: ManualTMDBClient(key))
        response=client.post(f"/api/v1/problems/{problem_id}/resolve", headers=headers, json={"action":"confirm_movie_match","payload":{"tmdb_id":27205}})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "resolved"
        assert directory_path.exists() and media_path.read_bytes() == b"unchanged"
        movies=client.get('/api/v1/movies').json()['items']
        assert len(movies)==1 and movies[0]['tmdb_id']==27205
        assert movies[0]['identity_state']=='manual'
    finally:
        client.app.dependency_overrides.clear()
        shutil.rmtree(root, ignore_errors=True)


async def seed_duplicate_torrent(
    db,
    loser_release_id: UUID,
    *,
    archived: bool,
) -> tuple[UUID, UUID, str]:
    client_config = get_integration_config_store().save_download_client(
        DownloadClientConfig(
            id=uuid.uuid4(),
            name="qbit-movies",
            url="http://qbit.test",
            username="user",
            password="pass",
            scope=MediaType.MOVIES.value,
            enabled=True,
        )
    )
    client = DownloadClient(id=client_config.id)
    info_hash = uuid.uuid4().hex
    torrent = Torrent(
        info_hash=info_hash,
        name="Inception duplicate torrent",
        archive_state=TorrentArchiveState.ARCHIVED if archived else TorrentArchiveState.NOT_ARCHIVED,
        archive_path=f"/torrent-archive/{info_hash}.torrent" if archived else None,
        manifest_path=f"/torrent-archive/{info_hash}.json" if archived else None,
        manifest_schema_version=1 if archived else None,
    )
    db.add_all([client, torrent])
    await db.flush()
    db.add(MovieReleaseTorrent(movie_release_id=loser_release_id, torrent_id=torrent.id))
    observation = TorrentClientObservation(
        torrent_id=torrent.id,
        download_client_id=client.id,
        is_present=True,
        state="pausedUP",
        progress=1,
    )
    db.add(observation)
    await db.flush()
    return torrent.id, observation.id, info_hash


class DuplicateQbitAdapter:
    def __init__(self, calls: list[tuple[str, bool]]):
        self.calls = calls

    async def remove_torrent(self, info_hash: str, *, delete_files: bool = False):
        self.calls.append((info_hash, delete_files))

    async def close(self):
        return None


def test_duplicate_torrent_removal_requires_archive_and_never_deletes_qbit_data(client: TestClient) -> None:
    headers = login(client)
    root = Path.cwd() / f"part15-qbit-{uuid.uuid4().hex}"
    root.mkdir()
    calls: list[tuple[str, bool]] = []
    try:
        movie_id, winner_id, loser_id, problem_id = db_run(lambda db: seed_movie_duplicate(db, root))
        torrent_id, observation_id, info_hash = db_run(
            lambda db: seed_duplicate_torrent(db, loser_id, archived=False)
        )

        blocked = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={
                "winner_release_id": str(winner_id),
                "losing_release_ids": [str(loser_id)],
                "delete_media": False,
                "remove_torrents": True,
            },
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "TORRENT_ARCHIVE_REQUIRED"

        async def mark_archived(db):
            torrent = await db.get(Torrent, torrent_id)
            torrent.archive_state = TorrentArchiveState.ARCHIVED
            torrent.archive_path = f"/torrent-archive/{info_hash}.torrent"
            torrent.manifest_path = f"/torrent-archive/{info_hash}.json"
            torrent.manifest_schema_version = 1

        db_run(mark_archived)
        client.app.dependency_overrides[duplicates_api.get_qbit_client_factory] = lambda: (
            lambda *_args, **_kwargs: DuplicateQbitAdapter(calls)
        )

        preview = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={
                "winner_release_id": str(winner_id),
                "losing_release_ids": [str(loser_id)],
                "delete_media": False,
                "remove_torrents": True,
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["torrent_backups_will_be_kept"] is True
        committed = commit_duplicate(client, movie_id, headers, preview.json()["confirmation_token"])
        assert committed["status"] == "completed", committed
        assert calls == [(info_hash, False)]
        # The media still exists, so selecting a winner does not pretend that
        # the physical duplicate is gone merely because qBittorrent was cleaned up.
        assert committed["summary"]["duplicate_resolved"] is False
        assert committed["summary"]["problem_status"] == "open"

        async def state(db):
            torrent = await db.get(Torrent, torrent_id)
            observation = await db.get(TorrentClientObservation, observation_id)
            problem = await db.get(Problem, problem_id)
            return torrent.archive_state, torrent.archive_path, torrent.manifest_path, observation.is_present, problem.status

        archive_state, archive_path, manifest_path, is_present, problem_status = db_run(state)
        assert archive_state == TorrentArchiveState.ARCHIVED
        assert archive_path == f"/torrent-archive/{info_hash}.torrent"
        assert manifest_path == f"/torrent-archive/{info_hash}.json"
        assert is_present is False
        assert problem_status == ProblemStatus.OPEN
    finally:
        client.app.dependency_overrides.pop(duplicates_api.get_qbit_client_factory, None)
        shutil.rmtree(root, ignore_errors=True)



def test_existing_show_identity_can_be_manually_confirmed(client: TestClient) -> None:
    headers = login(client)

    async def seed(db):
        show = Show(title="Old Show Match", year=2020, tmdb_id=999001, identity_state=IdentityState.MATCHED)
        db.add(show)
        await db.flush()
        problem = Problem(
            reason="PLEX_IDENTITY_MISMATCH",
            entity_type="show",
            entity_id=show.id,
            message="Plex disagrees with the current Show identity.",
        )
        db.add(problem)
        await db.flush()
        return show.id, problem.id

    show_id, problem_id = db_run(seed)
    response = client.post(
        f"/api/v1/problems/{problem_id}/resolve",
        headers=headers,
        json={"action": "confirm_show_match", "payload": {"tmdb_id": 999002}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "resolved"

    async def state(db):
        show = await db.get(Show, show_id)
        problem = await db.get(Problem, problem_id)
        return show.tmdb_id, show.identity_state, show.manual_identity_override, problem.status

    tmdb_id, identity_state, manual_override, status = db_run(state)
    assert tmdb_id == 999002
    assert identity_state == IdentityState.MANUAL
    assert manual_override is True
    assert status == ProblemStatus.RESOLVED


def test_remote_path_mapping_can_be_managed_through_api(client: TestClient) -> None:
    headers = login(client)

    async def seed(db):
        root = StorageRoot(
            name="Movies mapping root",
            resolved_root_path="/media/movies",
            media_type=MediaType.MOVIES,
            access_mode=AccessMode.READ_ONLY,
            enabled=True,
        )
        qbit_config = get_integration_config_store().save_download_client(
            DownloadClientConfig(
                id=uuid.uuid4(),
                name="qbit-movies",
                url="http://qbit.test",
                username="",
                password="pass",
                scope=MediaType.MOVIES.value,
                enabled=True,
            )
        )
        qbit = DownloadClient(id=qbit_config.id)
        db.add_all([root, qbit])
        await db.flush()
        return root.id, qbit.id

    root_id, client_id = db_run(seed)
    created = client.post(
        "/api/v1/remote-path-mappings",
        headers=headers,
        json={
            "name": "qBit Movies",
            "integration_type": "qbittorrent",
            "integration_id": str(client_id),
            "remote_prefix": "/downloads/movies",
            "local_prefix": "/media/movies",
            "storage_root_id": str(root_id),
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    mapping_id = created.json()["id"]

    listed = client.get("/api/v1/remote-path-mappings")
    assert listed.status_code == 200, listed.text
    mapping = next(item for item in listed.json()["items"] if item["id"] == mapping_id)
    assert mapping["remote_prefix"] == "/downloads/movies"
    assert mapping["local_prefix"] == str(Path("/media/movies").resolve())
    assert mapping["integration_id"] == str(client_id)

    updated = client.patch(
        f"/api/v1/remote-path-mappings/{mapping_id}",
        headers=headers,
        json={
            "name": "qBit Movies updated",
            "integration_type": "qbittorrent",
            "integration_id": None,
            "remote_prefix": "/completed/movies",
            "storage_root_id": None,
            "enabled": False,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "qBit Movies updated"
    assert updated.json()["remote_prefix"] == "/completed/movies"
    assert updated.json()["integration_id"] is None
    assert updated.json()["storage_root_id"] is None
    assert updated.json()["enabled"] is False

    removed = client.delete(f"/api/v1/remote-path-mappings/{mapping_id}", headers=headers)
    assert removed.status_code == 200, removed.text
    assert all(item["id"] != mapping_id for item in client.get("/api/v1/remote-path-mappings").json()["items"])


def test_root_scoped_remote_mapping_cannot_translate_outside_selected_root(client: TestClient) -> None:
    headers = login(client)

    async def seed(db):
        root = StorageRoot(
            name="Cartoons mapping root",
            resolved_root_path="/media/movies/Cartoons",
            media_type=MediaType.MOVIES,
            access_mode=AccessMode.READ_ONLY,
            enabled=True,
        )
        qbit_config = get_integration_config_store().save_download_client(
            DownloadClientConfig(
                id=uuid.uuid4(),
                name="qbit-cartoons",
                url="http://qbit.test",
                username="",
                password="pass",
                scope=MediaType.MOVIES.value,
                enabled=True,
            )
        )
        qbit = DownloadClient(id=qbit_config.id)
        db.add_all([root, qbit])
        await db.flush()
        return root.id, qbit.id

    root_id, client_id = db_run(seed)
    response = client.post(
        "/api/v1/remote-path-mappings",
        headers=headers,
        json={
            "name": "Too broad",
            "integration_type": "qbittorrent",
            "integration_id": str(client_id),
            "remote_prefix": "/Movies",
            "local_prefix": "/media/movies",
            "storage_root_id": str(root_id),
            "enabled": True,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "PATH_MAPPING_OUTSIDE_ROOT"


def test_duplicate_commit_refuses_to_delete_loser_if_winner_disappears(client: TestClient) -> None:
    headers = login(client)
    root = Path.cwd() / f"part15-winner-gone-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        movie_id, winner_id, loser_id, _ = db_run(lambda db: seed_movie_duplicate(db, root))
        preview = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={
                "winner_release_id": str(winner_id),
                "losing_release_ids": [str(loser_id)],
                "delete_media": True,
            },
        )
        assert preview.status_code == 200, preview.text
        payload = preview.json()
        winner_path = Path(payload["winner"]["directories"][0]["path"])
        loser_path = Path(payload["losers"][0]["directories"][0]["path"])
        shutil.rmtree(winner_path)

        committed = commit_duplicate(client, movie_id, headers, payload["confirmation_token"])
        assert committed["status"] == "failed", committed
        assert committed["error"]["code"] == "DUPLICATE_WINNER_NOT_PRESENT"
        assert loser_path.exists(), "the losing copy must not be deleted when the selected winner vanished"
    finally:
        shutil.rmtree(root, ignore_errors=True)


class FailingDuplicateQbitAdapter:
    async def remove_torrent(self, info_hash: str, *, delete_files: bool = False):
        raise RuntimeError("qBittorrent unavailable")

    async def close(self):
        return None


def test_qbit_failure_skips_requested_media_deletion(client: TestClient) -> None:
    headers = login(client)
    root = Path.cwd() / f"part15-qbit-failure-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        movie_id, winner_id, loser_id, problem_id = db_run(lambda db: seed_movie_duplicate(db, root))
        db_run(lambda db: seed_duplicate_torrent(db, loser_id, archived=True))
        client.app.dependency_overrides[duplicates_api.get_qbit_client_factory] = lambda: (
            lambda *_args, **_kwargs: FailingDuplicateQbitAdapter()
        )

        preview = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={
                "winner_release_id": str(winner_id),
                "losing_release_ids": [str(loser_id)],
                "delete_media": True,
                "remove_torrents": True,
            },
        )
        assert preview.status_code == 200, preview.text
        loser_path = Path(preview.json()["losers"][0]["directories"][0]["path"])
        committed = commit_duplicate(client, movie_id, headers, preview.json()["confirmation_token"])
        assert committed["status"] == "completed", committed
        payload = committed["summary"]
        assert payload["duplicate_resolved"] is False
        assert payload["deleted_directories"] == []
        assert any("Media deletion was skipped" in warning for warning in payload["warnings"])
        assert loser_path.exists()

        async def state(db):
            problem = await db.get(Problem, problem_id)
            qbit_failure = await db.scalar(
                select(Problem).where(Problem.reason == "QBIT_REMOVE_FAILED", Problem.status == ProblemStatus.OPEN)
            )
            loser = await db.get(MovieRelease, loser_id)
            return problem.status, qbit_failure is not None, loser.release_state

        status, qbit_problem_exists, loser_state = db_run(state)
        assert status == ProblemStatus.OPEN
        assert qbit_problem_exists is True
        assert loser_state == ReleaseState.DUPLICATE
    finally:
        client.app.dependency_overrides.pop(duplicates_api.get_qbit_client_factory, None)
        shutil.rmtree(root, ignore_errors=True)


def test_duplicate_confirmation_rejects_tampering(client: TestClient) -> None:
    headers = login(client)
    root = Path.cwd() / f"part19-token-tamper-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        movie_id, winner_id, loser_id, _ = db_run(lambda db: seed_movie_duplicate(db, root))
        preview = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={
                "winner_release_id": str(winner_id),
                "losing_release_ids": [str(loser_id)],
                "delete_media": True,
            },
        ).json()
        token = preview["confirmation_token"]
        replacement = "A" if token[-1] != "A" else "B"
        tampered = f"{token[:-1]}{replacement}"
        committed = commit_duplicate(client, movie_id, headers, tampered)
        assert committed["status"] == "failed", committed
        assert committed["error"]["code"] == "INVALID_CONFIRMATION"
        assert Path(preview["losers"][0]["directories"][0]["path"]).exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_duplicate_confirmation_expires_before_any_destructive_action(client: TestClient, monkeypatch) -> None:
    import app.services.problem_resolution as problem_resolution_service

    headers = login(client)
    root = Path.cwd() / f"part19-token-expiry-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        movie_id, winner_id, loser_id, _ = db_run(lambda db: seed_movie_duplicate(db, root))
        preview = client.post(
            f"/api/v1/movies/{movie_id}/duplicates/resolve-preview",
            headers=headers,
            json={
                "winner_release_id": str(winner_id),
                "losing_release_ids": [str(loser_id)],
                "delete_media": True,
            },
        ).json()
        loser_path = Path(preview["losers"][0]["directories"][0]["path"])
        future = problem_resolution_service.time.time() + problem_resolution_service.CONFIRMATION_TTL_SECONDS + 2
        monkeypatch.setattr(problem_resolution_service.time, "time", lambda: future)
        committed = commit_duplicate(client, movie_id, headers, preview["confirmation_token"])
        assert committed["status"] == "failed", committed
        assert committed["error"]["code"] == "CONFIRMATION_EXPIRED"
        assert loser_path.exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)
