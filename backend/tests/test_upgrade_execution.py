import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import session as db_session
from app.db.base import Base
from app.integrations.qbittorrent import TorrentCategory
from app.main import create_app
from app.models.domain import (
    AccessMode,
    AssociationType,
    Episode,
    EpisodeMediaMap,
    IdentityState,
    MatchMethod,
    MediaDirectory,
    MediaFile,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    PresenceState,
    ReleaseScope,
    ReleaseState,
    Season,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    StorageRoot,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
    UpgradeCandidate,
)
from app.services.upgrade_execution import advance_upgrade_handoff, associate_upgrade_torrent


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "medialogue.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    settings = Settings(
        database_url=database_url,
        bootstrap_admin=True,
        config_dir=f"{db_path}.config",
        secret_key="test-secret-key-upgrade-execution",
    )
    engine = create_async_engine(database_url)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client, tmp_path
    asyncio.run(db_session.engine.dispose())
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass


def _login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminadmin"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _create_integrations(client: TestClient, headers: dict[str, str]) -> tuple[dict, dict]:
    qbit = client.post(
        "/api/v1/download-clients",
        headers=headers,
        json={
            "name": "Upgrade qBit",
            "url": "http://qbit-upgrade.test:8080",
            "username": "media",
            "password": "secret",
            "scope": "movies",
            "category": "movies",
            "tags": ["medialogue", "upgrade"],
            "enabled": True,
            "poll_interval_seconds": 15,
        },
    )
    assert qbit.status_code == 201, qbit.text
    indexer = client.post(
        "/api/v1/indexers",
        headers=headers,
        json={
            "name": "Upgrade Indexer",
            "torznab_url": "http://upgrade-indexer.test/api",
            "api_key": "secret",
            "scope": "movies",
            "enabled": True,
            "enable_interactive_search": True,
            "minimum_seeders": 0,
            "priority": 10,
            "download_client_ids": [qbit.json()["id"]],
        },
    )
    assert indexer.status_code == 201, indexer.text
    return qbit.json(), indexer.json()


def _create_show_integrations(client: TestClient, headers: dict[str, str]) -> tuple[dict, dict]:
    qbit = client.post(
        "/api/v1/download-clients",
        headers=headers,
        json={
            "name": "Upgrade Shows qBit",
            "url": "http://qbit-upgrade-shows.test:8080",
            "username": "media",
            "password": "secret",
            "scope": "shows",
            "category": "shows",
            "tags": ["medialogue", "upgrade"],
            "enabled": True,
            "poll_interval_seconds": 15,
        },
    )
    assert qbit.status_code == 201, qbit.text
    indexer = client.post(
        "/api/v1/indexers",
        headers=headers,
        json={
            "name": "Upgrade Shows Indexer",
            "torznab_url": "http://upgrade-shows-indexer.test/api",
            "api_key": "secret",
            "scope": "shows",
            "enabled": True,
            "enable_interactive_search": True,
            "minimum_seeders": 0,
            "priority": 10,
            "download_client_ids": [qbit.json()["id"]],
        },
    )
    assert indexer.status_code == 201, indexer.text
    return qbit.json(), indexer.json()


@dataclass
class FakeQBitBehavior:
    submissions: list[dict] = field(default_factory=list)
    removals: list[dict] = field(default_factory=list)
    fail_remove_hashes: set[str] = field(default_factory=set)

    def factory(self, url: str, username: str, password: str):
        return FakeQBitClient(self)


