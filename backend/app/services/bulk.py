from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.models.domain import (
    JobStatus,
    MediaProfileOverride,
    MediaType,
    Movie,
    MovieRelease,
    ParseEvidence,
    QualityDefinition,
    QualityProfile,
    SourceType,
    Tag,
)
from app.parser import parse_release_name
from app.services.events import create_event
from app.services.jobs import checkpoint_job, run_job
from app.services.plex import get_plex_configuration, recheck_movie_plex
from app.services.quality_profiles import refresh_movie_release_scores


def _resource_id(movie: Movie) -> str:
    return str(movie.tmdb_id or movie.id)


async def resolve_movies(db: AsyncSession, resource_ids: list[str]) -> list[Movie]:
    """Resolve TMDB resource IDs/internal UUIDs in two batched queries.

    Bulk administration can legitimately target hundreds of titles, so this
    must not degrade into one database round trip per selected Movie.
    """

    unique = list(dict.fromkeys(item.strip() for item in resource_ids if item.strip()))
    tmdb_ids: list[int] = []
    uuid_ids: list[UUID] = []
    invalid: list[str] = []
    for value in unique:
        if value.isdigit():
            tmdb_ids.append(int(value))
            continue
        try:
            uuid_ids.append(UUID(value))
        except ValueError:
            invalid.append(value)

    options = (selectinload(Movie.tags), selectinload(Movie.releases))
    rows: list[Movie] = []
    if tmdb_ids:
        rows.extend((await db.scalars(select(Movie).options(*options).where(Movie.tmdb_id.in_(tmdb_ids)))).unique().all())
    if uuid_ids:
        rows.extend((await db.scalars(select(Movie).options(*options).where(Movie.id.in_(uuid_ids)))).unique().all())

    by_tmdb = {str(movie.tmdb_id): movie for movie in rows if movie.tmdb_id is not None}
    by_uuid = {str(movie.id): movie for movie in rows}
    movies: list[Movie] = []
    missing = list(invalid)
    for value in unique:
        movie = by_tmdb.get(value) if value.isdigit() else by_uuid.get(value)
        if movie is None:
            missing.append(value)
        else:
            movies.append(movie)
    if missing:
        raise AppError(
            "MOVIES_NOT_FOUND",
            "One or more selected Movies no longer exist.",
            status_code=404,
            details={"missing": list(dict.fromkeys(missing))},
        )
    return movies


async def load_tags(db: AsyncSession, tag_ids: list[UUID]) -> list[Tag]:
    unique_ids = list(dict.fromkeys(tag_ids))
    rows = list((await db.scalars(select(Tag).where(Tag.id.in_(unique_ids)))).all()) if unique_ids else []
    found = {row.id for row in rows}
    missing = [str(item) for item in unique_ids if item not in found]
    if missing:
        raise AppError(
            "TAGS_NOT_FOUND",
            "One or more selected tags no longer exist.",
            status_code=404,
            details={"missing": missing},
        )
    return rows


async def set_movie_monitoring(db: AsyncSession, movies: list[Movie], monitored: bool) -> int:
    changed = 0
    for movie in movies:
        if movie.monitored == monitored:
            continue
        movie.monitored = monitored
        movie.revision += 1
        changed += 1
        await create_event(
            db,
            "movie.monitoring_updated",
            entity_type="movie",
            entity_id=movie.id,
            message=f"Movie monitoring was {'enabled' if monitored else 'disabled'} by a bulk action.",
            details={"monitored": monitored, "bulk": True},
        )
    return changed


async def add_movie_tags(db: AsyncSession, movies: list[Movie], tags: list[Tag]) -> int:
    changed = 0
    for movie in movies:
        existing = {item.id for item in movie.tags}
        additions = [tag for tag in tags if tag.id not in existing]
        if not additions:
            continue
        movie.tags.extend(additions)
        movie.revision += 1
        changed += 1
        await create_event(
            db,
            "movie.tags_updated",
            entity_type="movie",
            entity_id=movie.id,
            message="Movie tags were updated by a bulk action.",
            details={"action": "add", "tag_ids": [str(tag.id) for tag in additions], "bulk": True},
        )
    return changed


