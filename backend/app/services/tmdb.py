from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import normalize_identity_title
from app.core.integration_config import get_integration_config_store
from app.integrations.tmdb import (
    TMDBClient,
    TMDBEpisodeMetadata,
    TMDBMovieMatch,
    TMDBSeasonMetadata,
    TMDBShowDetails,
    TMDBShowMatch,
)
from app.models.domain import Episode, JobStatus, PresenceState, Season, Severity, Show
from app.services.events import create_event
from app.services.integration_state import ConfiguredTMDB, get_configured_tmdb
from app.services.jobs import JobFailure, checkpoint_job, run_job

TMDBClientFactory = Callable[[str], TMDBClient]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class TMDBMovieIdentityResolution:
    match: TMDBMovieMatch | None
    reason: str
    candidates: tuple[TMDBMovieMatch, ...] = ()
    queries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TMDBShowIdentityResolution:
    match: TMDBShowMatch | None
    reason: str
    candidates: tuple[TMDBShowMatch, ...] = ()
    queries: tuple[str, ...] = ()


def _title_query_variants(title: str) -> tuple[str, ...]:
    """Return conservative query spellings for punctuation/ampersand variants."""

    variants = [title]
    if " & " in title:
        variants.append(title.replace(" & ", " and "))
    if " and " in title.casefold():
        variants.append(title.replace(" and ", " & ").replace(" And ", " & "))
    # Preserve order while avoiding duplicate API calls.
    return tuple(dict.fromkeys(value.strip() for value in variants if value.strip()))


def _select_movie_identity(
    title: str,
    year: int | None,
    matches: list[TMDBMovieMatch] | tuple[TMDBMovieMatch, ...],
) -> tuple[TMDBMovieMatch | None, str, tuple[TMDBMovieMatch, ...]]:
    target = normalize_identity_title(title)
    exact = tuple(
        item
        for item in matches
        if target in {
            normalize_identity_title(item.title),
            normalize_identity_title(item.original_title),
        }
        and (year is None or item.year is None or item.year == year)
    )
    if len(exact) == 1:
        return exact[0], "matched", exact
    if len(exact) > 1:
        with_year = tuple(item for item in exact if year is not None and item.year == year)
        if len(with_year) == 1:
            return with_year[0], "matched", exact
        return None, "ambiguous", exact
    return None, "not_found", tuple(matches)


async def _select_movie_by_alternative_title(
    client: object,
    title: str,
    year: int | None,
    matches: list[TMDBMovieMatch],
) -> tuple[TMDBMovieMatch | None, str | None]:
    """Use TMDB-owned alternate-title evidence without fuzzy guessing."""

    loader = getattr(client, "get_movie_alternative_titles", None)
    if not callable(loader):
        return None, None
    target = normalize_identity_title(title)
    alias_matches: list[TMDBMovieMatch] = []
    for item in matches[:8]:
        if year is not None and item.year is not None and item.year != year:
            continue
        aliases = await loader(item.tmdb_id)
        if any(normalize_identity_title(alias) == target for alias in aliases):
            alias_matches.append(item)
    if len(alias_matches) == 1:
        return alias_matches[0], "matched"
    if len(alias_matches) > 1:
        with_year = [item for item in alias_matches if year is not None and item.year == year]
        if len(with_year) == 1:
            return with_year[0], "matched"
        return None, "ambiguous"
    return None, None


class TMDBUnavailable(RuntimeError):
    """TMDB could not be reached while identity was required.

    This is an infrastructure state, not a property of any one title, so it is
    raised once and allowed to fail the surrounding job. Recording it against
    every directory instead would turn a single outage into thousands of
    individually actionable Problems that one retry resolves.
    """


async def get_tmdb_configuration(db: AsyncSession) -> ConfiguredTMDB | None:
    return await get_configured_tmdb(db)


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
    configuration: ConfiguredTMDB,
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

    resolution = await resolve_movie_identity_detailed(
        db,
        title,
        year,
        client_factory=client_factory,
    )
    return resolution.match, resolution.reason


