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
    PlexMatchMethod,
    PlexMatchState,
    PlexObservation,
    ReleaseState,
    Severity,
    Show,
    StorageRoot,
)
from app.services.events import create_event
from app.services.integration_state import ConfiguredPlex, get_configured_plex
from app.services.reconciliation import open_problem, resolve_problem

PlexClientFactory = Callable[[str, str], PlexClient]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_plex_configuration(db: AsyncSession) -> ConfiguredPlex | None:
    return await get_configured_plex(db)


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
    configuration: ConfiguredPlex,
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
    db: AsyncSession, configuration: ConfiguredPlex, previous_health: str
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




async def _client_library_snapshot(client: object) -> PlexLibrarySnapshot | None:
    """Use the optimized snapshot API when the concrete Plex adapter supports it.

    Test doubles and older adapters can omit ``library_snapshot``; callers then
    fall back to the legacy per-path read methods.
    """

    loader = getattr(client, "library_snapshot", None)
    if not callable(loader):
        return None
    return await loader()

async def recheck_movie_plex(
    db: AsyncSession,
    movie: Movie,
    configuration: ConfiguredPlex,
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
    effective_snapshot = snapshot
    checked = matched = conflicts = not_found = 0
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
            effective_snapshot = await _client_library_snapshot(client)
        for release in releases:
            checked += 1
            exact, checked_path = await _find_release_exact_match(
                db, client, release, snapshot=effective_snapshot
            )
            if exact is not None:
                # TMDB/manual matching owns movie identity. Once Plex reports
                # the same physical file, its display title/year are metadata
                # only and cannot turn a verified path into a conflict.
                state = PlexMatchState.MATCHED
                method = PlexMatchMethod.EXACT_PATH
                matched += 1
                await _upsert_observation(
                    db, movie, release, state, method, exact=exact, resolved_path=checked_path
                )
                continue

            if effective_snapshot is not None:
                title_matches = effective_snapshot.search_title_year(movie.title, movie.year)
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
                    resolved_path=checked_path,
                )
            elif len(title_matches) > 1:
                state = PlexMatchState.MULTIPLE_VERSIONS
                multiple_versions += 1
                await _upsert_observation(
                    db, movie, release, state, PlexMatchMethod.TITLE_YEAR, resolved_path=checked_path
                )
            else:
                # Plex answered successfully and this media was absent. This is
                # a completed observation, not a job that is still pending.
                state = PlexMatchState.NOT_FOUND
                not_found += 1
                await _upsert_observation(db, movie, release, state, None, resolved_path=checked_path)

        if conflicts:
            overall = PlexMatchState.CONFLICT
        elif multiple_versions:
            overall = PlexMatchState.MULTIPLE_VERSIONS
        elif not_found:
            # A movie is only fully verified when every eligible active release
            # is accounted for in Plex. Keep a missing release visible.
            overall = PlexMatchState.NOT_FOUND
        elif matched:
            overall = PlexMatchState.MATCHED
    except Exception as exc:
        # When a caller supplied (or this function loaded) a Plex snapshot,
        # Plex already answered successfully. Local parser/database failures
        # must not be misreported as a Plex outage.
        if effective_snapshot is not None:
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
        "not_found_releases": not_found,
        "multiple_version_releases": multiple_versions,
        "conflict_releases": conflicts,
    }


async def _find_release_exact_match(
    db: AsyncSession,
    client: PlexClient | None,
    release: MovieRelease,
    *,
    snapshot: PlexLibrarySnapshot | None = None,
) -> tuple[PlexMediaMatch | None, str | None]:
    root_ids = {directory.storage_root_id for directory in release.directories if directory.storage_root_id}
    roots = (
        await db.scalars(select(StorageRoot).where(StorageRoot.id.in_(root_ids)))
    ).all() if root_ids else []
    root_paths = {root.id: root.resolved_root_path for root in roots}
    first_checked_path: str | None = None
    for directory in release.directories:
        if not directory.exists:
            continue
        for media_file in directory.files:
            if not media_file.exists or media_file.media_role.value not in {"movie_video", "other_media"}:
                continue
            path = str(PurePosixPath(directory.resolved_path) / media_file.relative_path)
            first_checked_path = first_checked_path or path
            if snapshot is not None:
                match = snapshot.find_exact_path(
                    path,
                    local_root=root_paths.get(directory.storage_root_id),
                    media_type="movies",
                )
            else:
                assert client is not None
                match = await client.find_exact_path(path)
            if match is not None:
                return match, path
    return None, first_checked_path


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
    resolved_path: str | None = None,
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
    # Keep the path in Medialogue's namespace here. The Plex-side path is
    # already preserved separately in plex_reported_path.
    observation.resolved_path = resolved_path
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