async def remove_movie_tags(db: AsyncSession, movies: list[Movie], tags: list[Tag]) -> int:
    remove_ids = {tag.id for tag in tags}
    changed = 0
    for movie in movies:
        removed = [tag for tag in movie.tags if tag.id in remove_ids]
        if not removed:
            continue
        movie.tags[:] = [tag for tag in movie.tags if tag.id not in remove_ids]
        movie.revision += 1
        changed += 1
        await create_event(
            db,
            "movie.tags_updated",
            entity_type="movie",
            entity_id=movie.id,
            message="Movie tags were updated by a bulk action.",
            details={"action": "remove", "tag_ids": [str(tag.id) for tag in removed], "bulk": True},
        )
    return changed


async def change_movie_profile(
    db: AsyncSession,
    movies: list[Movie],
    quality_profile_id: UUID | None,
) -> int:
    if quality_profile_id is not None and await db.get(QualityProfile, quality_profile_id) is None:
        raise AppError("INVALID_QUALITY_PROFILE", "Quality Profile does not exist.", status_code=422)

    changed = 0
    for movie in movies:
        assignment = await db.scalar(
            select(MediaProfileOverride)
            .where(MediaProfileOverride.movie_id == movie.id)
            .with_for_update()
        )
        if assignment is None:
            if quality_profile_id is None:
                continue
            assignment = MediaProfileOverride(
                media_type=MediaType.MOVIES,
                movie_id=movie.id,
                quality_profile_id=quality_profile_id,
                override_definition={},
                revision=1,
            )
            db.add(assignment)
            await db.flush()
            changed += 1
        elif assignment.quality_profile_id != quality_profile_id:
            assignment.quality_profile_id = quality_profile_id
            assignment.revision += 1
            changed += 1
        else:
            continue

        # Per-title minimum/CF overrides are deliberately preserved. A bulk
        # profile change changes only the inherited base profile.
        await refresh_movie_release_scores(db, movie.id)
        await create_event(
            db,
            "quality_profile.assignment_updated",
            entity_type="movie",
            entity_id=movie.id,
            message="Quality Profile assignment was changed by a bulk action.",
            details={
                "quality_profile_id": str(quality_profile_id) if quality_profile_id else None,
                "bulk": True,
                "overrides_preserved": True,
            },
        )
    return changed


_OPERATIONAL_PARSE_KEYS = {
    "identity_confidence",
    "incoming",
    "incoming_kind",
    "replacement_of_release_id",
    "current_score_snapshot",
}


async def reparse_movie_releases(db: AsyncSession, movie: Movie) -> int:
    releases = list((await db.scalars(select(MovieRelease).where(MovieRelease.movie_id == movie.id))).all())
    for release in releases:
        parsed = parse_release_name(release.raw_release_name)
        quality = None
        if parsed.quality.canonical:
            quality = await db.scalar(
                select(QualityDefinition).where(QualityDefinition.name == parsed.quality.canonical)
            )
        previous = dict(release.parse_snapshot or {})
        preserved = {key: previous[key] for key in _OPERATIONAL_PARSE_KEYS if key in previous}
        fresh = parsed.to_dict()
        release.parsed_title = parsed.identity.title_candidate
        release.parsed_year = parsed.identity.year
        release.parsed_edition = parsed.edition
        if release.manual_edition_override is None:
            release.effective_edition = parsed.edition
        release.quality_definition_id = quality.id if quality else None
        release.release_group = parsed.release_group
        release.parser_version = parsed.parser_version
        release.parse_snapshot = {**fresh, **preserved}
        db.add(
            ParseEvidence(
                source_type=SourceType.DIRECTORY_NAME,
                source_id=release.id,
                raw_name=release.raw_release_name,
                parse_snapshot={**fresh, "bulk_reparse": True},
                parser_version=parsed.parser_version,
            )
        )
    await db.flush()
    await refresh_movie_release_scores(db, movie.id)
    await create_event(
        db,
        "parser.reevaluated",
        entity_type="movie",
        entity_id=movie.id,
        message=f"Parser evidence was re-evaluated for {len(releases)} release(s).",
        details={"release_count": len(releases), "bulk": True},
    )
    return len(releases)


async def reevaluate_movie_custom_formats(db: AsyncSession, movie: Movie) -> int:
    count = await refresh_movie_release_scores(db, movie.id)
    await create_event(
        db,
        "custom_formats.reevaluated",
        entity_type="movie",
        entity_id=movie.id,
        message=f"Current Custom Format scores were re-evaluated for {count} release(s).",
        details={"release_count": count, "bulk": True},
    )
    return count


