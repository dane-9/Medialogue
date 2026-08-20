from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
from time import perf_counter
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.integrations.plex import PlexClient, PlexLibrarySnapshot, PlexMediaMatch
from app.models.domain import (
    Episode,
    EpisodeMediaMap,
    MediaDirectory,
    MediaFile,
    MediaType,
    Movie,
    MovieRelease,
    PlexConfiguration,
    PlexMatchMethod,
    PlexMatchState,
    PlexObservation,
    ReleaseState,
    Severity,
    Show,
)
from app.services.events import create_event
from app.services.reconciliation import open_problem, resolve_problem

PlexClientFactory = Callable[[str, str], PlexClient]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_plex_configuration(db: AsyncSession) -> PlexConfiguration | None:
    return await db.scalar(select(PlexConfiguration).order_by(PlexConfiguration.created_at).limit(1))


async def test_plex_connection(
    url: str,
    token: str,
    *,
    client_factory: PlexClientFactory = PlexClient,
) -> dict[str, object]:
    started = perf_counter()
    client = client_factory(url, token)
    try:
        health = await client.health()
        return {
            "status": "healthy",
            "machine_identifier": health.get("machine_identifier"),
            "latency_ms": round((perf_counter() - started) * 1000),
        }
    finally:
        await client.close()


async def refresh_plex_health(
    db: AsyncSession,
    configuration: PlexConfiguration,
    *,
    client_factory: PlexClientFactory = PlexClient,
) -> dict[str, object]:
    previous_health = configuration.health
    configuration.last_checked_at = utcnow()
    try:
        result = await test_plex_connection(
            configuration.url, configuration.token, client_factory=client_factory
        )
    except Exception as exc:
        configuration.health = "unavailable"
        configuration.last_error = str(exc)
        configuration.latency_ms = None
        await _record_health_change(db, configuration, previous_health)
        return {"status": "unavailable", "message": str(exc)}
    configuration.health = "healthy"
    configuration.machine_identifier = str(result.get("machine_identifier") or "") or None
    configuration.latency_ms = int(result["latency_ms"])
    configuration.last_success_at = utcnow()
    configuration.last_error = None
    await _record_health_change(db, configuration, previous_health)
    return result


async def _record_health_change(
    db: AsyncSession, configuration: PlexConfiguration, previous_health: str
) -> None:
    if configuration.health == previous_health:
        return
    await create_event(
        db,
        "plex.health",
        entity_type="integration",
        entity_id=configuration.id,
        message=f"Plex health changed to {configuration.health}.",
        severity=Severity.ERROR if configuration.health == "unavailable" else Severity.INFO,
        details={"status": configuration.health},
    )


async def recheck_movie_plex(
    db: AsyncSession,
    movie: Movie,
    configuration: PlexConfiguration,
    *,
    client_factory: PlexClientFactory = PlexClient,
    snapshot: PlexLibrarySnapshot | None = None,
) -> dict[str, int | str]:
    loaded_releases = (
        await db.scalars(
            select(MovieRelease)
            .options(selectinload(MovieRelease.directories).selectinload(MediaDirectory.files))
            .where(MovieRelease.movie_id == movie.id)
        )
    ).all()
    releases = [
        release
        for release in loaded_releases
        if release.release_state in {ReleaseState.CURRENT, ReleaseState.DUPLICATE, ReleaseState.CONFLICT}
        and any(directory.exists for directory in release.directories)
    ]
    client = None if snapshot is not None else client_factory(configuration.url, configuration.token)
    checked = matched = conflicts = 0
    overall = PlexMatchState.PENDING
    multiple_versions = 0
    previous_health = configuration.health
    try:
        if snapshot is None:
            assert client is not None
            health_started = perf_counter()
            health = await client.health()
            configuration.health = "healthy"
            configuration.last_checked_at = utcnow()
            configuration.last_success_at = utcnow()
            configuration.machine_identifier = str(health.get("machine_identifier") or "") or None
            configuration.latency_ms = round((perf_counter() - health_started) * 1000)
            configuration.last_error = None
        for release in releases:
            checked += 1
            exact = await _find_release_exact_match(client, release, snapshot=snapshot)
            if exact is not None:
                title_agrees = not exact.title or exact.title.casefold() == movie.title.casefold()
                year_agrees = movie.year is None or exact.year is None or exact.year == movie.year
                agrees = title_agrees and year_agrees
                if agrees:
                    state = PlexMatchState.MATCHED
                    method = PlexMatchMethod.EXACT_PATH
                    matched += 1
                else:
                    state = PlexMatchState.CONFLICT
                    method = PlexMatchMethod.EXACT_PATH
                    conflicts += 1
                await _upsert_observation(db, movie, release, state, method, exact=exact)
                if state == PlexMatchState.CONFLICT:
                    await _open_conflict(db, movie, release, exact)
                else:
                    await _resolve_conflict(db, movie.id)
                continue

            if snapshot is not None:
                title_matches = snapshot.search_title_year(movie.title, movie.year)
            else:
                assert client is not None
                title_matches = await client.search_title_year(movie.title, movie.year)
            if len(title_matches) == 1:
                title_match = title_matches[0]
                state = PlexMatchState.MATCHED
                matched += 1
                await _upsert_observation(
                    db,
                    movie,
                    release,
                    state,
                    PlexMatchMethod.TITLE_YEAR,
                    title=title_match.title,
                    year=title_match.year,
                    rating_key=title_match.rating_key,
                    edition=title_match.edition,
                )
            elif len(title_matches) > 1:
                state = PlexMatchState.MULTIPLE_VERSIONS
                multiple_versions += 1
                await _upsert_observation(db, movie, release, state, PlexMatchMethod.TITLE_YEAR)
            else:
                state = PlexMatchState.PENDING
                await _upsert_observation(db, movie, release, state, None)

        if conflicts:
            overall = PlexMatchState.CONFLICT
        elif multiple_versions:
            overall = PlexMatchState.MULTIPLE_VERSIONS
        elif matched:
            overall = PlexMatchState.MATCHED
        elif releases:
            overall = PlexMatchState.PENDING
    except Exception as exc:
        # When a caller supplied a pre-fetched library snapshot, Plex already
        # answered successfully. A local parser/database failure must not be
        # misreported as a Plex outage. Let the sync worker isolate that title.
        if snapshot is not None:
            raise
        configuration.health = "unavailable"
        configuration.last_checked_at = utcnow()
        configuration.last_error = str(exc)
        overall = PlexMatchState.UNAVAILABLE
        checked = len(releases)
        for release in releases:
            await _upsert_observation(db, movie, release, PlexMatchState.UNAVAILABLE, None)
    finally:
        if client is not None:
            await client.close()
    await _record_health_change(db, configuration, previous_health)

    return {
        "state": overall.value,
        "checked_releases": checked,
        "matched_releases": matched,
        "conflict_releases": conflicts,
    }