class FakeQBitClient:
    def __init__(self, behavior: FakeQBitBehavior):
        self.behavior = behavior

    async def list_categories(self):
        return [
            TorrentCategory(name="movies", save_path="/downloads/movies", resolved_save_path="/downloads/movies"),
            TorrentCategory(name="shows", save_path="/downloads/shows", resolved_save_path="/downloads/shows"),
        ]

    async def add_url(self, url: str, *, save_path=None, category=None, tags=(), **options):
        self.behavior.submissions.append({
            "url": url,
            "save_path": save_path,
            "category": category,
            "tags": tuple(tags),
            **options,
        })

    async def remove_torrent(self, info_hash: str, *, delete_files: bool = False):
        if info_hash in self.behavior.fail_remove_hashes:
            raise RuntimeError("simulated qBittorrent removal failure")
        self.behavior.removals.append({"info_hash": info_hash, "delete_files": delete_files})

    async def close(self):
        return None


async def _seed_movie_upgrade(tmp_path: Path, qbit: dict, indexer: dict) -> tuple[UUID, UUID, UUID, UUID, Path, Path]:
    root_path = tmp_path / "movies"
    old_dir = root_path / "Upgrade Movie (2024) [old]"
    new_dir = root_path / "Upgrade Movie (2024) [new]"
    old_dir.mkdir(parents=True)
    (old_dir / "Upgrade.Movie.2024.1080p.WEB-DL-GRP.mkv").write_bytes(b"old-video")
    (old_dir / "Upgrade.Movie.2024.1080p.WEB-DL-GRP.srt").write_text("old subtitle")
    new_dir.mkdir(parents=True)
    (new_dir / "Upgrade.Movie.2024.2160p.WEB-DL-GRP.mkv").write_bytes(b"new-video")

    async with db_session.async_session_factory() as db:
        root = StorageRoot(
            name="Movies",
            resolved_root_path=str(root_path),
            media_type=MediaType.MOVIES,
            access_mode=AccessMode.READ_WRITE,
            enabled=True,
        )
        movie = Movie(
            title="Upgrade Movie",
            sort_title="Upgrade Movie",
            year=2024,
            tmdb_id=7001,
            identity_state=IdentityState.MATCHED,
        )
        db.add_all([root, movie])
        await db.flush()
        old_release = MovieRelease(
            movie_id=movie.id,
            raw_release_name="Upgrade.Movie.2024.1080p.WEB-DL-GRP",
            release_state=ReleaseState.CURRENT,
            current_custom_format_score=0,
        )
        db.add(old_release)
        await db.flush()
        old_directory = MediaDirectory(
            storage_root_id=root.id,
            resolved_path=str(old_dir),
            movie_release_id=old_release.id,
        )
        db.add(old_directory)
        await db.flush()
        db.add(MediaFile(
            media_directory_id=old_directory.id,
            relative_path="Upgrade.Movie.2024.1080p.WEB-DL-GRP.mkv",
            filename="Upgrade.Movie.2024.1080p.WEB-DL-GRP.mkv",
        ))
        old_torrent = Torrent(
            info_hash="old-upgrade-hash",
            name=old_release.raw_release_name,
            archive_state=TorrentArchiveState.ARCHIVED,
            archive_path=str(tmp_path / "archive" / "old.torrent"),
            manifest_path=str(tmp_path / "archive" / "old.json"),
        )
        db.add(old_torrent)
        await db.flush()
        db.add_all([
            MovieReleaseTorrent(
                movie_release_id=old_release.id,
                torrent_id=old_torrent.id,
                association_type=AssociationType.ATTACHED,
            ),
            TorrentClientObservation(
                torrent_id=old_torrent.id,
                download_client_id=UUID(qbit["id"]),
                state="uploading",
                progress=1,
                category="movies",
                is_present=True,
            ),
        ])
        candidate = UpgradeCandidate(
            target_key=f"movie:{old_release.id}",
            media_type=MediaType.MOVIES,
            movie_id=movie.id,
            current_release_ids=[str(old_release.id)],
            current_release_name=old_release.raw_release_name,
            current_quality="1080p WEB-DL",
            current_custom_format_score=0,
            indexer_id=UUID(indexer["id"]),
            indexer_name=indexer["name"],
            guid="upgrade-guid",
            download_url=f"magnet:?xt=urn:btih:{'a' * 40}",
            title="Upgrade.Movie.2024.2160p.WEB-DL-GRP",
            quality="2160p WEB-DL",
            custom_format_score=25,
            score_delta=25,
            upgrade_types=["quality", "custom_format"],
            added_custom_formats=[{
                "custom_format_id": "format-1",
                "custom_format_name": "Preferred Encode",
                "contribution": 25,
                "previous_contribution": None,
                "delta": 25,
                "new": True,
            }],
            execution_eligible=True,
            comparison_snapshot={},
        )
        db.add(candidate)
        await db.commit()
        return candidate.id, movie.id, old_release.id, old_torrent.id, old_dir, new_dir