async def create_bulk_summary_event(
    db: AsyncSession,
    *,
    action: str,
    movies: list[Movie],
    updated: int,
    details: dict[str, object] | None = None,
) -> None:
    await create_event(
        db,
        "bulk.operation_completed",
        entity_type="bulk_operation",
        entity_id=None,
        message=f"Bulk Movie action '{action}' completed for {len(movies)} selected title(s).",
        details={
            "action": action,
            "requested": len(movies),
            "updated": updated,
            "movie_ids": [_resource_id(movie) for movie in movies],
            **(details or {}),
        },
    )


async def run_long_bulk_movie_action(
    job_id: UUID,
    movie_ids: list[str],
    action: str,
    *,
    plex_client_factory,
) -> None:
    """Run the multi-title bulk actions that can take longer than one request."""

    async def worker(db, job) -> None:
        movies = await resolve_movies(db, movie_ids)
        if action == "recheck_plex":
            configuration = await get_plex_configuration(db)
            if configuration is None or not configuration.enabled or not configuration.token:
                raise AppError("PLEX_NOT_CONFIGURED", "Plex is not configured and enabled.", status_code=409)

        total = len(movies)
        updated = 0
        details: dict[str, object] = {
            "release_count": 0,
            "checked_releases": 0,
            "matched_releases": 0,
            "not_found_releases": 0,
            "multiple_version_releases": 0,
            "conflict_releases": 0,
            "failed_movies": [],
        }
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"current": 0, "total": total, "percent": 0, "stage": "processing_movies", "detail": f"Starting {action.replace('_', ' ')} for {total} movie{'s' if total != 1 else ''}…"},
            summary={"action": action, "requested": total, "updated": 0, "details": details},
        )

        for index, movie in enumerate(movies, start=1):
            await db.refresh(job)
            if job.status == JobStatus.CANCELLED:
                return
            try:
                async with db.begin_nested():
                    if action == "reevaluate_parser":
                        details["release_count"] = int(details["release_count"]) + await reparse_movie_releases(db, movie)
                    elif action == "reevaluate_custom_formats":
                        details["release_count"] = int(details["release_count"]) + await reevaluate_movie_custom_formats(db, movie)
                    elif action == "recheck_plex":
                        result = await recheck_movie_plex(db, movie, configuration, client_factory=plex_client_factory)
                        for key in ("checked_releases", "matched_releases", "not_found_releases", "multiple_version_releases", "conflict_releases"):
                            details[key] = int(details[key]) + int(result.get(key, 0))
                    else:  # pragma: no cover - API only queues supported actions.
                        raise AppError("INVALID_BULK_ACTION", "Unsupported long-running Movie action.", status_code=422)
                updated += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed_movies = details["failed_movies"]
                assert isinstance(failed_movies, list)
                if len(failed_movies) < 10:
                    failed_movies.append({"movie_id": str(movie.id), "title": movie.title, "error": str(exc)})

            progress = {
                "current": index,
                "total": total,
                "percent": round(index * 100 / total, 1) if total else 100,
                "stage": "processing_movies",
                "detail": f"Processed {movie.title} ({index}/{total}).",
            }
            await checkpoint_job(
                db,
                job,
                progress=progress,
                summary={"action": action, "requested": total, "updated": updated, "details": details},
            )

        await create_bulk_summary_event(
            db,
            action=action,
            movies=movies,
            updated=updated,
            details=details,
        )
        summary = {
            "action": action,
            "requested": total,
            "updated": updated,
            "details": details,
            "message": f"Bulk {action.replace('_', ' ')} completed for {updated} of {total} movie{'s' if total != 1 else ''}.",
        }
        await checkpoint_job(
            db,
            job,
            status=JobStatus.COMPLETED,
            progress={"current": total, "total": total, "percent": 100, "stage": "completed", "detail": summary["message"]},
            summary=summary,
        )

    await run_job(
        job_id,
        worker,
        failure_code="BULK_MOVIE_ACTION_FAILED",
        failure_message=f"Bulk {action.replace('_', ' ')} failed.",
        failure_progress={"current": 0, "total": 0, "percent": 0, "stage": "failed", "detail": f"Bulk {action.replace('_', ' ')} failed."},
    )