async def _find_release_exact_match(
    client: PlexClient | None,
    release: MovieRelease,
    *,
    snapshot: PlexLibrarySnapshot | None = None,
) -> PlexMediaMatch | None:
    for directory in release.directories:
        if not directory.exists:
            continue
        for media_file in directory.files:
            if not media_file.exists or media_file.media_role.value not in {"movie_video", "other_media"}:
                continue
            path = str(PurePosixPath(directory.resolved_path) / media_file.relative_path)
            if snapshot is not None:
                match = snapshot.find_exact_path(path)
            else:
                assert client is not None
                match = await client.find_exact_path(path)
            if match is not None:
                return match
    return None


async def _upsert_observation(
    db: AsyncSession,
    movie: Movie,
    release: MovieRelease,
    state: PlexMatchState,
    method: PlexMatchMethod | None,
    *,
    exact: PlexMediaMatch | None = None,
    title: str | None = None,
    year: int | None = None,
    rating_key: str | None = None,
    edition: str | None = None,
) -> PlexObservation:
    observation = await db.scalar(
        select(PlexObservation).where(PlexObservation.movie_release_id == release.id)
    )
    previous = observation.match_state if observation else None
    if observation is None:
        observation = PlexObservation(
            media_type=MediaType.MOVIES,
            movie_id=movie.id,
            movie_release_id=release.id,
            match_state=state,
            match_method=method,
        )
        db.add(observation)
    observation.match_state = state
    observation.match_method = method
    observation.plex_rating_key = exact.rating_key if exact else rating_key
    observation.plex_title = exact.title if exact else title
    observation.plex_year = exact.year if exact else year
    observation.plex_edition = exact.edition if exact else edition
    observation.plex_reported_path = exact.file_path if exact else None
    observation.resolved_path = exact.file_path if exact else None
    observation.last_seen_at = utcnow()
    await db.flush()
    if previous != state:
        await create_event(
            db,
            f"plex.{state.value}",
            entity_type="movie",
            entity_id=movie.id,
            message=f"Plex verification changed to {state.value} for {movie.title}.",
            severity=Severity.ERROR if state == PlexMatchState.CONFLICT else Severity.INFO,
            details={"release_id": str(release.id), "plex_state": state.value},
        )
    return observation


async def _open_conflict(
    db: AsyncSession, movie: Movie, release: MovieRelease, match: PlexMediaMatch
) -> None:
    details = {
        "movie_release_id": str(release.id),
        "local_title": movie.title,
        "local_year": movie.year,
        "plex_title": match.title,
        "plex_year": match.year,
        "path": match.file_path,
    }
    await open_problem(
        db,
        reason="PLEX_IDENTITY_MISMATCH",
        entity_type="movie",
        entity_id=movie.id,
        severity=Severity.ERROR,
        message="Plex identifies the exact media path as a different movie.",
        details=details,
    )


async def _resolve_conflict(db: AsyncSession, movie_id) -> None:
    # A movie-level problem remains open while any current Plex observation
    # still disagrees.  Resolving it merely because another release matched
    # would lose a real conflict when a movie has multiple physical releases.
    unresolved = await db.scalar(
        select(PlexObservation.id)
        .where(
            PlexObservation.movie_id == movie_id,
            PlexObservation.match_state == PlexMatchState.CONFLICT,
        )
        .limit(1)
    )
    if unresolved is not None:
        return
    await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "movie", movie_id)


