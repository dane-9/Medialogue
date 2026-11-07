from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.integrations.filesystem import FilesystemObserver
from app.models.domain import (
    IdentityState,
    JobStatus,
    MediaDirectory,
    Movie,
    MovieRelease,
    Problem,
    ProblemStatus,
    ReleaseState,
    Show,
    StorageRoot,
    utcnow,
)
from app.core.problem_types import MOVIE_MATCH_REASONS, SHOW_MATCH_REASONS
from app.services.events import create_event, queue_live_event
from app.services.jobs import JobFailure, checkpoint_job, create_job, run_job
from app.parser import EDITION_OPTIONS
from app.services.reconciliation import reconcile_movie_directory, refresh_movie_duplicate_problem
from app.services.shows import reconcile_show_directory
from app.services.movie_identity import apply_movie_identity
from app.services.tmdb import get_tmdb_configuration, sync_show_metadata

CONFIRMATION_TTL_SECONDS = 10 * 60


def available_actions(problem: Problem) -> list[str]:
    """What can be done about this Problem, most useful first.

    Dismiss and recheck end every list, so the only question worth asking is
    whether this particular condition has a step in front of them. Reasons that
    do not are not enumerated: they take the same ending as everything else.
    """
    if problem.status == ProblemStatus.DISMISSED:
        return ["restore"]
    if problem.status != ProblemStatus.OPEN:
        return []

    closing = ["dismiss", "recheck"]
    reason = problem.reason
    if problem.entity_type == "movie" and reason == "AMBIGUOUS_REPLACEMENT_TARGET":
        return ["choose_replacement_target", *closing]
    if problem.entity_type in {"media_directory", "movie"} and reason in MOVIE_MATCH_REASONS:
        return ["confirm_movie_match", *closing]
    if problem.entity_type in {"media_directory", "show"} and reason in SHOW_MATCH_REASONS:
        return ["confirm_show_match", *closing]
    return closing


async def resolve_explicit_problem_action(
    db: AsyncSession,
    problem: Problem,
    action: str,
    payload: dict[str, Any],
    *,
    tmdb_client_factory: Callable[..., Any] | None = None,
) -> Problem:
    if action == "restore":
        if problem.status != ProblemStatus.DISMISSED:
            raise AppError("PROBLEM_ACTION_NOT_ALLOWED", "Only a suppressed problem can be restored.", status_code=409)
        problem.status = ProblemStatus.OPEN
        problem.resolution = {"action": "restored", "restored_at": utcnow().isoformat()}
        problem.resolved_at = None
        await create_event(
            db,
            "problem.restored",
            entity_type=problem.entity_type,
            entity_id=problem.entity_id,
            message="A suppressed Problem was restored to the active queue.",
            details={"problem_id": str(problem.id), "reason": problem.reason},
        )
        queue_live_event(
            db,
            "problem.updated",
            entity_type=problem.entity_type,
            entity_id=problem.entity_id,
            data={"problem_id": str(problem.id), "reason": problem.reason},
        )
        return problem

    if problem.status != ProblemStatus.OPEN:
        raise AppError("PROBLEM_ALREADY_RESOLVED", "Problem is no longer open.", status_code=409)

    if action == "dismiss":
        problem.status = ProblemStatus.DISMISSED
        problem.resolution = {"action": action, "suppressed": True, "suppressed_at": utcnow().isoformat()}
        problem.resolved_at = utcnow()
        await _resolution_event(db, problem, "Problem dismissed after manual review.")
        return problem

    if action == "recheck":
        problem.resolution = {"action": "recheck_requested", "requested_at": utcnow().isoformat()}
        queue_live_event(
            db,
            "problem.updated",
            entity_type=problem.entity_type,
            entity_id=problem.entity_id,
            data={"problem_id": str(problem.id), "reason": problem.reason},
        )
        await create_event(
            db,
            "problem.recheck_requested",
            entity_type=problem.entity_type,
            entity_id=problem.entity_id,
            message="A manual evidence recheck was requested.",
            details={"problem_id": str(problem.id), "reason": problem.reason},
        )
        return problem

    if action == "choose_replacement_target":
        release_id = await _choose_replacement_target(db, problem, payload)
        # Reconciliation resolves the Problem when the attachment succeeds;
        # record what the administrator chose on it.
        problem.status = ProblemStatus.RESOLVED
        problem.resolution = {"action": action, "release_id": release_id}
        problem.resolved_at = problem.resolved_at or utcnow()
        await _resolution_event(db, problem, "Replacement target chosen manually.")
        return problem

    if action == "confirm_movie_match":
        await _confirm_movie_match(db, problem, payload, tmdb_client_factory=tmdb_client_factory)
        problem.status = ProblemStatus.RESOLVED
        problem.resolution = {"action": action, "tmdb_id": int(payload["tmdb_id"])}
        problem.resolved_at = utcnow()
        await _resolution_event(db, problem, "Movie identity manually confirmed.")
        return problem

    if action == "confirm_show_match":
        followup_job_id = await _confirm_show_match(
            db,
            problem,
            payload,
            tmdb_client_factory=tmdb_client_factory,
        )
        problem.status = ProblemStatus.RESOLVED
        problem.resolution = {
            "action": action,
            "tmdb_id": int(payload["tmdb_id"]),
        }
        if followup_job_id is not None:
            problem.resolution["followup_job_id"] = str(followup_job_id)
        problem.resolved_at = utcnow()
        await _resolution_event(db, problem, "Show identity manually confirmed.")
        return problem

    raise AppError("PROBLEM_ACTION_NOT_ALLOWED", f"Unsupported resolution action: {action}", status_code=422)


