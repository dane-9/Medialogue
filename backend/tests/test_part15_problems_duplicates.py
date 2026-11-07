import asyncio
import shutil
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.integration_config import DownloadClientConfig, get_integration_config_store
from app.db import session as db_session
from app.db.base import Base
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
)
from app.services.reconciliation import mark_absent_known_directories
from app.services.torrent_reconciliation import reconcile_torrent_disagreements

pytestmark = pytest.mark.usefixtures("scratch_cwd")


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


def test_movie_edition_overrides_reclassify_copies_and_survive_automatic_mode(client: TestClient) -> None:
    headers = login(client)
    root_path = Path.cwd() / f"part15-edition-overrides-{uuid.uuid4().hex}"
    root_path.mkdir()
    try:
        movie_id, first_id, second_id, problem_id = db_run(lambda db: seed_movie_duplicate(db, root_path))

        options = client.get("/api/v1/movies/edition-options")
        assert options.status_code == 200, options.text
        assert "Director's Cut" in options.json()["editions"]

        resolved = client.patch(
            f"/api/v1/movies/{movie_id}/releases/editions",
            headers=headers,
            json={
                "updates": [
                    {"release_id": str(first_id), "mode": "standard"},
                    {"release_id": str(second_id), "mode": "named", "edition": "director's cut"},
                ]
            },
        )
        assert resolved.status_code == 200, resolved.text
        payload = resolved.json()
        assert payload["duplicate_resolved"] is True
        by_id = {item["release_id"]: item for item in payload["releases"]}
        assert by_id[str(first_id)]["edition_override_active"] is True
        assert by_id[str(first_id)]["manual_edition_override"] is None
        assert by_id[str(first_id)]["effective_edition"] is None
        assert by_id[str(second_id)]["manual_edition_override"] == "Director's Cut"
        assert by_id[str(second_id)]["effective_edition"] == "Director's Cut"
        assert {item["state"] for item in payload["releases"]} == {"current"}

        movie = client.get(f"/api/v1/movies/{movie_id}")
        assert movie.status_code == 200, movie.text
        releases = {item["id"]: item for item in movie.json()["releases"]}
        assert releases[str(first_id)]["edition_override_active"] is True
        assert releases[str(first_id)]["parsed_edition"] is None
        assert releases[str(second_id)]["manual_edition_override"] == "Director's Cut"

        async def problem_status(db):
            problem = await db.get(Problem, problem_id)
            return problem.status

        assert db_run(problem_status) == ProblemStatus.RESOLVED

        duplicate_again = client.patch(
            f"/api/v1/movies/{movie_id}/releases/editions",
            headers=headers,
            json={
                "updates": [
                    {"release_id": str(first_id), "mode": "automatic"},
                    {"release_id": str(second_id), "mode": "automatic"},
                ]
            },
        )
        assert duplicate_again.status_code == 200, duplicate_again.text
        assert duplicate_again.json()["duplicate_resolved"] is False
        assert {item["state"] for item in duplicate_again.json()["releases"]} == {"duplicate"}
    finally:
        shutil.rmtree(root_path, ignore_errors=True)


def test_problem_actions_are_explicit_and_recheck_does_not_fake_resolution(client: TestClient) -> None:
    headers = login(client)

    async def seed(db):
        problem = Problem(
            reason="TORRENT_PATH_NOT_FOUND",
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


def test_remote_path_mapping_can_be_managed_through_api(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    local_root = tmp_path / "movies"
    local_root.mkdir()

    async def seed(db):
        root = StorageRoot(
            name="Movies mapping root",
            resolved_root_path=str(local_root),
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
            "remote_prefix": " /downloads/movies/ ",
            "local_prefix": str(local_root),
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
    assert mapping["local_prefix"] == str(local_root.resolve())
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


def test_remote_path_mapping_rejects_invalid_and_duplicate_prefixes(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    local_root = tmp_path / "media"
    local_root.mkdir()
    base = {
        "name": "Movies",
        "integration_type": "qbittorrent",
        "remote_prefix": "/downloads/movies",
        "local_prefix": str(local_root),
        "enabled": True,
    }

    relative = client.post(
        "/api/v1/remote-path-mappings",
        headers=headers,
        json={**base, "local_prefix": "relative/media"},
    )
    assert relative.status_code == 422, relative.text
    assert relative.json()["error"]["code"] == "PATH_MAPPING_LOCAL_NOT_ABSOLUTE"

    remote_relative = client.post(
        "/api/v1/remote-path-mappings",
        headers=headers,
        json={**base, "remote_prefix": "downloads/movies"},
    )
    assert remote_relative.status_code == 422, remote_relative.text
    assert remote_relative.json()["error"]["code"] == "PATH_MAPPING_REMOTE_NOT_ABSOLUTE"

    missing = client.post(
        "/api/v1/remote-path-mappings",
        headers=headers,
        json={**base, "local_prefix": str(tmp_path / "missing")},
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["error"]["code"] == "PATH_MAPPING_LOCAL_NOT_FOUND"

    first = client.post("/api/v1/remote-path-mappings", headers=headers, json=base)
    assert first.status_code == 201, first.text
    duplicate = client.post(
        "/api/v1/remote-path-mappings",
        headers=headers,
        json={**base, "name": "Duplicate", "remote_prefix": "/DOWNLOADS/MOVIES/"},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "REMOTE_PATH_MAPPING_DUPLICATE"

    nested = client.post(
        "/api/v1/remote-path-mappings",
        headers=headers,
        json={**base, "name": "Nested", "remote_prefix": "/downloads/movies/uhd"},
    )
    assert nested.status_code == 201, nested.text

    collision = client.patch(
        f"/api/v1/remote-path-mappings/{nested.json()['id']}",
        headers=headers,
        json={"remote_prefix": "/downloads/movies/"},
    )
    assert collision.status_code == 409, collision.text
    assert collision.json()["error"]["code"] == "REMOTE_PATH_MAPPING_DUPLICATE"


def test_root_scoped_remote_mapping_cannot_translate_outside_selected_root(client: TestClient, tmp_path: Path) -> None:
    headers = login(client)
    local_prefix = tmp_path / "movies"
    selected_root = local_prefix / "Cartoons"
    selected_root.mkdir(parents=True)

    async def seed(db):
        root = StorageRoot(
            name="Cartoons mapping root",
            resolved_root_path=str(selected_root),
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
            "local_prefix": str(local_prefix),
            "storage_root_id": str(root_id),
            "enabled": True,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "PATH_MAPPING_OUTSIDE_ROOT"
