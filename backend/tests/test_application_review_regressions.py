from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.integrations.filesystem import FilesystemObserver, OutsideMediaRootError
from app.models.domain import (
    Episode, EpisodeMediaMap, MediaDirectory, MediaFile, MediaType, Movie,
    PresenceState, QualityProfile, Season, Show, StorageRoot, UpgradeCandidate,
)
from app.services import tmdb
from app.services.bulk import change_movie_profile
from app.services.shows import mark_absent_show_directories


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def seed_episode(db):
    show = Show(title="Example", tmdb_id=123)
    db.add(show)
    await db.flush()
    season = Season(show_id=show.id, season_number=1)
    db.add(season)
    await db.flush()
    episode = Episode(show_id=show.id, season_id=season.id, season_number=1,
                      episode_number=1, tmdb_id=456, presence_state=PresenceState.PRESENT)
    db.add(episode)
    await db.commit()
    return show, episode


def metadata_client(monkeypatch, episodes):
    metadata = SimpleNamespace(
        title="Example", year=2020, overview=None, poster_path=None,
        original_language="en", tvdb_id=None,
        seasons=[SimpleNamespace(season_number=1, title="Season 1", episode_count=1,
                                 air_date=None, poster_path=None)],
    )
    client = SimpleNamespace(get_show=AsyncMock(return_value=metadata),
                             get_season=AsyncMock(return_value=episodes), close=AsyncMock())
    config = SimpleNamespace(enabled=True, api_key="test")
    monkeypatch.setattr(tmdb, "get_tmdb_configuration", AsyncMock(return_value=config))
    return client, config


@pytest.mark.parametrize("failure", ["season", "group"])
async def test_failed_metadata_fetch_never_parks_existing_episode(db, monkeypatch, failure):
    show, episode = await seed_episode(db)
    client, config = metadata_client(monkeypatch, [])
    if failure == "group":
        show.tmdb_episode_group_id = "alternative"
        client.get_episode_group = AsyncMock(side_effect=TimeoutError("Ordering unavailable"))
    else:
        client.get_season.side_effect = TimeoutError("Season unavailable")
    with pytest.raises(TimeoutError):
        await tmdb.sync_show_metadata(db, show, client_factory=lambda _: client)
    # Even a caller committing the recorded failure cannot persist parked numbers.
    await db.commit()
    await db.refresh(episode)
    assert (episode.season_number, episode.episode_number) == (1, 1)
    assert config.health == "unavailable"
    client.close.assert_awaited_once()


async def test_metadata_refresh_reuses_placeholder_and_preserves_unreturned_episode(db, monkeypatch):
    show, episode = await seed_episode(db)
    episode.tmdb_id = None
    omitted = Episode(show_id=show.id, season_id=episode.season_id,
                      season_number=1, episode_number=2, tmdb_id=789)
    db.add(omitted)
    await db.commit()
    item = SimpleNamespace(tmdb_id=456, season_number=1, episode_number=1,
                           title="Pilot", air_date=None, overview=None)
    client, _ = metadata_client(monkeypatch, [item])
    await tmdb.sync_show_metadata(db, show, client_factory=lambda _: client)
    await db.commit()
    rows = (await db.scalars(select(Episode).where(Episode.show_id == show.id))).all()
    assert len(rows) == 2
    assert episode.tmdb_id == 456
    assert (episode.season_number, episode.episode_number) == (1, 1)
    assert (omitted.season_number, omitted.episode_number) == (1, 2)


def test_unreadable_directory_fails_scan(tmp_path, monkeypatch):
    (tmp_path / "Movie").mkdir()
    observer = FilesystemObserver()
    def denied(*args):
        raise PermissionError("Access denied")
    monkeypatch.setattr(observer, "inspect_directory", denied)
    with pytest.raises(PermissionError, match="Access denied"):
        observer.scan_root(str(tmp_path))


def test_nested_traversal_error_fails_scan(tmp_path, monkeypatch):
    directory = tmp_path / "Show"
    directory.mkdir()
    def unreadable_walk(path, *, onerror, followlinks):
        onerror(PermissionError("Unreadable season folder"))
        return iter(())
    monkeypatch.setattr("app.integrations.filesystem.os.walk", unreadable_walk)
    with pytest.raises(PermissionError, match="Unreadable season"):
        FilesystemObserver().inspect_directory(directory, tmp_path)


def test_outside_root_remains_intentionally_excluded(tmp_path, monkeypatch):
    (tmp_path / "Link").mkdir()
    observer = FilesystemObserver()
    def outside(*args):
        raise OutsideMediaRootError("Outside root")
    monkeypatch.setattr(observer, "inspect_directory", outside)
    assert observer.scan_root(str(tmp_path)) == []


async def test_bulk_profile_change_invalidates_idle_but_not_active_upgrades(db):
    movie = Movie(title="Example", sort_title="Example")
    profile = QualityProfile(name="New policy")
    db.add_all([movie, profile])
    await db.flush()
    rows = []
    for state in (None, "submitted"):
        row = UpgradeCandidate(target_key=f"target:{state}", media_type=MediaType.MOVIES,
                               movie_id=movie.id, current_release_name="Old", indexer_name="Indexer",
                               guid="candidate", title="Old recommendation", execution_eligible=True,
                               execution_state=state)
        db.add(row)
        rows.append(row)
    await db.commit()
    assert await change_movie_profile(db, [movie], profile.id) == 1
    await db.commit()
    assert rows[0].execution_eligible is False
    assert rows[0].execution_block_reason
    assert rows[1].execution_eligible is True


@pytest.mark.parametrize("other_copy", [True, False])
async def test_missing_directory_checks_other_episode_copies(db, other_copy):
    show, episode = await seed_episode(db)
    roots = [StorageRoot(name=name, resolved_root_path=f"/media/{name}", media_type=MediaType.SHOWS)
             for name in ("a", "b")]
    db.add_all(roots)
    await db.flush()
    files = []
    for root in roots:
        directory = MediaDirectory(storage_root_id=root.id, resolved_path=f"{root.resolved_root_path}/Example")
        db.add(directory)
        await db.flush()
        media_file = MediaFile(media_directory_id=directory.id, relative_path="S01E01.mkv",
                               filename="S01E01.mkv", exists=True if not files else other_copy)
        db.add(media_file)
        await db.flush()
        db.add(EpisodeMediaMap(episode_id=episode.id, media_file_id=media_file.id))
        files.append(media_file)
    await db.commit()
    for _ in range(3):
        await mark_absent_show_directories(db, roots[0], set())
        await db.flush()
    assert not files[0].exists
    assert episode.presence_state == (PresenceState.PRESENT if other_copy else PresenceState.MISSING)
