"""A show's page lists every torrent its releases were tied to, as a movie's does."""

import asyncio

from app.db import session as db_session
from app.models.domain import ReleaseScope, ReleaseState, Season, Show, ShowRelease, ShowReleaseTorrent, Torrent
from tests.test_libraries import client, login  # noqa: F401  (client is a fixture)


def test_a_show_lists_the_torrents_of_its_season_packs_and_episode_releases(client):
    headers = login(client)

    async def seed():
        async with db_session.async_session_factory() as db:
            show = Show(title="Example", year=2020)
            db.add(show)
            await db.flush()
            season = Season(show_id=show.id, season_number=1)
            db.add(season)
            await db.flush()
            for name, scope, hash_char in (("Example.S01.1080p", ReleaseScope.SEASON_PACK, "a"),
                                           ("Example.S01E02.2160p", ReleaseScope.EPISODE, "b")):
                release = ShowRelease(show_id=show.id, season_id=season.id, raw_release_name=name,
                                      release_scope=scope, release_state=ReleaseState.CURRENT)
                torrent = Torrent(info_hash=hash_char * 40, name=name)
                db.add_all([release, torrent])
                await db.flush()
                db.add(ShowReleaseTorrent(show_release_id=release.id, torrent_id=torrent.id))
            await db.commit()
            return show.id

    show_id = asyncio.run(seed())
    detail = client.get(f"/api/v1/shows/{show_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    history = detail.json()["torrent_history"]
    assert {item["release_name"] for item in history} == {"Example.S01.1080p", "Example.S01E02.2160p"}
    assert {item["info_hash"] for item in history} == {"a" * 40, "b" * 40}
    assert all(item["qbit_present"] is False for item in history)