async def resolve_movie_identity_detailed(
    db: AsyncSession,
    title: str,
    year: int | None,
    *,
    client_factory: TMDBClientFactory | None = None,
) -> TMDBMovieIdentityResolution:
    """Resolve a Movie and retain useful failure evidence for the Problems UI.

    TMDB's ``year`` filter and punctuation handling are useful but not
    authoritative.  We first search the parsed title/year, then retry harmless
    title variants and finally the same query without the year filter when no
    exact identity was returned. A candidate is still accepted only when its
    normalized title and year evidence agree; fallback searches never lower
    that identity requirement.
    """

    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        return TMDBMovieIdentityResolution(None, "not_configured")
    factory = client_factory or TMDBClient
    client = factory(configuration.api_key)
    configuration.last_checked_at = utcnow()
    queries: list[str] = []
    collected: dict[int, TMDBMovieMatch] = {}
    try:
        for query_title in _title_query_variants(title):
            query_label = f"{query_title} ({year})" if year is not None else query_title
            queries.append(query_label)
            for item in await client.search_movie(query_title, year):
                collected[item.tmdb_id] = item
            match, reason, evidence = _select_movie_identity(title, year, list(collected.values()))
            if match is not None or reason == "ambiguous":
                configuration.health = "healthy"
                configuration.last_success_at = utcnow()
                configuration.last_error = None
                return TMDBMovieIdentityResolution(match, reason, evidence, tuple(queries))

        # A TMDB year filter can occasionally exclude a valid result when its
        # release-date metadata differs from the filename convention. Retry
        # without the filter, but still require the returned candidate's year
        # to agree before it can be selected automatically.
        if year is not None:
            for query_title in _title_query_variants(title):
                queries.append(query_title)
                for item in await client.search_movie(query_title, None):
                    collected[item.tmdb_id] = item
                match, reason, evidence = _select_movie_identity(title, year, list(collected.values()))
                if match is not None or reason == "ambiguous":
                    configuration.health = "healthy"
                    configuration.last_success_at = utcnow()
                    configuration.last_error = None
                    return TMDBMovieIdentityResolution(match, reason, evidence, tuple(queries))

        alias_match, alias_reason = await _select_movie_by_alternative_title(
            client,
            title,
            year,
            list(collected.values()),
        )
        if alias_match is not None or alias_reason == "ambiguous":
            configuration.health = "healthy"
            configuration.last_success_at = utcnow()
            configuration.last_error = None
            return TMDBMovieIdentityResolution(
                alias_match,
                alias_reason or "matched",
                tuple(collected.values()),
                tuple(queries),
            )

        configuration.health = "healthy"
        configuration.last_success_at = utcnow()
        configuration.last_error = None
    except Exception as exc:
        configuration.health = "unavailable"
        configuration.last_error = str(exc)
        return TMDBMovieIdentityResolution(None, "unavailable", tuple(collected.values()), tuple(queries))
    finally:
        await client.close()
    match, reason, evidence = _select_movie_identity(title, year, list(collected.values()))
    return TMDBMovieIdentityResolution(match, reason, evidence, tuple(queries))


async def resolve_show_identity(
    db: AsyncSession,
    title: str,
    year: int | None,
    *,
    client_factory: TMDBClientFactory | None = None,
) -> tuple[TMDBShowMatch | None, str]:
    """Resolve a show candidate against TMDB with the same no-guess policy as Movies."""

    resolution = await resolve_show_identity_detailed(
        db,
        title,
        year,
        client_factory=client_factory,
    )
    return resolution.match, resolution.reason


def _select_show_identity(
    title: str,
    year: int | None,
    matches: list[TMDBShowMatch] | tuple[TMDBShowMatch, ...],
) -> tuple[TMDBShowMatch | None, str, tuple[TMDBShowMatch, ...]]:
    target = normalize_identity_title(title)
    exact = tuple(
        item
        for item in matches
        if target in {
            normalize_identity_title(item.title),
            normalize_identity_title(item.original_title),
        }
        and (year is None or item.year is None or item.year == year)
    )
    if len(exact) == 1:
        return exact[0], "matched", exact
    if len(exact) > 1:
        with_year = tuple(item for item in exact if year is not None and item.year == year)
        if len(with_year) == 1:
            return with_year[0], "matched", exact
        return None, "ambiguous", exact
    return None, "not_found", tuple(matches)


