"""A newly found season or episode follows the monitoring the user set."""

import asyncio

from app.db import session as db_session
from app.models.domain import Show
from app.services.shows import _ensure_episode_for_mapping, _ensure_season
from tests.test_upgrade_candidates import client  # noqa: F401


def test_new_seasons_and_episodes_follow_what_is_monitored(client):  # noqa: F811
    async def scenario():
        async with db_session.async_session_factory() as db:
            show = Show(title="Severance", year=2022, tmdb_id=95396, monitored=True)
            db.add(show)
            await db.flush()
            season = await _ensure_season(db, show, 1)
            season.monitored = False
            # An episode turning up in a season the user stopped monitoring.
            episode = await _ensure_episode_for_mapping(db, show, season, 9)
            assert episode is not None and episode.monitored is False

            show.monitored = False
            later = await _ensure_season(db, show, 2)
            assert later.monitored is False

    asyncio.run(scenario())