async def _simulate_movie_download_and_handoff(
    tmp_path: Path,
    qbit: dict,
    candidate_id: UUID,
    old_release_id: UUID,
    new_dir: Path,
    behavior: FakeQBitBehavior,
    expected_handoff_state: str = "cleanup_required",
) -> None:
    async with db_session.async_session_factory() as db:
        replacement = Torrent(
            info_hash="a" * 40,
            # qBittorrent's internal torrent name is allowed to differ from the
            # indexer's display title. The submitted info hash anchors it.
            name="Upgrade.Movie.2024.2160p.WEB-DL-GRP.INTERNAL",
            completed_at=datetime.now(timezone.utc),
            archive_state=TorrentArchiveState.NOT_ARCHIVED,
        )
        db.add(replacement)
        await db.flush()
        association = await associate_upgrade_torrent(
            db,
            replacement,
            download_client_id=UUID(qbit["id"]),
            scope=MediaType.MOVIES,
            complete=False,
        )
        assert association is not None
        candidate = await db.get(UpgradeCandidate, candidate_id)
        assert candidate is not None
        assert candidate.execution_state == "downloading"
        assert candidate.submitted_info_hash == "a" * 40
        incoming_id = UUID(candidate.incoming_release_ids[0])
        incoming = await db.get(MovieRelease, incoming_id)
        old = await db.get(MovieRelease, old_release_id)
        assert incoming is not None and old is not None
        assert incoming.release_state == ReleaseState.MISSING
        assert old.release_state == ReleaseState.CURRENT

        # Simulate the filesystem reconciler's validated replacement.
        incoming.release_state = ReleaseState.CURRENT
        old.release_state = ReleaseState.REPLACED
        old.replaced_at = datetime.now(timezone.utc)
        root = await db.scalar(select(StorageRoot).where(StorageRoot.media_type == MediaType.MOVIES))
        assert root is not None
        new_directory = MediaDirectory(
            storage_root_id=root.id,
            resolved_path=str(new_dir),
            movie_release_id=incoming.id,
        )
        db.add(new_directory)
        await db.flush()
        db.add(MediaFile(
            media_directory_id=new_directory.id,
            relative_path="Upgrade.Movie.2024.2160p.WEB-DL-GRP.mkv",
            filename="Upgrade.Movie.2024.2160p.WEB-DL-GRP.mkv",
        ))
        replacement.archive_state = TorrentArchiveState.ARCHIVED
        replacement.archive_path = str(tmp_path / "archive" / "new.torrent")
        replacement.manifest_path = str(tmp_path / "archive" / "new.json")
        db.add(TorrentClientObservation(
            torrent_id=replacement.id,
            download_client_id=UUID(qbit["id"]),
            state="uploading",
            progress=1,
            category="movies",
            is_present=True,
        ))
        await db.flush()
        updated = await advance_upgrade_handoff(
            db,
            replacement,
            qbittorrent_client_factory=behavior.factory,
        )
        assert updated is not None
        assert updated.execution_state == expected_handoff_state
        await db.commit()