async def resolve_show_identity_detailed(
    db: AsyncSession,
    title: str,
    year: int | None,
    *,
    client_factory: TMDBClientFactory | None = None,
) -> TMDBShowIdentityResolution:
    """Resolve a Show while retaining candidates for manual Problem resolution."""

    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        return TMDBShowIdentityResolution(None, "not_configured")
    factory = client_factory or TMDBClient
    client = factory(configuration.api_key)
    configuration.last_checked_at = utcnow()
    queries: list[str] = []
    collected: dict[int, TMDBShowMatch] = {}
    try:
        for query_title in _title_query_variants(title):
            queries.append(f"{query_title} ({year})" if year is not None else query_title)
            for item in await client.search_show(query_title, year):
                collected[item.tmdb_id] = item
            match, reason, evidence = _select_show_identity(title, year, list(collected.values()))
            if match is not None or reason == "ambiguous":
                configuration.health = "healthy"
                configuration.last_success_at = utcnow()
                configuration.last_error = None
                return TMDBShowIdentityResolution(match, reason, evidence, tuple(queries))

        # TMDB's first-air-year filter can omit the intended series. Retry the
        # title without that filter, while retaining the same strict year check
        # before an automatic match is accepted.
        if year is not None:
            for query_title in _title_query_variants(title):
                queries.append(query_title)
                for item in await client.search_show(query_title, None):
                    collected[item.tmdb_id] = item
                match, reason, evidence = _select_show_identity(title, year, list(collected.values()))
                if match is not None or reason == "ambiguous":
                    configuration.health = "healthy"
                    configuration.last_success_at = utcnow()
                    configuration.last_error = None
                    return TMDBShowIdentityResolution(match, reason, evidence, tuple(queries))

        configuration.health = "healthy"
        configuration.last_success_at = utcnow()
        configuration.last_error = None
    except Exception as exc:
        configuration.health = "unavailable"
        configuration.last_error = str(exc)
        return TMDBShowIdentityResolution(
            None,
            "unavailable",
            tuple(collected.values()),
            tuple(queries),
        )
    finally:
        await client.close()
    match, reason, evidence = _select_show_identity(title, year, list(collected.values()))
    return TMDBShowIdentityResolution(match, reason, evidence, tuple(queries))


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

        # A show can follow one of TMDB's episode groups instead of the default
        # season structure. The group holds the same episodes, rearranged, so
        # this changes how they are numbered rather than which ones exist.
        grouped: dict[int, tuple[str | None, tuple[TMDBEpisodeMetadata, ...]]] | None = None
        season_plan = list(details.seasons)
        if show.tmdb_episode_group_id:
            try:
                group = await client.get_episode_group(show.tmdb_episode_group_id)
            except Exception:
                # An unreachable group must not wipe a show's structure; keep
                # whatever is already stored and try again on the next refresh.
                group = None
            if group is not None and group.seasons:
                grouped = {number: (title, episodes) for number, title, episodes in group.seasons}
                season_plan = [
                    TMDBSeasonMetadata(
                        season_number=number,
                        title=title,
                        episode_count=len(episodes),
                        air_date=next((item.air_date for item in episodes if item.air_date), None),
                        poster_path=None,
                    )
                    for number, title, episodes in group.seasons
                ]

        # Renumbering in place would trip uq_episodes_show_season_number the
        # moment an episode takes a slot another one still holds. Park every
        # existing episode on a negative number first; the mapping is injective,
        # so the parked values cannot collide either. Final numbers are written
        # below. This runs in both directions, because switching back to the
        # default structure renumbers just as much as switching away from it.
        existing = (await db.scalars(select(Episode).where(Episode.show_id == show.id))).all()
        for episode in existing:
            episode.season_number = -1000 - abs(episode.season_number)
            episode.episode_number = -abs(episode.episode_number)
        if existing:
            await db.flush()

        season_count = episode_count = 0
        for season_meta in season_plan:
            # Specials (season 0) are valid metadata. They remain independently
            # monitorable just like numbered seasons.
            season = await db.scalar(
                select(Season).where(Season.show_id == show.id, Season.season_number == season_meta.season_number)
            )
            if season is None:
                # Season 0 follows the library-wide Specials preference, so a
                # newly added show matches how the rest of the library counts.
                counted = season_meta.season_number != 0 or get_integration_config_store().get_count_specials()
                season = Season(
                    show_id=show.id,
                    season_number=season_meta.season_number,
                    title=season_meta.title,
                    counted=counted,
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
            if grouped is not None:
                episode_metadata = list(grouped.get(season_meta.season_number, (None, ()))[1])
            else:
                try:
                    episode_metadata = await client.get_season(show.tmdb_id, season_meta.season_number)
                except Exception:
                    # One unavailable season must not discard metadata already
                    # refreshed for the rest of the show.
                    continue
            for item in episode_metadata:
                # TMDB's episode id is the stable identity. Matching on it first
                # means switching ordering renumbers the existing rows instead of
                # creating duplicates and stranding their file mappings.
                episode = None
                if item.tmdb_id is not None:
                    episode = await db.scalar(
                        select(Episode).where(Episode.show_id == show.id, Episode.tmdb_id == item.tmdb_id)
                    )
                if episode is None:
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
                episode.season_number = item.season_number
                episode.episode_number = item.episode_number
                episode.title = item.title
                episode.air_date = item.air_date
                episode.tmdb_id = item.tmdb_id
                episode.metadata_json = {
                    **dict(episode.metadata_json or {}),
                    "overview": item.overview,
                    "provider": "tmdb",
                }
                episode_count += 1
        # An ordering with fewer seasons leaves the surplus behind, in either
        # direction. Drop the ones nothing landed in; a season still holding
        # episodes is never touched, so no mapped media can be lost here.
        planned = {season_meta.season_number for season_meta in season_plan}
        for season in (await db.scalars(select(Season).where(Season.show_id == show.id))).all():
            if season.season_number in planned:
                continue
            remaining = await db.scalar(
                select(func.count()).select_from(Episode).where(Episode.season_id == season.id)
            )
            if not remaining:
                await db.delete(season)
        await db.flush()

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


async def run_show_metadata_refresh(
    job_id,
    show_id,
    *,
    client_factory=TMDBClient,
) -> None:
    """Refresh one Show's TMDB metadata as a durable background Job."""

    async def worker(db, job) -> None:
        show = await db.get(Show, show_id)
        if show is None:
            return

        configuration = await get_tmdb_configuration(db)
        if configuration is None or not configuration.enabled or not configuration.api_key:
            message = "TMDB is not configured and enabled."
            raise JobFailure(
                "TMDB_NOT_CONFIGURED",
                message,
                progress={"current": 0, "total": 1, "percent": 0, "stage": "failed", "detail": message},
            )

        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"current": 0, "total": 1, "percent": 0, "stage": "refreshing_metadata", "detail": f"Refreshing TMDB metadata for {show.title}…"},
        )

        try:
            from app.services.reconciliation import resolve_problem

            counts = await sync_show_metadata(db, show, client_factory=client_factory)
            await resolve_problem(db, "TMDB_SHOW_METADATA_UNAVAILABLE", "show", show.id)
            show.revision += 1
            await create_event(
                db,
                "show.metadata_refreshed",
                entity_type="show",
                entity_id=show.id,
                message=f"Refreshed metadata for {show.title}.",
                details=counts,
            )
            summary = {
                "show_id": str(show.id),
                "show_title": show.title,
                **counts,
                "message": f"TMDB metadata refreshed for {show.title}.",
            }
            await checkpoint_job(
                db,
                job,
                status=JobStatus.COMPLETED,
                progress={"current": 1, "total": 1, "percent": 100, "stage": "completed", "detail": summary["message"]},
                summary=summary,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise JobFailure(
                "TMDB_SHOW_METADATA_FAILED",
                str(exc),
                progress={"current": 1, "total": 1, "percent": 100, "stage": "failed", "detail": f"TMDB metadata refresh failed for {show.title}."},
            )

    await run_job(
        job_id,
        worker,
        failure_code="TMDB_SHOW_METADATA_FAILED",
        failure_message="TMDB metadata refresh failed.",
    )
