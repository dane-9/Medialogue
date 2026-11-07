"""Problems that end in a choice: the choice is offered and carried out."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db import session as db_session
from app.integrations.filesystem import FilesystemObserver
from app.models.domain import (IdentityState, MediaDirectory, Movie, MovieRelease, Problem, ProblemStatus, ReleaseState,
                               StorageRoot, Torrent, TorrentClientObservation)
from app.services.missing_paths import PASSES_BEFORE_PROBLEM, clear_missing_path, report_missing_path
from app.services.reconciliation import reconcile_movie_directory
from tests.test_torrent_archive import _login, archive_client  # noqa: F401

NEW_FOLDER = "Inception.2010.Directors.Cut.1080p.BluRay-GRP"


def _seed_two_missing_releases(client, base):
    headers = _login(client)
    root_path = base / "movies"
    root_path.mkdir()
    assert client.post("/api/v1/storage-roots", headers=headers,
                       json={"name": "Movies", "path": str(root_path), "media_type": "movies"}).status_code == 201
    new_folder = root_path / NEW_FOLDER
    new_folder.mkdir()
    (new_folder / f"{NEW_FOLDER}.mkv").write_bytes(b"video")

    async def seed():
        async with db_session.async_session_factory() as db:
            root = await db.scalar(select(StorageRoot))
            movie = Movie(title="Inception", sort_title="Inception", year=2010, identity_state=IdentityState.MATCHED)
            db.add(movie); await db.flush()
            ids = {}
            for edition in ("IMAX", "Extended Cut"):
                release = MovieRelease(movie_id=movie.id, raw_release_name=f"Inception.2010.{edition}", effective_edition=edition,
                                       release_state=ReleaseState.MISSING)
                db.add(release); await db.flush()
                db.add(MediaDirectory(storage_root_id=root.id, resolved_path=str(root_path / f"gone-{edition}"),
                                      movie_release_id=release.id, exists=False))
                ids[edition] = release.id
            await db.flush()
            observation = FilesystemObserver().inspect_directory(new_folder, Path(root.resolved_root_path))
            result = await reconcile_movie_directory(db, root, observation, movie_hint=movie)
            await db.commit()
            return movie.id, ids, result

    movie_id, ids, result = asyncio.run(seed())
    assert result == "review"
    return headers, movie_id, ids


def test_an_unclear_replacement_target_offers_the_missing_releases_and_attaches_the_choice(archive_client):
    client, base, _ = archive_client
    headers, movie_id, ids = _seed_two_missing_releases(client, base)

    listed = client.get("/api/v1/problems?reason=AMBIGUOUS_REPLACEMENT_TARGET").json()
    assert listed["total"] == 1
    problem = listed["items"][0]
    assert problem["available_actions"][0] == "choose_replacement_target"
    assert set(problem["details"]["candidate_release_ids"]) == {str(item) for item in ids.values()}

    # Only a listed release can be chosen.
    wrong = client.post(f"/api/v1/problems/{problem['id']}/resolve", headers=headers,
                        json={"action": "choose_replacement_target", "payload": {"release_id": str(uuid.uuid4())}})
    assert wrong.status_code == 422, wrong.text

    chosen = ids["Extended Cut"]
    done = client.post(f"/api/v1/problems/{problem['id']}/resolve", headers=headers,
                       json={"action": "choose_replacement_target", "payload": {"release_id": str(chosen)}})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "resolved"

    async def check():
        async with db_session.async_session_factory() as db:
            assert (await db.get(MovieRelease, chosen)).release_state == ReleaseState.REPLACED
            assert (await db.get(MovieRelease, ids["IMAX"])).release_state == ReleaseState.MISSING
            directory = await db.scalar(select(MediaDirectory).where(MediaDirectory.resolved_path.like(f"%{NEW_FOLDER}")))
            current = await db.get(MovieRelease, directory.movie_release_id)
            assert current.movie_id == movie_id and current.release_state == ReleaseState.CURRENT
            still_open = await db.scalar(select(Problem).where(Problem.reason == "AMBIGUOUS_REPLACEMENT_TARGET",
                                                                Problem.status == ProblemStatus.OPEN))
            assert still_open is None
    asyncio.run(check())


def test_missing_paths_are_one_problem_per_client_after_repeated_passes(archive_client):
    client, _base, _ = archive_client
    headers = _login(client)
    qbit = client.post("/api/v1/download-clients", headers=headers, json={
        "name": "Main", "url": "http://qbit.test:8080", "username": "u", "password": "p", "scope": "movies", "enabled": True,
    }).json()
    client_id = uuid.UUID(qbit["id"])

    async def scenario():
        async with db_session.async_session_factory() as db:
            torrents = []
            for name in ("Alpha", "Beta"):
                torrent = Torrent(info_hash=(name[0] * 40).lower(), name=name, metadata_json={})
                db.add(torrent); await db.flush()
                db.add(TorrentClientObservation(torrent_id=torrent.id, download_client_id=client_id, is_present=True,
                                                reported_save_path=f"/remote/{name}", resolved_save_path=f"/local/{name}",
                                                last_seen_at=datetime.now(timezone.utc)))
                torrents.append(torrent)
            await db.flush()

            async def open_count():
                return len((await db.scalars(select(Problem).where(
                    Problem.reason == "TORRENT_PATH_NOT_FOUND", Problem.status == ProblemStatus.OPEN))).all())

            for _ in range(PASSES_BEFORE_PROBLEM - 1):
                for torrent in torrents:
                    assert not await report_missing_path(db, torrent, download_client_id=client_id, message="gone")
            assert await open_count() == 0

            for torrent in torrents:
                assert await report_missing_path(db, torrent, download_client_id=client_id, message="gone",
                                                 details={"resolved_path": f"/local/{torrent.name}"})
            groups = (await db.scalars(select(Problem).where(Problem.reason == "TORRENT_PATH_NOT_FOUND"))).all()
            assert len(groups) == 1
            group = groups[0]
            assert (group.entity_type, group.entity_id) == ("download_client", client_id)
            assert [(item["name"], item["reported_path"]) for item in group.details["torrents"]] == [
                ("Alpha", "/remote/Alpha"), ("Beta", "/remote/Beta")]
            assert "2 completed downloads in Main" in group.message

            await clear_missing_path(db, torrents[0])
            assert [item["name"] for item in group.details["torrents"]] == ["Beta"]
            await clear_missing_path(db, torrents[1])
            assert group.status == ProblemStatus.RESOLVED
            await db.commit()

    asyncio.run(scenario())

    # The list shows the client as the subject.
    resolved = client.get("/api/v1/problems?status=resolved&reason=TORRENT_PATH_NOT_FOUND").json()
    assert resolved["items"][0]["subject"] == "Main"
