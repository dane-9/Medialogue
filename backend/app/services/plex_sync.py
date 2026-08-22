from __future__ import annotations

import asyncio
from time import perf_counter
from uuid import UUID

from sqlalchemy import select

from app.db import session as db_session
from app.integrations.plex import PlexClient
from app.models.domain import Job, JobStatus, MediaDirectory, Movie, MovieRelease, Show, ShowRelease
from app.services.events import create_event
from app.services.jobs import JobFailure, checkpoint_job, publish_job_status, run_job, update_job
from app.services.plex import get_plex_configuration, recheck_movie_plex, recheck_show_plex, utcnow


async def run_plex_library_sync(job_id: UUID, storage_root_id: UUID | None = None) -> None:
    """Read Plex once, then verify all relevant local media against that snapshot.

    This never asks Plex to scan or mutate its library.  A root-scoped sync is
    normally launched after a manual Medialogue root scan; the Settings action
    launches an all-library sync.
    """

    async with db_session.async_session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None or job.status in {JobStatus.CANCELLED, JobStatus.INTERRUPTED, JobStatus.COMPLETED, JobStatus.FAILED}:
            return
        configuration = await get_plex_configuration(db)
        if configuration is None or not configuration.enabled or not configuration.token:
            await update_job(
                db,
                job,
                status=JobStatus.FAILED,
                error={"code": "PLEX_NOT_CONFIGURED", "message": "Plex is not configured and enabled."},
            )
            await db.commit()
            return

        await update_job(db, job, status=JobStatus.RUNNING, progress={"current": 0, "total": 0, "percent": 0, "stage": "loading_plex"})
        await db.commit()
        publish_job_status(job)

        client = PlexClient(configuration.url, configuration.token)
        try:
            started = perf_counter()
            health = await client.health()
            snapshot = await client.library_snapshot()
            configuration.health = "healthy"
            configuration.last_checked_at = utcnow()
            configuration.last_success_at = utcnow()
            configuration.machine_identifier = str(health.get("machine_identifier") or "") or None
            configuration.latency_ms = round((perf_counter() - started) * 1000)
            configuration.last_error = None
            await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            configuration.health = "unavailable"
            configuration.last_checked_at = utcnow()
            configuration.last_error = str(exc)
            await update_job(
                db,
                job,
                status=JobStatus.FAILED,
                error={"code": "PLEX_SYNC_FAILED", "message": str(exc)},
            )
            await db.commit()
            return
        finally:
            await client.close()

        movie_ids = await _movie_ids(db, storage_root_id)
        show_ids = await _show_ids(db, storage_root_id)
        work = [("movie", item) for item in movie_ids] + [("show", item) for item in show_ids]
        total = len(work)
        matched = not_found = multiple_versions = pending = conflicts = unavailable = errors = 0

        for index, (kind, entity_id) in enumerate(work, start=1):
            await db.refresh(job)
            if job.status == JobStatus.CANCELLED:
                return
            try:
                # One malformed/local title must not abort verification for the
                # rest of the library. The SAVEPOINT also prevents partial
                # observations for the failed title from leaking into the DB.
                async with db.begin_nested():
                    if kind == "movie":
                        entity = await db.get(Movie, entity_id)
                        if entity is None:
                            continue
                        result = await recheck_movie_plex(db, entity, configuration, snapshot=snapshot)
                    else:
                        entity = await db.get(Show, entity_id)
                        if entity is None:
                            continue
                        result = await recheck_show_plex(db, entity, configuration, snapshot=snapshot)
                state = str(result.get("state") or "pending")
                if state == "matched":
                    matched += 1
                elif state == "not_found":
                    not_found += 1
                elif state == "multiple_versions":
                    multiple_versions += 1
                elif state == "conflict":
                    conflicts += 1
                elif state == "unavailable":
                    unavailable += 1
                else:
                    pending += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                errors += 1
            progress = {
                "current": index,
                "total": total,
                "percent": round(index * 100 / total, 1) if total else 100,
                "stage": "verifying",
            }
            summary = {
                "matched": matched,
                "not_found": not_found,
                "multiple_versions": multiple_versions,
                "pending": pending,
                "conflicts": conflicts,
                "unavailable": unavailable,
                "errors": errors,
                "storage_root_id": str(storage_root_id) if storage_root_id else None,
            }
            await update_job(db, job, progress=progress, summary=summary)
            await db.commit()
            publish_job_status(job)

        summary = {
            "matched": matched,
            "not_found": not_found,
            "multiple_versions": multiple_versions,
            "pending": pending,
            "conflicts": conflicts,
            "unavailable": unavailable,
            "errors": errors,
            "storage_root_id": str(storage_root_id) if storage_root_id else None,
        }
        await update_job(
            db,
            job,
            status=JobStatus.COMPLETED,
            progress={"current": total, "total": total, "percent": 100, "stage": "completed"},
            summary=summary,
        )
        await create_event(
            db,
            "plex.sync_completed",
            entity_type="integration",
            entity_id=configuration.id,
            message=f"Plex verification completed for {total} titles.",
            details=summary,
        )
        await db.commit()
        publish_job_status(job)