async def _choose_replacement_target(db: AsyncSession, problem: Problem, payload: dict[str, Any]) -> str:
    """Attach the waiting download as the replacement of the chosen release."""

    from app.models.domain import Torrent

    if problem.entity_type != "movie" or problem.entity_id is None:
        raise AppError("PROBLEM_ACTION_NOT_ALLOWED", "Only a Movie can have a replacement target.", status_code=409)
    details = dict(problem.details or {})
    release_id = str(payload.get("release_id") or "")
    candidates = {str(item) for item in details.get("candidate_release_ids") or []}
    if release_id not in candidates:
        raise AppError("RELEASE_NOT_A_CANDIDATE", "Choose one of the missing releases listed on this Problem.", status_code=422)
    release = await db.get(MovieRelease, UUID(release_id))
    if release is None or release.movie_id != problem.entity_id:
        raise AppError("NOT_FOUND", "That release no longer exists.", status_code=404)
    if release.release_state not in {ReleaseState.CURRENT, ReleaseState.MISSING}:
        raise AppError("RELEASE_NOT_MISSING", "That release is no longer waiting for a replacement.", status_code=409)

    def _uuid(key: str) -> UUID | None:
        try:
            return UUID(str(details[key])) if details.get(key) else None
        except ValueError:
            return None

    directory = None
    if _uuid("directory_id"):
        directory = await db.get(MediaDirectory, _uuid("directory_id"))
    if directory is None and details.get("path"):
        directory = await db.scalar(select(MediaDirectory).where(MediaDirectory.resolved_path == str(details["path"])))
    if directory is None or directory.storage_root_id is None:
        raise AppError("NOT_FOUND", "The new download's folder is no longer known. Recheck this Problem.", status_code=404)
    root = await db.get(StorageRoot, directory.storage_root_id)
    if root is None:
        raise AppError("NOT_FOUND", "The download's media folder no longer exists.", status_code=404)
    try:
        observation = FilesystemObserver().inspect_directory(Path(directory.resolved_path), Path(root.resolved_root_path))
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        raise AppError("MEDIA_PATH_UNAVAILABLE", f"Could not inspect the new download: {exc}", status_code=409) from exc

    movie = await db.get(Movie, problem.entity_id)
    torrent = await db.get(Torrent, _uuid("torrent_id")) if _uuid("torrent_id") else None
    incoming = await db.get(MovieRelease, _uuid("incoming_release_id")) if _uuid("incoming_release_id") else None
    result = await reconcile_movie_directory(
        db,
        root,
        observation,
        torrent=torrent,
        movie_hint=movie,
        incoming_release=incoming,
        replacement_of_release_id=release_id,
    )
    await db.refresh(release)
    if result != "matched" or release.release_state != ReleaseState.REPLACED:
        raise AppError(
            "REPLACEMENT_FAILED",
            "The download could not replace that release. Recheck this Problem to see its current state.",
            status_code=409,
        )
    return release_id


async def _resolution_event(db: AsyncSession, problem: Problem, message: str) -> None:
    await create_event(
        db,
        "problem.resolved",
        entity_type=problem.entity_type,
        entity_id=problem.entity_id,
        message=message,
        details={"problem_id": str(problem.id), "reason": problem.reason, "resolution": problem.resolution or {}},
    )


async def _tmdb_movie(db: AsyncSession, tmdb_id: int, factory: Callable[..., Any] | None) -> Any:
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before manually matching a Movie.", status_code=409)
    from app.integrations.tmdb import TMDBClient

    client = (factory or TMDBClient)(configuration.api_key)
    try:
        return await client.get_movie(tmdb_id)
    except Exception as exc:
        raise AppError("TMDB_UNAVAILABLE", f"Could not load the selected TMDB Movie: {exc}", status_code=503) from exc
    finally:
        await client.close()


