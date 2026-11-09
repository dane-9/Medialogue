"""Finding upgrades from trackers' new-release feeds, and the order of full searches."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from app.db import session as db_session
from app.integrations.torznab import SearchResult
from app.models.domain import (
    AccessMode,
    IdentityState,
    Job,
    JobStatus,
    MediaDirectory,
    MediaType,
    Movie,
    MovieRelease,
    ReleaseBlocklist,
    StorageRoot,
    UpgradeCandidate,
    UpgradeSearchMark,
)
from app.services.release_feed import run_new_release_check
from app.services.upgrade_candidates import run_upgrade_search_job
from tests.test_upgrade_candidates import _create_indexer, _login, client  # noqa: F401


def _release(title: str, guid: str | None = None) -> SearchResult:
    return SearchResult(title=title, guid=guid or title, download_url=f"http://upgrade-indexer.test/{guid or title}",
                        size=10, seeders=20, published=None, leechers=1)


class _Tracker:
    def __init__(self, feed=(), *, fails=False):
        self.feed = list(feed)
        self.fails = fails
        self.feed_requests: list[tuple[str, tuple[int, ...]]] = []
        self.searched: list[str] = []

    def factory(self, *_args, **_kwargs):
        return self

    async def recent(self, *, media_type, categories):
        self.feed_requests.append((media_type, categories))
        if self.fails:
            raise RuntimeError("tracker is down")
        return list(self.feed) if media_type == "movies" else []

    async def search(self, query, **_kwargs):
        self.searched.append(query)
        return []

    async def close(self):
        return None


async def _seed_movies(*titles) -> dict[str, UUID]:
    ids: dict[str, UUID] = {}
    async with db_session.async_session_factory() as db:
        root = StorageRoot(name="Movies", resolved_root_path="/media/movies", media_type=MediaType.MOVIES, access_mode=AccessMode.READ_WRITE)
        db.add(root)
        await db.flush()
        for number, title in enumerate(titles):
            movie = Movie(title=title, sort_title=title, year=2024, tmdb_id=1000 + number, identity_state=IdentityState.MATCHED)
            db.add(movie)
            await db.flush()
            dotted = title.replace(" ", ".")
            release = MovieRelease(movie_id=movie.id, raw_release_name=f"{title} (2024)", tracker_title=f"{dotted}.2024.1080p.WEB-DL.x264-GRP")
            db.add(release)
            await db.flush()
            db.add(MediaDirectory(storage_root_id=root.id, resolved_path=f"/media/movies/{title} (2024)", movie_release_id=release.id))
            ids[title] = movie.id
        await db.commit()
    return ids


async def _job(job_type: str) -> UUID:
    async with db_session.async_session_factory() as db:
        job = Job(job_type=job_type, status=JobStatus.QUEUED, cancellable=False, summary={})
        db.add(job)
        await db.commit()
        return job.id


def _check(tracker: _Tracker) -> Job:
    async def run():
        job_id = await _job("check_new_releases")
        await run_new_release_check(job_id, client_factory=tracker.factory)
        async with db_session.async_session_factory() as db:
            return await db.get(Job, job_id)

    return asyncio.run(run())


async def _rows():
    async with db_session.async_session_factory() as db:
        return list((await db.scalars(select(UpgradeCandidate))).all())


def test_a_new_release_is_offered_for_the_title_it_names(client) -> None:  # noqa: F811
    _create_indexer(client, _login(client))
    ids = asyncio.run(_seed_movies("Upgrade Movie", "Quiet Movie"))
    tracker = _Tracker([
        _release("Upgrade.Movie.2024.2160p.WEB-DL.x264-GRP"),
        _release("Upgrade.Movie.1999.2160p.WEB-DL.x264-GRP"),  # same name, another film
        _release("Not.In.The.Library.2024.2160p.WEB-DL.x264-GRP"),
    ])
    job = _check(tracker)
    assert job.status == JobStatus.COMPLETED
    assert job.summary["releases_seen"] == 3
    assert job.summary["upgrades_offered"] == 1
    rows = asyncio.run(_rows())
    assert [(row.movie_id, row.title) for row in rows] == [(ids["Upgrade Movie"], "Upgrade.Movie.2024.2160p.WEB-DL.x264-GRP")]
    # Nothing is searched per title; a tracker with no categories gets the standard one.
    assert tracker.searched == []
    assert ("movies", (2000,)) in tracker.feed_requests and ("shows", (5000,)) in tracker.feed_requests


def test_a_better_release_replaces_the_offer_and_a_lesser_one_does_not(client) -> None:  # noqa: F811
    _create_indexer(client, _login(client))
    asyncio.run(_seed_movies("Upgrade Movie"))
    _check(_Tracker([_release("Upgrade.Movie.2024.1440p.WEB-DL.x264-GRP")]))
    assert asyncio.run(_rows())[0].quality == "1440p WEB-DL"

    _check(_Tracker([_release("Upgrade.Movie.2024.2160p.WEB-DL.x264-GRP")]))
    row = asyncio.run(_rows())[0]
    assert row.quality == "2160p WEB-DL"

    _check(_Tracker([_release("Upgrade.Movie.2024.1440p.WEB-DL.x264-OTHER")]))
    row = asyncio.run(_rows())[0]
    assert row.quality == "2160p WEB-DL"
    assert "Upgrade.Movie.2024.1440p.WEB-DL.x264-OTHER" in [item["title"] for item in row.search_results]


def test_an_offer_survives_a_feed_with_nothing_for_it(client) -> None:  # noqa: F811
    _create_indexer(client, _login(client))
    asyncio.run(_seed_movies("Upgrade Movie"))
    _check(_Tracker([_release("Upgrade.Movie.2024.2160p.WEB-DL.x264-GRP")]))
    _check(_Tracker([]))
    assert len(asyncio.run(_rows())) == 1


def test_a_blocklisted_release_is_not_offered(client) -> None:  # noqa: F811
    _create_indexer(client, _login(client))
    ids = asyncio.run(_seed_movies("Upgrade Movie"))
    title = "Upgrade.Movie.2024.2160p.WEB-DL.x264-GRP"

    async def block():
        async with db_session.async_session_factory() as db:
            db.add(ReleaseBlocklist(media_type=MediaType.MOVIES, target_entity_type="movie", target_entity_id=ids["Upgrade Movie"],
                                    source_title=title, indexer_name="Upgrade Indexer", reason="Not an upgrade"))
            await db.commit()

    asyncio.run(block())
    _check(_Tracker([_release(title)]))
    assert asyncio.run(_rows()) == []


def test_the_check_fails_only_when_no_tracker_answers(client) -> None:  # noqa: F811
    _create_indexer(client, _login(client))
    job = _check(_Tracker(fails=True))
    assert job.status == JobStatus.FAILED
    assert job.error["code"] == "NEW_RELEASE_CHECK_FAILED"


def _search(tracker: _Tracker) -> None:
    async def run():
        await run_upgrade_search_job(await _job("search_media_upgrades"), client_factory=tracker.factory)

    asyncio.run(run())


def test_the_full_search_goes_longest_unsearched_first_and_remembers(client) -> None:  # noqa: F811
    _create_indexer(client, _login(client))
    asyncio.run(_seed_movies("Alpha Movie", "Beta Movie", "Gamma Movie"))
    first = _Tracker()
    _search(first)
    assert len(first.searched) == 3

    async def age():
        async with db_session.async_session_factory() as db:
            marks = {mark.target_key: mark for mark in (await db.scalars(select(UpgradeSearchMark))).all()}
            assert len(marks) == 3
            return marks

    marks = asyncio.run(age())

    async def set_ages():
        now = datetime.now(timezone.utc)
        async with db_session.async_session_factory() as db:
            rows = (await db.scalars(select(UpgradeSearchMark).order_by(UpgradeSearchMark.target_key))).all()
            for days, mark in zip((1, 30, 7), rows):
                mark.searched_at = now - timedelta(days=days)
            await db.commit()
            return [mark.target_key for mark in rows]

    keys = asyncio.run(set_ages())
    assert set(keys) == set(marks)
    second = _Tracker()
    _search(second)

    async def order_of_marks():
        async with db_session.async_session_factory() as db:
            rows = (await db.scalars(select(UpgradeSearchMark).order_by(UpgradeSearchMark.searched_at))).all()
            return [mark.target_key for mark in rows]

    assert asyncio.run(order_of_marks()) == [keys[1], keys[2], keys[0]]