async def run_plex_movie_recheck(
    job_id: UUID,
    movie_id: UUID,
    *,
    client_factory=PlexClient,
) -> None:
    """Run one movie Plex verification as a durable background Job."""

    async def worker(db, job) -> None:
        movie = await db.get(Movie, movie_id)
        if movie is None:
            return

        configuration = await get_plex_configuration(db)
        if configuration is None or not configuration.enabled or not configuration.token:
            raise JobFailure(
                "PLEX_NOT_CONFIGURED",
                "Plex is not configured and enabled.",
                progress={"current": 0, "total": 1, "percent": 0, "stage": "failed", "detail": "Plex is not configured and enabled."},
            )

        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"current": 0, "total": 1, "percent": 0, "stage": "checking_plex", "detail": f"Checking Plex for {movie.title}…"},
        )

        try:
            result = await recheck_movie_plex(db, movie, configuration, client_factory=client_factory)
            summary = {
                "movie_id": str(movie.id),
                "movie_title": movie.title,
                **result,
                "message": f"Plex recheck completed for {movie.title}.",
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
                "PLEX_RECHECK_FAILED",
                str(exc),
                progress={"current": 1, "total": 1, "percent": 100, "stage": "failed", "detail": f"Plex recheck failed for {movie.title}."},
            )

    await run_job(
        job_id,
        worker,
        failure_code="PLEX_RECHECK_FAILED",
        failure_message="Plex recheck failed.",
    )


async def run_plex_show_recheck(
    job_id: UUID,
    show_id: UUID,
    *,
    client_factory=PlexClient,
) -> None:
    """Run one Show Plex verification as a durable background Job."""

    async def worker(db, job) -> None:
        show = await db.get(Show, show_id)
        if show is None:
            return

        configuration = await get_plex_configuration(db)
        if configuration is None or not configuration.enabled or not configuration.token:
            raise JobFailure(
                "PLEX_NOT_CONFIGURED",
                "Plex is not configured and enabled.",
                progress={"current": 0, "total": 1, "percent": 0, "stage": "failed", "detail": "Plex is not configured and enabled."},
            )

        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"current": 0, "total": 1, "percent": 0, "stage": "checking_plex", "detail": f"Checking Plex for {show.title}…"},
        )

        try:
            result = await recheck_show_plex(db, show, configuration, client_factory=client_factory)
            summary = {
                "show_id": str(show.id),
                "show_title": show.title,
                **result,
                "message": f"Plex recheck completed for {show.title}.",
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
                "PLEX_RECHECK_FAILED",
                str(exc),
                progress={"current": 1, "total": 1, "percent": 100, "stage": "failed", "detail": f"Plex recheck failed for {show.title}."},
            )

    await run_job(
        job_id,
        worker,
        failure_code="PLEX_RECHECK_FAILED",
        failure_message="Plex recheck failed.",
    )


async def _movie_ids(db, storage_root_id: UUID | None) -> list[UUID]:
    statement = select(Movie.id)
    if storage_root_id is not None:
        statement = (
            statement.join(MovieRelease, MovieRelease.movie_id == Movie.id)
            .join(MediaDirectory, MediaDirectory.movie_release_id == MovieRelease.id)
            .where(MediaDirectory.storage_root_id == storage_root_id)
            .distinct()
        )
    return list((await db.scalars(statement)).all())


async def _show_ids(db, storage_root_id: UUID | None) -> list[UUID]:
    statement = select(Show.id)
    if storage_root_id is not None:
        statement = (
            statement.join(ShowRelease, ShowRelease.show_id == Show.id)
            .join(MediaDirectory, MediaDirectory.show_release_id == ShowRelease.id)
            .where(MediaDirectory.storage_root_id == storage_root_id)
            .distinct()
        )
    return list((await db.scalars(statement)).all())
