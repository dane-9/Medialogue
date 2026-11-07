"""Why a title is in Replace Media: one test per reason, and the safeguards."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.domain import (AssociationType, IdentityState, Movie, MovieRelease, MovieReleaseTorrent, ReleaseState,
                               ReplacementReview, Torrent, TorrentClientObservation)
from app.services.replacements import (NO_SEEDERS, NO_TORRENT, NOT_WATCHED, REMOVED_FROM_TRACKER, TRACKER_GONE,
                                       _dismissed, classify_torrent, release_problems)
from app.services.torrent_tracking import other_seeders, terminal_tracker_reason, track_other_seeders

NOW = datetime.now(timezone.utc)
CLIENT = uuid4()


def observation(*, present=True, removed_at=None, alone_since=None, client=CLIENT):
    return SimpleNamespace(is_present=present, removed_at=removed_at, last_seen_at=removed_at or NOW,
                           alone_since=alone_since, download_client_id=client)


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
