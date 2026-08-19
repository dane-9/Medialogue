from __future__ import annotations

import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.tmdb import TMDBClient, TMDBMovieMatch, TMDBShowDetails, TMDBShowMatch
from app.models.domain import Episode, PresenceState, Season, Severity, Show, TMDBConfiguration
from app.services.events import create_event

TMDBClientFactory = Callable[[str], TMDBClient]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


async def get_tmdb_configuration(db: AsyncSession) -> TMDBConfiguration | None:
    return await db.scalar(select(TMDBConfiguration).order_by(TMDBConfiguration.created_at).limit(1))


async def test_tmdb_connection(
    api_key: str,
    *,
    client_factory: TMDBClientFactory | None = None,
) -> dict[str, object]:
    started = perf_counter()
    factory = client_factory or TMDBClient
    client = factory(api_key)
    try:
        await client.health()
        return {"status": "healthy", "latency_ms": round((perf_counter() - started) * 1000)}
    finally:
        await client.close()


async def refresh_tmdb_health(
    db: AsyncSession,
    configuration: TMDBConfiguration,
    *,
    client_factory: TMDBClientFactory | None = None,
) -> dict[str, object]:
    previous = configuration.health
    configuration.last_checked_at = utcnow()
    try:
        result = await test_tmdb_connection(configuration.api_key, client_factory=client_factory)
    except Exception as exc:
        configuration.health = "unavailable"
        configuration.last_error = str(exc)
        configuration.latency_ms = None
        if previous != configuration.health:
            await create_event(
                db,
                "tmdb.health",
                entity_type="integration",
                entity_id=configuration.id,
                message="TMDB health changed to unavailable.",
                severity=Severity.ERROR,
                details={"status": "unavailable"},
            )
        return {"status": "unavailable", "message": str(exc)}
    configuration.health = "healthy"
    configuration.last_success_at = utcnow()
    configuration.last_error = None
    configuration.latency_ms = int(result["latency_ms"])
    if previous != configuration.health:
        await create_event(
            db,
            "tmdb.health",
            entity_type="integration",
            entity_id=configuration.id,
            message="TMDB health changed to healthy.",
            details={"status": "healthy"},
        )
    return result


async def resolve_movie_identity(
    db: AsyncSession,
    title: str,
    year: int | None,
    *,
    client_factory: TMDBClientFactory | None = None,
) -> tuple[TMDBMovieMatch | None, str]:
    """Resolve a parser candidate against TMDB without silently guessing.

    Returns `(match, reason)` where reason is one of matched/not_configured/
    unavailable/not_found/ambiguous. Exact normalized title + year candidates
    are preferred. A missing TMDB configuration never becomes implicit local
    authority for a newly discovered title.
    """

    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        return None, "not_configured"
    factory = client_factory or TMDBClient
    client = factory(configuration.api_key)
    configuration.last_checked_at = utcnow()
    try:
        matches = await client.search_movie(title, year)
        configuration.health = "healthy"
        configuration.last_success_at = utcnow()
        configuration.last_error = None
    except Exception as exc:
        configuration.health = "unavailable"
        configuration.last_error = str(exc)
        return None, "unavailable"
    finally:
        await client.close()

    target = _normalized_title(title)
    exact = [
        item
        for item in matches
        if target in {_normalized_title(item.title), _normalized_title(item.original_title or "")}
        and (year is None or item.year is None or item.year == year)
    ]
    if len(exact) == 1:
        return exact[0], "matched"
    if len(exact) > 1:
        with_year = [item for item in exact if year is not None and item.year == year]
        if len(with_year) == 1:
            return with_year[0], "matched"
        return None, "ambiguous"
    return None, "not_found"


async def resolve_show_identity(
    db: AsyncSession,
    title: str,
    year: int | None,
    *,
    client_factory: TMDBClientFactory | None = None,
) -> tuple[TMDBShowMatch | None, str]:
    """Resolve a show candidate against TMDB with the same no-guess policy as Movies."""

    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        return None, "not_configured"
    factory = client_factory or TMDBClient
    client = factory(configuration.api_key)
    configuration.last_checked_at = utcnow()
    try:
        matches = await client.search_show(title, year)
        configuration.health = "healthy"
        configuration.last_success_at = utcnow()
        configuration.last_error = None
    except Exception as exc:
        configuration.health = "unavailable"
        configuration.last_error = str(exc)
        return None, "unavailable"
    finally:
        await client.close()

    target = _normalized_title(title)
    exact = [
        item
        for item in matches
        if target in {_normalized_title(item.title), _normalized_title(item.original_title or "")}
        and (year is None or item.year is None or item.year == year)
    ]
    if len(exact) == 1:
        return exact[0], "matched"
    if len(exact) > 1:
        with_year = [item for item in exact if year is not None and item.year == year]
        if len(with_year) == 1:
            return with_year[0], "matched"
        return None, "ambiguous"
    return None, "not_found"