async def recheck_show_plex(
    db: AsyncSession,
    show: Show,
    configuration: PlexConfiguration,
    *,
    client_factory: PlexClientFactory = PlexClient,
    snapshot: PlexLibrarySnapshot | None = None,
) -> dict[str, int | str]:
    """Verify mapped episode files by exact Plex path without triggering scans."""

    rows = (
        await db.execute(
            select(Episode, EpisodeMediaMap, MediaFile, MediaDirectory)
            .join(EpisodeMediaMap, EpisodeMediaMap.episode_id == Episode.id)
            .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
            .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
            .where(Episode.show_id == show.id, MediaFile.exists.is_(True), MediaDirectory.exists.is_(True))
        )
    ).all()
    client = None if snapshot is not None else client_factory(configuration.url, configuration.token)
    checked = matched = conflicts = 0
    previous_health = configuration.health
    try:
        if snapshot is None:
            assert client is not None
            started = perf_counter()
            health = await client.health()
            configuration.health = "healthy"
            configuration.last_checked_at = utcnow()
            configuration.last_success_at = utcnow()
            configuration.machine_identifier = str(health.get("machine_identifier") or "") or None
            configuration.latency_ms = round((perf_counter() - started) * 1000)
            configuration.last_error = None
        for episode, mapping, media_file, directory in rows:
            checked += 1
            path = str(PurePosixPath(directory.resolved_path) / media_file.relative_path)
            if snapshot is not None:
                exact = snapshot.find_exact_path(path)
            else:
                assert client is not None
                exact = await client.find_exact_path(path)
            if exact is None:
                state = PlexMatchState.PENDING
            else:
                title_agrees = not exact.show_title or exact.show_title.casefold() == show.title.casefold()
                season_agrees = exact.season_number is None or exact.season_number == episode.season_number
                episode_agrees = exact.episode_number is None or exact.episode_number == episode.episode_number
                state = PlexMatchState.MATCHED if title_agrees and season_agrees and episode_agrees else PlexMatchState.CONFLICT
                if state == PlexMatchState.MATCHED:
                    matched += 1
                else:
                    conflicts += 1
            observation = await db.scalar(select(PlexObservation).where(PlexObservation.media_file_id == media_file.id))
            previous = observation.match_state if observation else None
            if observation is None:
                observation = PlexObservation(
                    media_type=MediaType.SHOWS,
                    show_id=show.id,
                    episode_id=episode.id,
                    media_file_id=media_file.id,
                    match_state=state,
                )
                db.add(observation)
            observation.show_id = show.id
            observation.episode_id = episode.id
            observation.media_file_id = media_file.id
            observation.match_state = state
            observation.match_method = PlexMatchMethod.EXACT_PATH if exact is not None else None
            observation.plex_rating_key = exact.rating_key if exact else None
            observation.plex_title = exact.show_title or exact.title if exact else None
            observation.plex_year = exact.year if exact else None
            observation.plex_reported_path = exact.file_path if exact else None
            observation.resolved_path = path
            observation.last_seen_at = utcnow()
            if previous != state:
                await create_event(
                    db,
                    f"plex.{state.value}",
                    entity_type="episode",
                    entity_id=episode.id,
                    message=f"Plex verification changed to {state.value} for S{episode.season_number:02d}E{episode.episode_number:02d}.",
                    severity=Severity.ERROR if state == PlexMatchState.CONFLICT else Severity.INFO,
                    details={"show_id": str(show.id), "path": path},
                )
        if conflicts:
            await open_problem(
                db,
                reason="PLEX_IDENTITY_MISMATCH",
                entity_type="show",
                entity_id=show.id,
                severity=Severity.ERROR,
                message="Plex identifies one or more exact episode paths as another Show or episode.",
                details={"conflict_count": conflicts},
            )
        else:
            await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "show", show.id)
    except Exception as exc:
        # A snapshot-backed verification has already established Plex
        # connectivity. Keep local processing errors local to the sync job.
        if snapshot is not None:
            raise
        configuration.health = "unavailable"
        configuration.last_checked_at = utcnow()
        configuration.last_error = str(exc)
        for episode, mapping, media_file, directory in rows:
            observation = await db.scalar(select(PlexObservation).where(PlexObservation.media_file_id == media_file.id))
            if observation is None:
                observation = PlexObservation(
                    media_type=MediaType.SHOWS,
                    show_id=show.id,
                    episode_id=episode.id,
                    media_file_id=media_file.id,
                    match_state=PlexMatchState.UNAVAILABLE,
                )
                db.add(observation)
            else:
                observation.match_state = PlexMatchState.UNAVAILABLE
                observation.last_seen_at = utcnow()
    finally:
        if client is not None:
            await client.close()
    await _record_health_change(db, configuration, previous_health)
    overall = "conflict" if conflicts else "matched" if matched else "pending" if rows else "pending"
    if configuration.health == "unavailable":
        overall = "unavailable"
    return {"state": overall, "checked_releases": checked, "matched_releases": matched, "conflict_releases": conflicts}