async def recheck_show_plex(
    db: AsyncSession,
    show: Show,
    configuration: ConfiguredPlex,
    *,
    client_factory: PlexClientFactory = PlexClient,
    snapshot: PlexLibrarySnapshot | None = None,
) -> dict[str, int | str]:
    """Verify mapped episode files by Plex path without triggering Plex scans."""

    rows = (
        await db.execute(
            select(Episode, EpisodeMediaMap, MediaFile, MediaDirectory)
            .join(EpisodeMediaMap, EpisodeMediaMap.episode_id == Episode.id)
            .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
            .join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id)
            .where(Episode.show_id == show.id, MediaFile.exists.is_(True), MediaDirectory.exists.is_(True))
        )
    ).all()
    root_ids = {directory.storage_root_id for _, _, _, directory in rows if directory.storage_root_id}
    roots = (
        await db.scalars(select(StorageRoot).where(StorageRoot.id.in_(root_ids)))
    ).all() if root_ids else []
    root_paths = {root.id: root.resolved_root_path for root in roots}

    client = None if snapshot is not None else client_factory(configuration.url, configuration.token)
    effective_snapshot = snapshot
    checked = matched = conflicts = not_found = 0
    conflict_evidence: list[dict[str, object]] = []
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
            effective_snapshot = await _client_library_snapshot(client)
        for episode, mapping, media_file, directory in rows:
            del mapping
            checked += 1
            path = str(PurePosixPath(directory.resolved_path) / media_file.relative_path)
            if effective_snapshot is not None:
                exact = effective_snapshot.find_exact_path(
                    path,
                    local_root=root_paths.get(directory.storage_root_id),
                    media_type="shows",
                )
            else:
                assert client is not None
                exact = await client.find_exact_path(path)
            if exact is None:
                state = PlexMatchState.NOT_FOUND
                not_found += 1
            else:
                # Plex show naming is also advisory. For TV the only identity
                # disagreement worth surfacing on an exact physical file is
                # episode numbering, because that can indicate a real mapping
                # error (for example S01E02 vs S01E03).
                season_agrees = exact.season_number is None or exact.season_number == episode.season_number
                episode_agrees = exact.episode_number is None or exact.episode_number == episode.episode_number
                state = PlexMatchState.MATCHED if season_agrees and episode_agrees else PlexMatchState.CONFLICT
                if state == PlexMatchState.MATCHED:
                    matched += 1
                else:
                    conflicts += 1
                    differences: list[str] = []
                    if exact.season_number is not None and exact.season_number != episode.season_number:
                        differences.append("season")
                    if exact.episode_number is not None and exact.episode_number != episode.episode_number:
                        differences.append("episode")
                    conflict_evidence.append(
                        {
                            "local_show_title": show.title,
                            "local_episode": f"S{episode.season_number:02d}E{episode.episode_number:02d}",
                            "local_path": path,
                            "plex_show_title": exact.show_title,
                            "plex_episode_title": exact.title,
                            "plex_episode": (
                                f"S{exact.season_number:02d}E{exact.episode_number:02d}"
                                if exact.season_number is not None and exact.episode_number is not None
                                else None
                            ),
                            "plex_path": exact.file_path,
                            "differences": differences,
                        }
                    )
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
                message=f"Plex reports different episode numbering for {conflicts} mapped file{'s' if conflicts != 1 else ''}.",
                details={"conflict_count": conflicts, "conflicts": conflict_evidence[:20]},
            )
        else:
            await resolve_problem(db, "PLEX_IDENTITY_MISMATCH", "show", show.id)
    except Exception as exc:
        if effective_snapshot is not None:
            raise
        configuration.health = "unavailable"
        configuration.last_checked_at = utcnow()
        configuration.last_error = str(exc)
        for episode, mapping, media_file, directory in rows:
            del mapping, directory
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
    overall = (
        "conflict" if conflicts else
        "not_found" if not_found else
        "matched" if matched else
        "pending"
    )
    if configuration.health == "unavailable":
        overall = "unavailable"
    return {
        "state": overall,
        "checked_releases": checked,
        "matched_releases": matched,
        "not_found_releases": not_found,
        "multiple_version_releases": 0,
        "conflict_releases": conflicts,
    }

