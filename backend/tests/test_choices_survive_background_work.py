"""Choices made by hand outlive the background work that revisits them."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session as db_session
from app.integrations.tmdb import TMDBMovieMatch
from app.models.domain import (
    MediaDirectory, MediaDiscovery, MediaType, Movie, MovieRelease, ReleaseState, Torrent,
)
from app.parser import parse_release
from app.services.discoveries import stage_media_discovery
from app.services.tmdb import TMDBMovieIdentityResolution
from app.services.torrent_reconciliation import associate_incoming_torrent
from tests.test_upgrade_candidates import client  # noqa: F401


def _match(tmdb_id: int) -> TMDBMovieMatch:
    return TMDBMovieMatch(tmdb_id=tmdb_id, title=f"Heat {tmdb_id}", original_title=None, year=1995, overview=None, poster_path=None)


def test_a_confirmed_new_media_match_survives_a_rescan_with_another_suggestion(client):  # noqa: F811
    async def scenario():
        async with db_session.async_session_factory() as db:
            directory = MediaDirectory(resolved_path="/movies/Heat.1995", reported_path="/movies/Heat.1995", exists=True)
            db.add(directory)
            await db.flush()
            parse = parse_release("Heat.1995.1080p.BluRay.x264-GRP")
            first = TMDBMovieIdentityResolution(match=_match(1), reason="matched", candidates=(_match(1), _match(2)))
            discovery = await stage_media_discovery(db, directory, parse, media_type=MediaType.MOVIES, title="Heat",
                                                    year=1995, identity_resolution=first)
            assert discovery.selected_tmdb_id == 1
            # The user confirms the suggested match.
            discovery.selected_at = datetime.now(timezone.utc)
            await db.flush()

            later = TMDBMovieIdentityResolution(match=_match(2), reason="matched", candidates=(_match(2), _match(1)))
            again = await stage_media_discovery(db, directory, parse, media_type=MediaType.MOVIES, title="Heat",
                                                year=1995, identity_resolution=later)
            assert again.selected_tmdb_id == 1 and again.suggested_tmdb_id == 2

    asyncio.run(scenario())


def test_a_replacement_keeps_the_edition_set_by_hand_when_its_name_names_none(client):  # noqa: F811
    async def scenario():
        async with db_session.async_session_factory() as db:
            movie = Movie(title="Heat", sort_title="heat", year=1995, tmdb_id=949, monitored=True)
            db.add(movie)
            await db.flush()
            lost = MovieRelease(movie_id=movie.id, raw_release_name="Heat.1995.1080p.BluRay.x264-OLD",
                                release_state=ReleaseState.MISSING, edition_override_active=True,
                                manual_edition_override="Director's Definitive Edition",
                                effective_edition="Director's Definitive Edition", parse_snapshot={})
            torrent = Torrent(info_hash="ab" * 20, name="Heat.1995.1080p.BluRay.x264-NEW")
            db.add_all([lost, torrent])
            await db.flush()

            result = await associate_incoming_torrent(db, torrent, resolved_path=None, scope=MediaType.MOVIES, complete=False)
            assert result is not None
            incoming = await db.get(MovieRelease, result["release_id"])
            assert incoming.edition_override_active is True
            assert incoming.effective_edition == "Director's Definitive Edition"
            assert incoming.parse_snapshot["replacement_of_release_id"] == str(lost.id)

    asyncio.run(scenario())
