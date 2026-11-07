"""Why a title is in Replace Media: one test per reason, and the safeguards."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.domain import (AssociationType, IdentityState, Movie, MovieRelease, MovieReleaseTorrent, ReleaseState,
                               ReplacementReview, Torrent, TorrentArchiveState, TorrentClientObservation)
from app.services.replacements import (FILES_MISSING, MISSING, NO_SEEDERS, NO_TORRENT, NOT_WATCHED, REMOVED_FROM_TRACKER, TRACKER_GONE,
                                       ReplacementTarget, _dismissed, classify_torrent, release_problems)
from app.services.torrent_tracking import other_seeders, terminal_tracker_reason, track_other_seeders

NOW = datetime.now(timezone.utc)
CLIENT = uuid4()


def observation(*, present=True, removed_at=None, alone_since=None, client=CLIENT, state="stalledUP"):
    return SimpleNamespace(is_present=present, removed_at=removed_at, last_seen_at=removed_at or NOW,
                           alone_since=alone_since, download_client_id=client, state=state)


def tracker(*, state="working", checked=NOW, worked=None, first=None, client=CLIENT):
    return SimpleNamespace(download_client_id=client, active=True, health_state=state, last_checked_at=checked,
                           last_success_at=worked, first_seen_at=first or NOW - timedelta(days=30))


def torrent(observations, trackers=(), summary=None):
    return SimpleNamespace(observations=list(observations), tracker_observations=list(trackers), tracker_summary=summary or {})


def reason(item, *, removed=False):
    problem = classify_torrent(item, NOW, confirmed_removed=removed)
    return problem.reason if problem else None


def test_other_seeders_leaves_this_client_out_only_while_it_seeds():
    assert other_seeders(seeds_total=1, seeds_connected=0, state="stalledUP", progress=1) == 0
    assert other_seeders(seeds_total=3, seeds_connected=0, state="uploading", progress=1) == 2
    # Paused, the client is not announcing, so the tracker's count is all others.
    assert other_seeders(seeds_total=1, seeds_connected=0, state="stoppedUP", progress=1) == 1
    # Connected seeders are real whatever the tracker says.
    assert other_seeders(seeds_total=0, seeds_connected=2, state="stalledUP", progress=1) == 2
    assert other_seeders(seeds_total=None, seeds_connected=None, state="stalledUP", progress=1) is None
    assert other_seeders(seeds_total=-1, seeds_connected=0, state="stalledUP", progress=1) is None


def test_alone_since_starts_once_and_clears_when_another_seeder_appears():
    row = SimpleNamespace(seeds_total=1, seeds=0, state="stalledUP", progress=1, alone_since=None)
    track_other_seeders(row, now=NOW)
    assert row.alone_since == NOW
    track_other_seeders(row, now=NOW + timedelta(hours=1))
    assert row.alone_since == NOW
    row.seeds_total = None
    track_other_seeders(row, now=NOW + timedelta(hours=2))
    assert row.alone_since == NOW, "an unknown count changes nothing"
    row.seeds_total = 2
    track_other_seeders(row, now=NOW + timedelta(hours=3))
    assert row.alone_since is None


def test_not_in_qbittorrent_waits_a_day_and_keeps_the_trackers_last_word():
    assert reason(torrent([observation(present=False, removed_at=NOW - timedelta(hours=2))])) is None
    assert reason(torrent([observation(present=False, removed_at=NOW - timedelta(days=2))])) == NOT_WATCHED
    # A torrent no client has on record at all is not watched either.
    assert reason(torrent([])) == NOT_WATCHED
    # Deleting a dead torrent from qBittorrent keeps the title listed as removed.
    dead = torrent([observation(present=False, removed_at=NOW)], summary={"state": "unregistered", "checked_at": NOW.isoformat()})
    assert reason(dead) == REMOVED_FROM_TRACKER


def test_removed_from_tracker_comes_from_the_live_confirmation():
    assert reason(torrent([observation()], [tracker(state="error")]), removed=True) == REMOVED_FROM_TRACKER


def test_tracker_gone_needs_a_week_without_any_working_tracker():
    week_old = tracker(state="error", worked=NOW - timedelta(days=8))
    assert reason(torrent([observation()], [week_old])) == TRACKER_GONE
    assert reason(torrent([observation()], [tracker(state="error", worked=NOW - timedelta(days=2))])) is None
    assert reason(torrent([observation()], [week_old, tracker(state="working")])) is None
    # A client that has not checked its trackers lately proves nothing.
    stale = tracker(state="error", worked=NOW - timedelta(days=8), checked=NOW - timedelta(days=1))
    assert reason(torrent([observation()], [stale])) is None
    # Never having worked counts from when the tracker was first seen.
    never = tracker(state="error", first=NOW - timedelta(days=10))
    assert reason(torrent([observation()], [never])) == TRACKER_GONE
    # A paused torrent never contacts its tracker; that is not a dead tracker.
    paused = tracker(state="not_contacted", first=NOW - timedelta(days=10))
    assert reason(torrent([observation()], [paused])) is None


def test_no_other_seeders_needs_two_weeks_on_every_client():
    healthy = [tracker()]
    assert reason(torrent([observation(alone_since=NOW - timedelta(days=15))], healthy)) == NO_SEEDERS
    assert reason(torrent([observation(alone_since=NOW - timedelta(days=5))], healthy)) is None
    second = uuid4()
    both = [observation(alone_since=NOW - timedelta(days=15)), observation(client=second)]
    assert reason(torrent(both, healthy + [tracker(client=second)])) is None


def test_ignoring_is_tied_to_the_reason():
    review = SimpleNamespace(ignored_reason=NO_SEEDERS)
    assert _dismissed(review, NO_SEEDERS)
    assert not _dismissed(review, REMOVED_FROM_TRACKER)
    assert not _dismissed(None, NO_SEEDERS)


@pytest.mark.asyncio
async def test_release_problems_cover_no_torrent_downloads_and_cross_seeds():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        movie = Movie(title="Movie", sort_title="Movie", identity_state=IdentityState.MATCHED)
        db.add(movie); await db.flush()
        bare = MovieRelease(movie_id=movie.id, raw_release_name="Bare.2024.1080p")
        arriving = MovieRelease(movie_id=movie.id, raw_release_name="Arriving.2024.1080p")
        shared = MovieRelease(movie_id=movie.id, raw_release_name="Shared.2024.1080p")
        gone = MovieRelease(movie_id=movie.id, raw_release_name="Gone.2024.1080p")
        old = MovieRelease(movie_id=movie.id, raw_release_name="Old.2024.1080p", release_state=ReleaseState.REPLACED)
        db.add_all([bare, arriving, shared, gone, old])
        incoming = Torrent(info_hash="1" * 40, name="incoming")
        removed = Torrent(info_hash="2" * 40, name="removed from qBittorrent")
        seeding = Torrent(info_hash="3" * 40, name="seeding cross-seed")
        lonely = Torrent(info_hash="4" * 40, name="only one")
        db.add_all([incoming, removed, seeding, lonely]); await db.flush()
        db.add_all([
            MovieReleaseTorrent(movie_release_id=arriving.id, torrent_id=incoming.id, association_type=AssociationType.INCOMING),
            MovieReleaseTorrent(movie_release_id=shared.id, torrent_id=removed.id, association_type=AssociationType.ATTACHED),
            MovieReleaseTorrent(movie_release_id=shared.id, torrent_id=seeding.id, association_type=AssociationType.ATTACHED),
            MovieReleaseTorrent(movie_release_id=gone.id, torrent_id=lonely.id, association_type=AssociationType.ATTACHED),
            TorrentClientObservation(torrent_id=removed.id, download_client_id=CLIENT, is_present=False,
                                     removed_at=NOW - timedelta(days=3), last_seen_at=NOW - timedelta(days=3)),
            TorrentClientObservation(torrent_id=seeding.id, download_client_id=CLIENT, is_present=True, last_seen_at=NOW),
            TorrentClientObservation(torrent_id=lonely.id, download_client_id=CLIENT, is_present=False,
                                     removed_at=NOW - timedelta(days=3), last_seen_at=NOW - timedelta(days=3)),
        ])
        db.add(ReplacementReview(target_key="replace:x", ignored_reason=NO_TORRENT))
        await db.commit()

        problems, movies, shows = await release_problems(db)
        assert {release_id: item.problem.reason for release_id, item in problems.items()} == {
            bare.id: NO_TORRENT,
            gone.id: NOT_WATCHED,
        }
        assert movies == {movie.id} and shows == set()
        assert [torrent.id for torrent in problems[gone.id].torrents] == [lonely.id]
        # Scoped to other titles, nothing is evaluated.
        assert (await release_problems(db, movie_ids={uuid4()}, show_ids=set()))[0] == {}
    await engine.dispose()


def test_private_tracker_removal_wording_is_terminal():
    assert terminal_tracker_reason("Trumped by a newer encode") == "trumped"
    assert terminal_tracker_reason("Dupe of an existing upload") == "dupe"
    assert terminal_tracker_reason("Removed: source was retail-incorrect") == "removed"
    assert terminal_tracker_reason("timed out") is None
    assert terminal_tracker_reason("Connection refused") is None


def test_readd_needs_an_archived_torrent():
    def target(reason, *states):
        torrents = [SimpleNamespace(archive_state=state, archive_path="/archive/x.torrent" if state == TorrentArchiveState.ARCHIVED else None)
                    for state in states]
        return ReplacementTarget(target=None, torrents=torrents, releases=[], reason=reason)

    assert target(NOT_WATCHED, TorrentArchiveState.ARCHIVED).readdable
    assert target(NOT_WATCHED, TorrentArchiveState.NOT_ARCHIVED, TorrentArchiveState.ARCHIVED).readdable
    assert not target(NOT_WATCHED, TorrentArchiveState.NOT_ARCHIVED).readdable
    assert not target(NOT_WATCHED, TorrentArchiveState.PRUNED).readdable
    assert not target(NOT_WATCHED).readdable
    assert not target(REMOVED_FROM_TRACKER, TorrentArchiveState.ARCHIVED).readdable


def test_files_missing_after_a_readd_lists_the_title():
    item = torrent([observation()], [tracker()])
    item.readd_state = "files_missing"
    item.auto_readded_at = NOW - timedelta(hours=3)
    assert reason(item) == FILES_MISSING
    item.readd_state = "seeding"
    assert reason(item) is None


def test_readd_check_outcome_waits_for_a_finished_check():
    from app.services.auto_readd import FILES_MISSING as MISSING, SEEDING, check_outcome

    def readded(state, progress, seen_after):
        return SimpleNamespace(auto_readded_at=NOW, observations=[SimpleNamespace(
            is_present=True, state=state, progress=progress, last_seen_at=NOW + seen_after)])

    assert check_outcome(readded("stoppedUP", 1, timedelta(minutes=5)), NOW) == SEEDING
    assert check_outcome(readded("stoppedDL", 0.3, timedelta(minutes=5)), NOW) == MISSING
    assert check_outcome(readded("checkingUP", 0.3, timedelta(minutes=5)), NOW) is None
    # Seen right after the re-add, qBittorrent may not have started checking.
    assert check_outcome(readded("stoppedDL", 0, timedelta(seconds=30)), NOW) is None


def test_qbittorrent_reporting_missing_files_lists_files_missing():
    assert reason(torrent([observation(state="missingFiles")], [tracker()])) == FILES_MISSING


@pytest.mark.asyncio
async def test_media_gone_is_missing_or_files_missing_and_never_readdable():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        movie = Movie(title="Movie", sort_title="Movie", identity_state=IdentityState.MATCHED)
        db.add(movie); await db.flush()
        both_gone = MovieRelease(movie_id=movie.id, raw_release_name="Gone.2024.1080p", release_state=ReleaseState.MISSING)
        files_gone = MovieRelease(movie_id=movie.id, raw_release_name="Files.2024.1080p", release_state=ReleaseState.MISSING)
        db.add_all([both_gone, files_gone])
        removed = Torrent(info_hash="5" * 40, name="removed", archive_state=TorrentArchiveState.ARCHIVED, archive_path="x")
        present = Torrent(info_hash="6" * 40, name="present")
        db.add_all([removed, present]); await db.flush()
        db.add_all([
            MovieReleaseTorrent(movie_release_id=both_gone.id, torrent_id=removed.id, association_type=AssociationType.HISTORICAL),
            MovieReleaseTorrent(movie_release_id=files_gone.id, torrent_id=present.id, association_type=AssociationType.ATTACHED),
            TorrentClientObservation(torrent_id=removed.id, download_client_id=CLIENT, is_present=False,
                                     removed_at=NOW - timedelta(days=3), last_seen_at=NOW - timedelta(days=3)),
            TorrentClientObservation(torrent_id=present.id, download_client_id=CLIENT, is_present=True, last_seen_at=NOW),
        ])
        await db.commit()
        problems, _, _ = await release_problems(db)
        assert {release_id: item.problem.reason for release_id, item in problems.items()} == {
            both_gone.id: MISSING, files_gone.id: FILES_MISSING}
        # Missing media is never put back into qBittorrent.
        target = ReplacementTarget(target=None, torrents=problems[both_gone.id].torrents, releases=[], reason=MISSING)
        assert not target.readdable
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_cross_seed_on_the_same_files_protects_a_release_and_a_lone_removal_is_a_trump():
    from app.models.domain import TorrentTrackerObservation
    from app.services.payload_groups import GROUP_KEY
    from app.services.replacements import TRUMPED

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        movie = Movie(title="Movie", sort_title="Movie", identity_state=IdentityState.MATCHED)
        db.add(movie); await db.flush()
        releases = {name: MovieRelease(movie_id=movie.id, raw_release_name=f"{name}.2024.1080p")
                    for name in ("trumped", "lonely_but_shared", "all_dead")}
        db.add_all(releases.values()); await db.flush()

        def make(name, group, *, tracker_state, terminal=None, alone=None):
            item = Torrent(info_hash=uuid4().hex[:40], name=name, metadata_json={GROUP_KEY: group})
            db.add(item)
            return item, tracker_state, terminal, alone

        made = {
            # Linked and trumped on its tracker; an unlinked cross-seed of the files still seeds.
            "x_linked": make("X linked", "payload:a", tracker_state="error", terminal="trumped"),
            "x_cross": make("X cross-seed", "payload:a", tracker_state="working"),
            # Linked and alone for two weeks; its unlinked cross-seed has seeders.
            "y_linked": make("Y linked", "payload:b", tracker_state="working", alone=NOW - timedelta(days=15)),
            "y_cross": make("Y cross-seed", "payload:b", tracker_state="working"),
            # Both removed by their trackers: nothing left to restore from.
            "z_linked": make("Z linked", "payload:c", tracker_state="error", terminal="trumped"),
            "z_cross": make("Z cross-seed", "payload:c", tracker_state="error", terminal="unregistered"),
        }
        await db.flush()
        for item, state, terminal, alone in made.values():
            db.add(TorrentClientObservation(torrent_id=item.id, download_client_id=CLIENT, is_present=True, state="stalledUP",
                                            progress=1, last_seen_at=NOW, alone_since=alone))
            db.add(TorrentTrackerObservation(torrent_id=item.id, download_client_id=CLIENT, endpoint_key=item.info_hash,
                                             endpoint_display=f"https://{item.name.split()[0].lower()}.example/announce",
                                             health_state=state, terminal_reason=terminal, terminal_confirmed=terminal is not None,
                                             message="Trumped by a newer upload" if terminal == "trumped" else None,
                                             last_checked_at=NOW, first_seen_at=NOW - timedelta(days=30)))
        for release, key in ((releases["trumped"], "x_linked"), (releases["lonely_but_shared"], "y_linked"), (releases["all_dead"], "z_linked")):
            db.add(MovieReleaseTorrent(movie_release_id=release.id, torrent_id=made[key][0].id, association_type=AssociationType.ATTACHED))
        await db.commit()

        problems, _, _ = await release_problems(db)
        assert {release_id: item.problem.reason for release_id, item in problems.items()} == {
            releases["trumped"].id: TRUMPED,
            releases["all_dead"].id: REMOVED_FROM_TRACKER,
        }
        trumped = problems[releases["trumped"].id]
        # Only the removed torrent is the problem; the payload names both so a
        # search never offers the healthy cross-seed back.
        assert [item.name for item in trumped.torrents] == ["X linked"]
        assert {item.name for item in trumped.payload} == {"X linked", "X cross-seed"}
        assert {item.name for item in problems[releases["all_dead"].id].torrents} == {"Z linked", "Z cross-seed"}
    await engine.dispose()


def test_a_trumped_replacement_finishes_like_an_upgrade():
    from app.services.upgrade_execution import _finishes_like_upgrade

    assert _finishes_like_upgrade(SimpleNamespace(target_key="replace:movie-release:1", comparison_snapshot={"replacement_reason": "trumped"}))
    assert not _finishes_like_upgrade(SimpleNamespace(target_key="replace:movie-release:1", comparison_snapshot={"replacement_reason": "removed_from_tracker"}))
    assert not _finishes_like_upgrade(SimpleNamespace(target_key="movie-release:1", comparison_snapshot={"replacement_reason": "trumped"}))