async def get_show_details(
    db: AsyncSession,
    tmdb_id: int,
    *,
    client_factory: TMDBClientFactory | None = None,
) -> TMDBShowDetails | None:
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        return None
    factory = client_factory or TMDBClient
    client = factory(configuration.api_key)
    configuration.last_checked_at = utcnow()
    try:
        details = await client.get_show(tmdb_id)
        configuration.health = "healthy"
        configuration.last_success_at = utcnow()
        configuration.last_error = None
        return details
    except Exception as exc:
        configuration.health = "unavailable"
        configuration.last_error = str(exc)
        return None
    finally:
        await client.close()


async def sync_show_metadata(
    db: AsyncSession,
    show: Show,
    *,
    client_factory: TMDBClientFactory | None = None,
) -> dict[str, int]:
    """Refresh show/season/episode metadata without changing media presence.

    TMDB remains the primary metadata provider.  The TVDB identifier is stored
    from TMDB's external-IDs response as supporting identity evidence; it is
    never used as the primary application resource ID.
    """

    if show.tmdb_id is None:
        return {"seasons": 0, "episodes": 0}
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        return {"seasons": 0, "episodes": 0}
    factory = client_factory or TMDBClient
    client = factory(configuration.api_key)
    configuration.last_checked_at = utcnow()
    try:
        details = await client.get_show(show.tmdb_id)
        show.title = details.title
        show.year = details.year
        show.overview = details.overview
        show.poster_ref = details.poster_path
        show.tvdb_id = details.tvdb_id
        show.metadata_refreshed_at = utcnow()
        season_count = episode_count = 0
        for season_meta in details.seasons:
            # Specials (season 0) are valid metadata. They remain independently
            # monitorable just like numbered seasons.
            season = await db.scalar(
                select(Season).where(Season.show_id == show.id, Season.season_number == season_meta.season_number)
            )
            if season is None:
                season = Season(
                    show_id=show.id,
                    season_number=season_meta.season_number,
                    title=season_meta.title,
                    metadata_json={},
                )
                db.add(season)
                await db.flush()
            else:
                season.title = season_meta.title
            season.metadata_json = {
                **dict(season.metadata_json or {}),
                "episode_count": season_meta.episode_count,
                "air_date": season_meta.air_date.isoformat() if season_meta.air_date else None,
                "poster_path": season_meta.poster_path,
                "provider": "tmdb",
            }
            season_count += 1
            try:
                episode_metadata = await client.get_season(show.tmdb_id, season_meta.season_number)
            except Exception:
                # One unavailable season must not discard metadata already
                # refreshed for the rest of the show.
                continue
            for item in episode_metadata:
                episode = await db.scalar(
                    select(Episode).where(
                        Episode.show_id == show.id,
                        Episode.season_number == item.season_number,
                        Episode.episode_number == item.episode_number,
                    )
                )
                if episode is None:
                    episode = Episode(
                        show_id=show.id,
                        season_id=season.id,
                        season_number=item.season_number,
                        episode_number=item.episode_number,
                        presence_state=PresenceState.MISSING,
                    )
                    db.add(episode)
                episode.season_id = season.id
                episode.title = item.title
                episode.air_date = item.air_date
                episode.tmdb_id = item.tmdb_id
                episode.metadata_json = {
                    **dict(episode.metadata_json or {}),
                    "overview": item.overview,
                    "provider": "tmdb",
                }
                episode_count += 1
        configuration.health = "healthy"
        configuration.last_success_at = utcnow()
        configuration.last_error = None
        await db.flush()
        return {"seasons": season_count, "episodes": episode_count}
    except Exception as exc:
        configuration.health = "unavailable"
        configuration.last_error = str(exc)
        raise
    finally:
        await client.close()