async def _seed_show_upgrade(
    tmp_path: Path,
    qbit: dict,
    indexer: dict,
) -> tuple[UUID, UUID, list[UUID], list[UUID], Path, list[Path]]:
    root_path = tmp_path / "shows"
    show_path = root_path / "Upgrade Show"
    season_path = show_path / "Season 01"
    season_path.mkdir(parents=True)
    old_paths = [
        season_path / "Upgrade.Show.S01E01.1080p.WEB-DL-GRP.mkv",
        season_path / "Upgrade.Show.S01E02.1080p.WEB-DL-GRP.mkv",
    ]
    for path in old_paths:
        path.write_bytes(b"old-episode")
    old_paths[0].with_suffix(".srt").write_text("old subtitle")
    new_paths = [
        season_path / "Upgrade.Show.S01E01.2160p.WEB-DL-PACK.mkv",
        season_path / "Upgrade.Show.S01E02.2160p.WEB-DL-PACK.mkv",
    ]
    for path in new_paths:
        path.write_bytes(b"new-episode")

    async with db_session.async_session_factory() as db:
        root = StorageRoot(
            name="Shows",
            resolved_root_path=str(root_path),
            media_type=MediaType.SHOWS,
            access_mode=AccessMode.READ_WRITE,
            enabled=True,
        )
        show = Show(
            title="Upgrade Show",
            year=2024,
            tmdb_id=8001,
            identity_state=IdentityState.MATCHED,
        )
        db.add_all([root, show])
        await db.flush()
        season = Season(
            show_id=show.id,
            season_number=1,
            title="Season 1",
            monitored=True,
            metadata_json={"episode_count": 2},
        )
        db.add(season)
        await db.flush()
        episodes = [
            Episode(
                show_id=show.id,
                season_id=season.id,
                season_number=1,
                episode_number=number,
                title=f"Episode {number}",
                monitored=True,
                presence_state=PresenceState.PRESENT,
            )
            for number in (1, 2)
        ]
        db.add_all(episodes)
        await db.flush()
        directory = MediaDirectory(storage_root_id=root.id, resolved_path=str(show_path))
        db.add(directory)
        await db.flush()

        old_releases: list[ShowRelease] = []
        old_torrent_ids: list[UUID] = []
        for episode, old_path in zip(episodes, old_paths, strict=True):
            release = ShowRelease(
                show_id=show.id,
                season_id=season.id,
                raw_release_name=old_path.stem,
                release_scope=ReleaseScope.EPISODE,
                release_state=ReleaseState.CURRENT,
                current_custom_format_score=0,
            )
            db.add(release)
            await db.flush()
            media_file = MediaFile(
                media_directory_id=directory.id,
                relative_path=old_path.relative_to(show_path).as_posix(),
                filename=old_path.name,
            )
            db.add(media_file)
            await db.flush()
            db.add(EpisodeMediaMap(
                episode_id=episode.id,
                media_file_id=media_file.id,
                show_release_id=release.id,
                match_method=MatchMethod.PARSER,
                confidence=1,
            ))
            torrent = Torrent(
                info_hash=f"old-show-{episode.episode_number}",
                name=release.raw_release_name,
                archive_state=TorrentArchiveState.ARCHIVED,
                archive_path=str(tmp_path / "archive" / f"old-show-{episode.episode_number}.torrent"),
                manifest_path=str(tmp_path / "archive" / f"old-show-{episode.episode_number}.json"),
            )
            db.add(torrent)
            await db.flush()
            db.add_all([
                ShowReleaseTorrent(
                    show_release_id=release.id,
                    torrent_id=torrent.id,
                    association_type=AssociationType.ATTACHED,
                ),
                TorrentClientObservation(
                    torrent_id=torrent.id,
                    download_client_id=UUID(qbit["id"]),
                    state="uploading",
                    progress=1,
                    category="shows",
                    is_present=True,
                ),
            ])
            old_releases.append(release)
            old_torrent_ids.append(torrent.id)

        candidate = UpgradeCandidate(
            target_key=f"show-season:{season.id}",
            media_type=MediaType.SHOWS,
            season_id=season.id,
            current_release_ids=[str(release.id) for release in old_releases],
            current_release_name=old_releases[0].raw_release_name,
            current_quality="1080p WEB-DL",
            current_custom_format_score=0,
            indexer_id=UUID(indexer["id"]),
            indexer_name=indexer["name"],
            guid="show-upgrade-guid",
            download_url=f"magnet:?xt=urn:btih:{'b' * 40}",
            title="Upgrade.Show.S01.2160p.WEB-DL-PACK",
            quality="2160p WEB-DL",
            custom_format_score=15,
            score_delta=15,
            upgrade_types=["quality", "custom_format", "season_pack"],
            execution_eligible=True,
            comparison_snapshot={},
        )
        db.add(candidate)
        await db.commit()
        return (
            candidate.id,
            season.id,
            [release.id for release in old_releases],
            old_torrent_ids,
            show_path,
            new_paths,
        )


