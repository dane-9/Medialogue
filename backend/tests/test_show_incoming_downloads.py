"""A show's detail lists the downloads on their way, each with its season."""

import asyncio

from app.api.shows import _incoming_downloads
from app.db import session as db_session
from app.models.domain import (
    AssociationType, ReleaseScope, ReleaseState, Season, Show, ShowRelease, ShowReleaseTorrent, Torrent,
)
from tests.test_upgrade_candidates import client  # noqa: F401


def test_a_season_pack_on_its_way_is_listed_under_its_season(client):  # noqa: F811
    async def scenario():
        async with db_session.async_session_factory() as db:
            show = Show(title="Severance", year=2022, tmdb_id=95396, monitored=True)
            db.add(show)
            await db.flush()
            season = Season(show_id=show.id, season_number=2)
            torrent = Torrent(info_hash="ab" * 20, name="Severance.S02.2160p.ATVP.WEB-DL-GRP")
            db.add_all([season, torrent])
            await db.flush()
            release = ShowRelease(show_id=show.id, season_id=season.id, raw_release_name=torrent.name,
                                  release_scope=ReleaseScope.SEASON_PACK, release_state=ReleaseState.MISSING,
                                  parse_snapshot={"incoming": True})
            db.add(release)
            await db.flush()
            db.add(ShowReleaseTorrent(show_release_id=release.id, torrent_id=torrent.id,
                                      association_type=AssociationType.INCOMING))
            await db.commit()

            incoming = await _incoming_downloads(db, show)
            assert [(item["season_number"], item["name"], item["torrent_id"]) for item in incoming] == [
                (2, torrent.name, str(torrent.id))]
            assert incoming[0]["state"] == "incoming" and incoming[0]["progress"] == 0

    asyncio.run(scenario())