async def _confirm_movie_match(
    db: AsyncSession,
    problem: Problem,
    payload: dict[str, Any],
    *,
    tmdb_client_factory: Callable[..., Any] | None,
) -> None:
    try:
        tmdb_id = int(payload["tmdb_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("TMDB_ID_REQUIRED", "confirm_movie_match requires tmdb_id.", status_code=422) from exc
    metadata = await _tmdb_movie(db, tmdb_id, tmdb_client_factory)
    movie = await db.scalar(select(Movie).where(Movie.tmdb_id == tmdb_id))

    if problem.entity_type == "movie":
        target = await db.get(Movie, problem.entity_id) if problem.entity_id else None
        if target is None:
            raise AppError("NOT_FOUND", "Affected Movie no longer exists.", status_code=404)
        # Same write as the Movie page's Change match, through one helper, so
        # the two ways of re-pointing a Movie cannot drift apart.
        await apply_movie_identity(db, target, tmdb_id, metadata=metadata)
        return
    if problem.entity_type == "media_directory":
        directory = await db.get(MediaDirectory, problem.entity_id) if problem.entity_id else None
        if directory is None:
            raise AppError("NOT_FOUND", "Affected media directory no longer exists.", status_code=404)
        if movie is None:
            movie = Movie(
                title=metadata.title,
                sort_title=metadata.title.casefold(),
                year=metadata.year,
                tmdb_id=metadata.tmdb_id,
                overview=metadata.overview,
                poster_ref=metadata.poster_path,
                monitored=True,
                identity_state=IdentityState.MANUAL,
                manual_identity_override=True,
                metadata_refreshed_at=utcnow(),
            )
            db.add(movie)
            await db.flush()
        root = await db.get(StorageRoot, directory.storage_root_id)
        if root is None:
            raise AppError("NOT_FOUND", "The directory's media folder no longer exists.", status_code=404)
        try:
            observation = FilesystemObserver().inspect_directory(Path(directory.resolved_path), Path(root.resolved_root_path))
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
            raise AppError("MEDIA_PATH_UNAVAILABLE", f"Could not inspect the selected directory: {exc}", status_code=409) from exc
        result = await reconcile_movie_directory(db, root, observation, movie_hint=movie, manual_identity=True)
        if result not in {"matched", "duplicates", "conflicts"}:
            raise AppError("MANUAL_MATCH_FAILED", "The selected Movie could not be attached to this directory.", status_code=409)
    else:
        raise AppError("PROBLEM_ACTION_NOT_ALLOWED", "This problem cannot be resolved as a Movie match.", status_code=409)

    await apply_movie_identity(db, movie, tmdb_id, metadata=metadata)


async def _confirm_show_match(
    db: AsyncSession,
    problem: Problem,
    payload: dict[str, Any],
    *,
    tmdb_client_factory: Callable[..., Any] | None,
) -> UUID | None:
    try:
        tmdb_id = int(payload["tmdb_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("TMDB_ID_REQUIRED", "confirm_show_match requires tmdb_id.", status_code=422) from exc
    show = await db.scalar(select(Show).where(Show.tmdb_id == tmdb_id))
    directory_id: UUID | None = None

    if problem.entity_type == "show":
        target = await db.get(Show, problem.entity_id) if problem.entity_id else None
        if target is None:
            raise AppError("NOT_FOUND", "Affected Show no longer exists.", status_code=404)
        if show is not None and show.id != target.id:
            raise AppError("TMDB_ID_ALREADY_USED", "That TMDB Show is already represented by another library record.", status_code=409)
        if show is None:
            # Existing logical record remains authoritative; metadata refresh
            # remains an explicit Show action rather than part of identity correction.
            target.tmdb_id = tmdb_id
            show = target
    elif problem.entity_type == "media_directory":
        directory = await db.get(MediaDirectory, problem.entity_id) if problem.entity_id else None
        if directory is None:
            raise AppError("NOT_FOUND", "Affected media directory no longer exists.", status_code=404)
        directory_id = directory.id
        if show is None:
            metadata = await _tmdb_show(db, tmdb_id, tmdb_client_factory)
            show = Show(
                title=metadata.title,
                year=metadata.year,
                tmdb_id=metadata.tmdb_id,
                tvdb_id=metadata.tvdb_id,
                overview=metadata.overview,
                poster_ref=metadata.poster_path,
                monitored=True,
                identity_state=IdentityState.MANUAL,
                manual_identity_override=True,
            )
            db.add(show)
            await db.flush()
        root = await db.get(StorageRoot, directory.storage_root_id)
        if root is None:
            raise AppError("NOT_FOUND", "The directory's media folder no longer exists.", status_code=404)
    else:
        raise AppError("PROBLEM_ACTION_NOT_ALLOWED", "This problem cannot be resolved as a Show match.", status_code=409)

    show.identity_state = IdentityState.MANUAL
    show.manual_identity_override = True
    show.revision += 1
    if directory_id is None:
        return None

    job = await create_job(
        db,
        "confirmed_show_reconciliation",
        cancellable=False,
        summary={
            "show_id": str(show.id),
            "show_title": show.title,
            "directory_id": str(directory_id) if directory_id else None,
            "source_problem_id": str(problem.id),
            "message": f"Importing metadata and matching files for {show.title}…",
        },
    )
    return job.id


async def _tmdb_show(db: AsyncSession, tmdb_id: int, factory: Callable[..., Any] | None) -> Any:
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before manually matching a Show.", status_code=409)
    from app.integrations.tmdb import TMDBClient

    client = (factory or TMDBClient)(configuration.api_key)
    try:
        return await client.get_show(tmdb_id)
    except Exception as exc:
        raise AppError("TMDB_UNAVAILABLE", f"Could not load the selected TMDB Show: {exc}", status_code=503) from exc
    finally:
        await client.close()


async def run_confirmed_show_reconciliation(
    job_id: UUID,
    *,
    tmdb_client_factory: Callable[..., Any] | None = None,
) -> None:
    """Finish a confirmed Show import without holding open the Problems action."""

    async def worker(db: AsyncSession, job) -> None:
        summary = dict(job.summary or {})
        try:
            show_id = UUID(str(summary["show_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise JobFailure("SHOW_ID_REQUIRED", "The confirmed Show follow-up job has no valid Show id.") from exc
        show = await db.get(Show, show_id)
        if show is None:
            raise JobFailure("SHOW_NOT_FOUND", "The confirmed Show no longer exists.")

        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"current": 0, "total": 2, "percent": 0, "stage": "importing_metadata", "detail": f"Importing TMDB metadata for {show.title}…"},
        )
        try:
            counts = await sync_show_metadata(db, show, client_factory=tmdb_client_factory)
        except Exception as exc:
            await db.rollback()
            raise JobFailure("TMDB_SHOW_METADATA_FAILED", str(exc)) from exc

        await checkpoint_job(
            db,
            job,
            progress={"current": 1, "total": 2, "percent": 50, "stage": "matching_files", "detail": f"Matching files for {show.title}…"},
        )

        result = "metadata_only"
        directory_value = summary.get("directory_id")
        if directory_value:
            try:
                directory_id = UUID(str(directory_value))
            except ValueError as exc:
                raise JobFailure("DIRECTORY_ID_INVALID", "The confirmed Show follow-up job has an invalid directory id.") from exc
            directory = await db.get(MediaDirectory, directory_id)
            if directory is None:
                raise JobFailure("DIRECTORY_NOT_FOUND", "The matched Show directory no longer exists.")
            root = await db.get(StorageRoot, directory.storage_root_id)
            if root is None:
                raise JobFailure("STORAGE_ROOT_NOT_FOUND", "The matched Show directory has no media folder.")
            try:
                observation = FilesystemObserver().inspect_directory(
                    Path(directory.resolved_path),
                    Path(root.resolved_root_path),
                )
                result = await reconcile_show_directory(db, root, observation, show_hint=show)
                if result not in {"matched", "review", "partial", "duplicates"}:
                    raise RuntimeError(f"Show file matching returned {result}.")
            except Exception as exc:
                await db.rollback()
                raise JobFailure("SHOW_FILE_MATCHING_FAILED", str(exc)) from exc

        await create_event(
            db,
            "show.confirmed_reconciliation_completed",
            entity_type="show",
            entity_id=show.id,
            message=f"Imported metadata and matched files for {show.title}.",
            details={**counts, "result": result},
        )
        completed = {
            **summary,
            **counts,
            "result": result,
            "message": f"Metadata import and file matching completed for {show.title}.",
        }
        await checkpoint_job(
            db,
            job,
            status=JobStatus.COMPLETED,
            progress={"current": 2, "total": 2, "percent": 100, "stage": "completed", "detail": completed["message"]},
            summary=completed,
        )

    await run_job(
        job_id,
        worker,
        failure_code="CONFIRMED_SHOW_RECONCILIATION_FAILED",
        failure_message="Could not finish the confirmed Show import.",
        failure_progress={"current": 0, "total": 2, "percent": 0, "stage": "failed", "detail": "Confirmed Show follow-up failed."},
    )


async def _load_movie_for_duplicate(db: AsyncSession, resource_id: str) -> Movie:
    statement = (
        select(Movie)
        .options(
            selectinload(Movie.releases).selectinload(MovieRelease.directories).selectinload(MediaDirectory.files),
            selectinload(Movie.releases).selectinload(MovieRelease.quality_definition),
        )
    )
    if resource_id.isdigit():
        statement = statement.where(Movie.tmdb_id == int(resource_id))
    else:
        try:
            movie_id = UUID(resource_id)
        except ValueError as exc:
            raise AppError("NOT_FOUND", "Movie was not found.", status_code=404) from exc
        statement = statement.where(Movie.id == movie_id)
    movie = (await db.scalars(statement)).unique().one_or_none()
    if movie is None:
        raise AppError("NOT_FOUND", "Movie was not found.", status_code=404)
    return movie


def _inside_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _canonical_manual_edition(value: str | None) -> str:
    edition = (value or "").strip()
    if not edition:
        raise AppError("EDITION_REQUIRED", "Choose an edition or enter a custom edition name.", status_code=422)
    canonical = next((item for item in EDITION_OPTIONS if item.casefold() == edition.casefold()), None)
    return canonical or edition


async def update_movie_release_editions(
    db: AsyncSession,
    resource_id: str,
    updates: list[Any],
) -> dict[str, Any]:
    """Apply edition choices together, then recompute duplicate evidence once."""

    movie = await _load_movie_for_duplicate(db, resource_id)
    update_ids = [item.release_id for item in updates]
    if len(set(update_ids)) != len(update_ids):
        raise AppError("DUPLICATE_EDITION_UPDATE", "Each movie copy can only be updated once.", status_code=422)

    releases = (
        await db.scalars(
            select(MovieRelease)
            .options(selectinload(MovieRelease.directories))
            .where(MovieRelease.movie_id == movie.id, MovieRelease.id.in_(update_ids))
            .with_for_update()
        )
    ).unique().all()
    by_id = {release.id: release for release in releases}
    if len(by_id) != len(update_ids):
        raise AppError("INVALID_EDITION_RELEASE", "Every selected copy must belong to this movie.", status_code=409)

    changed: list[dict[str, Any]] = []
    for update in updates:
        release = by_id[update.release_id]
        if release.release_state not in {ReleaseState.CURRENT, ReleaseState.DUPLICATE} or not any(
            directory.exists for directory in release.directories
        ):
            raise AppError("EDITION_RELEASE_NOT_PRESENT", "A selected movie copy is no longer present.", status_code=409)

        previous = release.effective_edition
        if update.mode == "automatic":
            release.edition_override_active = False
            release.manual_edition_override = None
            release.effective_edition = release.parsed_edition
        elif update.mode == "standard":
            release.edition_override_active = True
            release.manual_edition_override = None
            release.effective_edition = None
        else:
            edition = _canonical_manual_edition(update.edition)
            release.edition_override_active = True
            release.manual_edition_override = edition
            release.effective_edition = edition
        changed.append(
            {
                "release_id": str(release.id),
                "previous_edition": previous,
                "effective_edition": release.effective_edition,
                "mode": update.mode,
            }
        )

    await db.flush()
    await refresh_movie_duplicate_problem(db, movie.id)
    movie.revision += 1
    await create_event(
        db,
        "movie.editions_changed",
        entity_type="movie",
        entity_id=movie.id,
        message=f"Updated movie edition assignments for {movie.title}.",
        details={"updates": changed},
    )
    await db.flush()

    duplicate_open = await db.scalar(
        select(Problem.id).where(
            Problem.reason == "DUPLICATE_PHYSICAL_RELEASE",
            Problem.entity_type == "movie",
            Problem.entity_id == movie.id,
            Problem.status == ProblemStatus.OPEN,
        )
    )
    ordered = sorted(releases, key=lambda item: item.raw_release_name.casefold())
    return {
        "movie_id": movie.id,
        "duplicate_resolved": duplicate_open is None,
        "releases": [
            {
                "release_id": release.id,
                "parsed_edition": release.parsed_edition,
                "edition_override_active": bool(
                    release.edition_override_active or release.manual_edition_override is not None
                ),
                "manual_edition_override": release.manual_edition_override,
                "effective_edition": release.effective_edition,
                "state": release.release_state.value,
            }
            for release in ordered
        ],
    }