def test_movie_upgrade_submission_handoff_and_explicit_cleanup(client) -> None:
    test_client, tmp_path = client
    headers = _login(test_client)
    qbit, indexer = _create_integrations(test_client, headers)
    candidate_id, _movie_id, old_release_id, old_torrent_id, old_dir, new_dir = asyncio.run(
        _seed_movie_upgrade(tmp_path, qbit, indexer)
    )

    behavior = FakeQBitBehavior()
    from app.api import downloads as downloads_api

    test_client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    try:
        response = test_client.post(
            f"/api/v1/upgrade-candidates/{candidate_id}/upgrade",
            headers=headers,
            json={"download_client_id": qbit["id"], "category": "movies"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["execution_state"] == "submitted"
        assert len(behavior.submissions) == 1
        assert behavior.submissions[0]["category"] == "movies"
        assert behavior.submissions[0]["save_path"] is None
        assert old_dir.exists()
        assert behavior.removals == []

        asyncio.run(_simulate_movie_download_and_handoff(
            tmp_path,
            qbit,
            candidate_id,
            old_release_id,
            new_dir,
            behavior,
        ))
        assert behavior.removals == [{"info_hash": "old-upgrade-hash", "delete_files": False}]
        assert old_dir.exists(), "old media must still exist after qBittorrent handoff"
        assert new_dir.exists()

        preview = test_client.get(f"/api/v1/upgrade-candidates/{candidate_id}/cleanup-preview")
        assert preview.status_code == 200, preview.text
        payload = preview.json()
        assert payload["eligible"] is True
        assert [target["path"] for target in payload["targets"]] == [str(old_dir)]
        assert payload["total_bytes"] > 0

        cleanup = test_client.post(
            f"/api/v1/upgrade-candidates/{candidate_id}/cleanup",
            headers=headers,
            json={"confirmation_token": payload["confirmation_token"]},
        )
        assert cleanup.status_code == 200, cleanup.text
        assert cleanup.json()["state"] == "completed"
        assert str(old_dir) in cleanup.json()["deleted_paths"]
        assert not old_dir.exists()
        assert new_dir.exists(), "replacement media must not be part of old-media cleanup"

        async def verify_database() -> None:
            async with db_session.async_session_factory() as db:
                candidate = await db.get(UpgradeCandidate, candidate_id)
                old = await db.get(MovieRelease, old_release_id)
                old_torrent = await db.get(Torrent, old_torrent_id)
                observation = await db.scalar(select(TorrentClientObservation).where(
                    TorrentClientObservation.torrent_id == old_torrent_id,
                    TorrentClientObservation.download_client_id == UUID(qbit["id"]),
                ))
                assert candidate is not None and candidate.execution_state == "completed"
                assert old is not None and old.release_state == ReleaseState.REMOVED
                assert old_torrent is not None and old_torrent.archive_state == TorrentArchiveState.ARCHIVED
                assert observation is not None and observation.is_present is False

        asyncio.run(verify_database())
    finally:
        test_client.app.dependency_overrides.clear()


def test_movie_upgrade_handoff_rejects_a_different_edition(client) -> None:
    test_client, tmp_path = client
    headers = _login(test_client)
    qbit, indexer = _create_integrations(test_client, headers)
    candidate_id, _movie_id, old_release_id, _old_torrent_id, _old_dir, _new_dir = asyncio.run(
        _seed_movie_upgrade(tmp_path, qbit, indexer)
    )

    async def run() -> None:
        async with db_session.async_session_factory() as db:
            old = await db.get(MovieRelease, old_release_id)
            candidate = await db.get(UpgradeCandidate, candidate_id)
            assert old is not None and candidate is not None
            old.parsed_edition = "Director's Cut"
            old.effective_edition = "Director's Cut"
            candidate.title = "Upgrade.Movie.2024.Directors.Cut.2160p.WEB-DL-GRP"
            candidate.comparison_snapshot = {
                "current": {"edition": "Director's Cut"},
                "candidate": {"edition": "Director's Cut"},
            }
            candidate.execution_state = "submitted"
            candidate.submitted_info_hash = "c" * 40
            candidate.selected_download_client_id = UUID(qbit["id"])
            candidate.submitted_at = datetime.now(timezone.utc)
            replacement = Torrent(
                info_hash="c" * 40,
                name="Upgrade.Movie.2024.2160p.WEB-DL-GRP",
            )
            db.add(replacement)
            await db.flush()

            association = await associate_upgrade_torrent(
                db,
                replacement,
                download_client_id=UUID(qbit["id"]),
                scope=MediaType.MOVIES,
                complete=False,
            )

            assert association is not None
            assert candidate.execution_state == "handoff_attention"
            assert "Director's Cut → Theatrical Cut" in (candidate.execution_error or "")
            assert candidate.incoming_release_ids == []

    asyncio.run(run())


def test_cleanup_refuses_when_replacement_disappears_after_preview(client) -> None:
    test_client, tmp_path = client
    headers = _login(test_client)
    qbit, indexer = _create_integrations(test_client, headers)
    candidate_id, _movie_id, old_release_id, _old_torrent_id, old_dir, new_dir = asyncio.run(
        _seed_movie_upgrade(tmp_path, qbit, indexer)
    )

    behavior = FakeQBitBehavior()
    from app.api import downloads as downloads_api

    test_client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    try:
        response = test_client.post(
            f"/api/v1/upgrade-candidates/{candidate_id}/upgrade",
            headers=headers,
            json={"download_client_id": qbit["id"], "category": "movies"},
        )
        assert response.status_code == 200, response.text
        asyncio.run(_simulate_movie_download_and_handoff(
            tmp_path,
            qbit,
            candidate_id,
            old_release_id,
            new_dir,
            behavior,
        ))

        preview = test_client.get(f"/api/v1/upgrade-candidates/{candidate_id}/cleanup-preview")
        assert preview.status_code == 200, preview.text
        assert preview.json()["eligible"] is True

        replacement_file = new_dir / "Upgrade.Movie.2024.2160p.WEB-DL-GRP.mkv"
        replacement_file.unlink()
        cleanup = test_client.post(
            f"/api/v1/upgrade-candidates/{candidate_id}/cleanup",
            headers=headers,
            json={"confirmation_token": preview.json()["confirmation_token"]},
        )
        assert cleanup.status_code == 409, cleanup.text
        assert cleanup.json()["error"]["code"] == "DELETE_PREVIEW_STALE"
        assert old_dir.exists(), "old media must be retained when replacement verification changes"
    finally:
        test_client.app.dependency_overrides.clear()


def test_show_season_pack_upgrade_keeps_new_pack_and_deletes_only_reviewed_old_files(client) -> None:
    test_client, tmp_path = client
    headers = _login(test_client)
    qbit, indexer = _create_show_integrations(test_client, headers)
    candidate_id, season_id, old_release_ids, old_torrent_ids, show_path, new_paths = asyncio.run(
        _seed_show_upgrade(tmp_path, qbit, indexer)
    )

    behavior = FakeQBitBehavior()
    from app.api import downloads as downloads_api

    test_client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    try:
        response = test_client.post(
            f"/api/v1/upgrade-candidates/{candidate_id}/upgrade",
            headers=headers,
            json={"download_client_id": qbit["id"], "category": "shows"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["execution_state"] == "submitted"

        async def simulate_show_download_and_handoff() -> None:
            async with db_session.async_session_factory() as db:
                replacement = Torrent(
                    info_hash="b" * 40,
                    name="Upgrade.Show.S01.2160p.WEB-DL-PACK.INTERNAL",
                    completed_at=datetime.now(timezone.utc),
                    archive_state=TorrentArchiveState.NOT_ARCHIVED,
                )
                db.add(replacement)
                await db.flush()
                association = await associate_upgrade_torrent(
                    db,
                    replacement,
                    download_client_id=UUID(qbit["id"]),
                    scope=MediaType.SHOWS,
                    complete=False,
                )
                assert association is not None
                candidate = await db.get(UpgradeCandidate, candidate_id)
                assert candidate is not None
                assert candidate.submitted_info_hash == "b" * 40
                incoming_id = UUID(candidate.incoming_release_ids[0])
                incoming = await db.get(ShowRelease, incoming_id)
                assert incoming is not None
                assert incoming.release_scope == ReleaseScope.SEASON_PACK
                assert incoming.release_state == ReleaseState.MISSING
                incoming.release_state = ReleaseState.CURRENT

                directory = await db.scalar(select(MediaDirectory).where(MediaDirectory.resolved_path == str(show_path)))
                episodes = list(
                    (
                        await db.scalars(
                            select(Episode).where(Episode.season_id == season_id).order_by(Episode.episode_number)
                        )
                    ).all()
                )
                assert directory is not None and len(episodes) == 2
                for episode, path in zip(episodes, new_paths, strict=True):
                    media_file = MediaFile(
                        media_directory_id=directory.id,
                        relative_path=path.relative_to(show_path).as_posix(),
                        filename=path.name,
                    )
                    db.add(media_file)
                    await db.flush()
                    db.add(EpisodeMediaMap(
                        episode_id=episode.id,
                        media_file_id=media_file.id,
                        show_release_id=incoming.id,
                        match_method=MatchMethod.PARSER,
                        confidence=1,
                    ))

                replacement.archive_state = TorrentArchiveState.ARCHIVED
                replacement.archive_path = str(tmp_path / "archive" / "show-new.torrent")
                replacement.manifest_path = str(tmp_path / "archive" / "show-new.json")
                db.add(TorrentClientObservation(
                    torrent_id=replacement.id,
                    download_client_id=UUID(qbit["id"]),
                    state="uploading",
                    progress=1,
                    category="shows",
                    is_present=True,
                ))
                await db.flush()
                updated = await advance_upgrade_handoff(
                    db,
                    replacement,
                    qbittorrent_client_factory=behavior.factory,
                )
                assert updated is not None
                assert updated.execution_state == "cleanup_required"
                for release_id in old_release_ids:
                    old_release = await db.get(ShowRelease, release_id)
                    assert old_release is not None and old_release.release_state == ReleaseState.REPLACED
                await db.commit()

        asyncio.run(simulate_show_download_and_handoff())
        assert {item["info_hash"] for item in behavior.removals} == {"old-show-1", "old-show-2"}
        assert all(item["delete_files"] is False for item in behavior.removals)

        preview = test_client.get(f"/api/v1/upgrade-candidates/{candidate_id}/cleanup-preview")
        assert preview.status_code == 200, preview.text
        payload = preview.json()
        assert payload["eligible"] is True
        target_paths = {target["path"] for target in payload["targets"]}
        assert str(show_path / "Season 01" / "Upgrade.Show.S01E01.1080p.WEB-DL-GRP.mkv") in target_paths
        assert str(show_path / "Season 01" / "Upgrade.Show.S01E01.1080p.WEB-DL-GRP.srt") in target_paths
        assert all(str(path) not in target_paths for path in new_paths)

        cleanup = test_client.post(
            f"/api/v1/upgrade-candidates/{candidate_id}/cleanup",
            headers=headers,
            json={"confirmation_token": payload["confirmation_token"]},
        )
        assert cleanup.status_code == 200, cleanup.text
        assert cleanup.json()["state"] == "completed"
        assert all(path.exists() for path in new_paths)
        assert not (show_path / "Season 01" / "Upgrade.Show.S01E01.1080p.WEB-DL-GRP.mkv").exists()
        assert not (show_path / "Season 01" / "Upgrade.Show.S01E01.1080p.WEB-DL-GRP.srt").exists()
        assert not (show_path / "Season 01" / "Upgrade.Show.S01E02.1080p.WEB-DL-GRP.mkv").exists()

        async def verify_show_database() -> None:
            async with db_session.async_session_factory() as db:
                candidate = await db.get(UpgradeCandidate, candidate_id)
                assert candidate is not None and candidate.execution_state == "completed"
                for release_id in old_release_ids:
                    release = await db.get(ShowRelease, release_id)
                    assert release is not None and release.release_state == ReleaseState.REMOVED
                episodes = list((await db.scalars(select(Episode).where(Episode.season_id == season_id))).all())
                assert episodes and all(episode.presence_state == PresenceState.PRESENT for episode in episodes)
                for torrent_id in old_torrent_ids:
                    observation = await db.scalar(
                        select(TorrentClientObservation).where(
                            TorrentClientObservation.torrent_id == torrent_id,
                            TorrentClientObservation.download_client_id == UUID(qbit["id"]),
                        )
                    )
                    assert observation is not None and observation.is_present is False

        asyncio.run(verify_show_database())
    finally:
        test_client.app.dependency_overrides.clear()


def test_handoff_attention_can_retry_old_torrent_removal_without_touching_media(client) -> None:
    test_client, tmp_path = client
    headers = _login(test_client)
    qbit, indexer = _create_integrations(test_client, headers)
    candidate_id, _movie_id, old_release_id, _old_torrent_id, old_dir, new_dir = asyncio.run(
        _seed_movie_upgrade(tmp_path, qbit, indexer)
    )

    behavior = FakeQBitBehavior(fail_remove_hashes={"old-upgrade-hash"})
    from app.api import downloads as downloads_api

    test_client.app.dependency_overrides[downloads_api.get_qbittorrent_client_factory] = lambda: behavior.factory
    try:
        response = test_client.post(
            f"/api/v1/upgrade-candidates/{candidate_id}/upgrade",
            headers=headers,
            json={"download_client_id": qbit["id"], "category": "movies"},
        )
        assert response.status_code == 200, response.text
        asyncio.run(_simulate_movie_download_and_handoff(
            tmp_path,
            qbit,
            candidate_id,
            old_release_id,
            new_dir,
            behavior,
            expected_handoff_state="handoff_attention",
        ))
        assert old_dir.exists()
        assert behavior.removals == []

        behavior.fail_remove_hashes.clear()
        retry = test_client.post(
            f"/api/v1/upgrade-candidates/{candidate_id}/retry-handoff",
            headers=headers,
        )
        assert retry.status_code == 200, retry.text
        assert retry.json()["execution_state"] == "cleanup_required"
        assert behavior.removals == [{"info_hash": "old-upgrade-hash", "delete_files": False}]
        assert old_dir.exists(), "handoff retry must still leave filesystem cleanup explicit"
    finally:
        test_client.app.dependency_overrides.clear()
