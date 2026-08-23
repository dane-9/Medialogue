from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.core.security import sign_confirmation_payload, verify_confirmation_payload
from app.integrations.filesystem import FilesystemObserver
from app.models.domain import (
    AccessMode,
    AssociationType,
    Episode,
    EpisodeMediaMap,
    IdentityState,
    JobStatus,
    MediaDirectory,
    MediaFile,
    MatchMethod,
    MediaType,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    Problem,
    ProblemStatus,
    ReleaseState,
    Severity,
    Show,
    StorageRoot,
    Torrent,
    TorrentArchiveState,
    TorrentClientObservation,
    utcnow,
)
from app.services.events import create_event, queue_live_event
from app.services.jobs import JobFailure, checkpoint_job, create_job, run_job
from app.services.reconciliation import open_problem, reconcile_movie_directory, resolve_problem
from app.services.shows import reconcile_show_directory
from app.services.tmdb import get_tmdb_configuration, sync_show_metadata

CONFIRMATION_TTL_SECONDS = 10 * 60


def available_actions(problem: Problem) -> list[str]:
    if problem.status == ProblemStatus.DISMISSED:
        return ["restore"]
    if problem.status != ProblemStatus.OPEN:
        return []
    reason = problem.reason
    if "DUPLICATE" in reason:
        return ["dismiss", "recheck"]
    if problem.entity_type in {"media_directory", "movie"} and reason in {
        "LOW_CONFIDENCE_MATCH",
        "TMDB_MATCH_REQUIRED",
        "TMDB_IDENTITY_UNRESOLVED",
        "PLEX_IDENTITY_MISMATCH",
    }:
        return ["confirm_movie_match", "dismiss", "recheck"]
    if problem.entity_type in {"media_directory", "show"} and reason in {
        "TMDB_SHOW_MATCH_REQUIRED",
        "TMDB_SHOW_IDENTITY_UNRESOLVED",
        "PLEX_IDENTITY_MISMATCH",
    }:
        return ["confirm_show_match", "dismiss", "recheck"]
    if reason in {"PATH_MAPPING_FAILED", "TORRENT_PATH_NOT_FOUND", "ROOT_UNREACHABLE"}:
        return ["dismiss", "recheck"]
    return ["dismiss", "recheck"]


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
        # Recheck is intentionally not equivalent to resolution. The normal
        # observer/reconciliation pass closes the Problem only when evidence
        # actually changes.
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
        if movie is not None and movie.id != target.id:
            raise AppError("TMDB_ID_ALREADY_USED", "That TMDB Movie is already represented by another library record.", status_code=409)
        movie = target
    elif problem.entity_type == "media_directory":
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
            raise AppError("NOT_FOUND", "The directory storage root no longer exists.", status_code=404)
        try:
            observation = FilesystemObserver().inspect_directory(Path(directory.resolved_path), Path(root.resolved_root_path))
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
            raise AppError("MEDIA_PATH_UNAVAILABLE", f"Could not inspect the selected directory: {exc}", status_code=409) from exc
        result = await reconcile_movie_directory(db, root, observation, movie_hint=movie, manual_identity=True)
        if result not in {"matched", "duplicates", "conflicts"}:
            raise AppError("MANUAL_MATCH_FAILED", "The selected Movie could not be attached to this directory.", status_code=409)
    else:
        raise AppError("PROBLEM_ACTION_NOT_ALLOWED", "This problem cannot be resolved as a Movie match.", status_code=409)

    movie.title = metadata.title
    movie.sort_title = metadata.title.casefold()
    movie.year = metadata.year
    movie.tmdb_id = metadata.tmdb_id
    movie.overview = metadata.overview
    movie.poster_ref = metadata.poster_path
    movie.identity_state = IdentityState.MANUAL
    movie.manual_identity_override = True
    movie.metadata_refreshed_at = utcnow()
    movie.revision += 1


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
            raise AppError("NOT_FOUND", "The directory storage root no longer exists.", status_code=404)
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
            "message": f"Importing metadata and reconciling files for {show.title}…",
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
            show = await db.get(Show, show_id)
            if show is not None:
                await open_problem(
                    db,
                    reason="TMDB_SHOW_METADATA_UNAVAILABLE",
                    entity_type="show",
                    entity_id=show.id,
                    message="Show identity is confirmed, but TMDB episode metadata could not be refreshed.",
                    details={"tmdb_id": show.tmdb_id, "error": str(exc)},
                )
                await db.commit()
            raise JobFailure("TMDB_SHOW_METADATA_FAILED", str(exc)) from exc

        await resolve_problem(db, "TMDB_SHOW_METADATA_UNAVAILABLE", "show", show.id)
        await checkpoint_job(
            db,
            job,
            progress={"current": 1, "total": 2, "percent": 50, "stage": "reconciling_directory", "detail": f"Reconciling files for {show.title}…"},
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
                raise JobFailure("STORAGE_ROOT_NOT_FOUND", "The matched Show directory has no storage root.")
            try:
                observation = FilesystemObserver().inspect_directory(
                    Path(directory.resolved_path),
                    Path(root.resolved_root_path),
                )
                result = await reconcile_show_directory(db, root, observation, show_hint=show)
                if result not in {"matched", "review", "partial", "duplicates"}:
                    raise RuntimeError(f"Show directory reconciliation returned {result}.")
                await resolve_problem(db, "SHOW_DIRECTORY_RECONCILIATION_FAILED", "media_directory", directory.id)
            except Exception as exc:
                await db.rollback()
                directory = await db.get(MediaDirectory, directory_id)
                if directory is not None:
                    await open_problem(
                        db,
                        reason="SHOW_DIRECTORY_RECONCILIATION_FAILED",
                        entity_type="media_directory",
                        entity_id=directory.id,
                        message="Show identity is confirmed, but its files could not be reconciled.",
                        details={"show_id": str(show_id), "path": directory.resolved_path, "error": str(exc)},
                    )
                    await db.commit()
                raise JobFailure("SHOW_DIRECTORY_RECONCILIATION_FAILED", str(exc)) from exc

        await create_event(
            db,
            "show.confirmed_reconciliation_completed",
            entity_type="show",
            entity_id=show.id,
            message=f"Imported metadata and reconciled files for {show.title}.",
            details={**counts, "result": result},
        )
        completed = {
            **summary,
            **counts,
            "result": result,
            "message": f"Metadata and file reconciliation completed for {show.title}.",
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


async def _choose_episode_winner(db: AsyncSession, problem: Problem, payload: dict[str, Any]) -> bool:
    if problem.reason != "DUPLICATE_EPISODE_RELEASE" or problem.entity_type != "episode" or problem.entity_id is None:
        raise AppError("PROBLEM_ACTION_NOT_ALLOWED", "This action only applies to episode duplicates.", status_code=409)
    try:
        winner_id = UUID(str(payload["winner_media_file_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("WINNER_REQUIRED", "winner_media_file_id is required.", status_code=422) from exc
    mapped = (
        await db.scalars(
            select(EpisodeMediaMap)
            .join(MediaFile, MediaFile.id == EpisodeMediaMap.media_file_id)
            .where(EpisodeMediaMap.episode_id == problem.entity_id, MediaFile.exists.is_(True))
        )
    ).all()
    ids = {item.media_file_id for item in mapped}
    verified_ids: set[UUID] = set()
    for value in (problem.details or {}).get("media_file_ids", []):
        try:
            verified_ids.add(UUID(str(value)))
        except (TypeError, ValueError):
            continue
    if winner_id not in ids or (verified_ids and winner_id not in verified_ids):
        raise AppError("INVALID_DUPLICATE_WINNER", "The selected file is not one of the current physical duplicates.", status_code=409)
    previous_id: UUID | None = None
    try:
        previous_id = UUID(str((problem.resolution or {}).get("winner_media_file_id")))
    except (TypeError, ValueError):
        pass
    if (
        previous_id is not None
        and previous_id != winner_id
        and bool((problem.resolution or {}).get("winner_manual_override_applied"))
    ):
        previous = next((item for item in mapped if item.media_file_id == previous_id), None)
        if previous is not None:
            previous.manual_override = False
            previous.match_method = MatchMethod.PARSER
    # Mark the chosen mapping as manual authority but leave all physical maps
    # intact. This is intentionally not a fake resolution: the Problem stays
    # open until the losing file really disappears.
    winner = next(item for item in mapped if item.media_file_id == winner_id)
    override_applied = not winner.manual_override
    winner.manual_override = True
    winner.match_method = MatchMethod.MANUAL
    problem.details = {**dict(problem.details or {}), "preferred_media_file_id": str(winner_id)}
    return override_applied


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


def _fresh_inventory(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink():
        raise AppError("UNSAFE_DELETE_TARGET", "Refusing to delete a media-directory symlink.", status_code=409)
    if not path.is_dir():
        raise AppError("UNSAFE_DELETE_TARGET", "The stored media directory is not a directory.", status_code=409)
    entries: list[dict[str, Any]] = []
    for child in sorted(path.rglob("*"), key=lambda item: str(item).casefold()):
        relative = str(child.relative_to(path))
        is_symlink = child.is_symlink()
        if is_symlink:
            entries.append({"relative_path": relative, "size": None, "is_symlink": True})
            continue
        if child.is_file():
            try:
                size = child.stat().st_size
            except OSError:
                size = None
            entries.append({"relative_path": relative, "size": size, "is_symlink": False})
    return entries


def _inventory_digest(releases: list[dict[str, Any]]) -> str:
    raw = json.dumps(releases, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def _release_preview(db: AsyncSession, release: MovieRelease, *, inventory: bool) -> dict[str, Any]:
    directories: list[dict[str, Any]] = []
    for directory in release.directories:
        root = await db.get(StorageRoot, directory.storage_root_id)
        path = Path(directory.resolved_path)
        files = _fresh_inventory(path) if inventory and path.exists() else []
        directories.append(
            {
                "directory_id": directory.id,
                "path": directory.resolved_path,
                "storage_root": root.name if root else "Unknown root",
                "access_mode": root.access_mode.value if root else "unknown",
                "exists": path.exists(),
                "files": files,
            }
        )

    torrent_rows = (
        await db.execute(
            select(Torrent, MovieReleaseTorrent)
            .join(MovieReleaseTorrent, MovieReleaseTorrent.torrent_id == Torrent.id)
            .where(MovieReleaseTorrent.movie_release_id == release.id)
        )
    ).all()
    torrents: list[dict[str, Any]] = []
    for torrent, _association in torrent_rows:
        observation_rows = (
            await db.scalars(
                select(TorrentClientObservation).where(TorrentClientObservation.torrent_id == torrent.id)
            )
        ).all()
        present = [
            (obs, client)
            for obs in observation_rows
            if obs.is_present
            if (client := get_integration_config_store().get_download_client(obs.download_client_id)) is not None
        ]
        torrents.append(
            {
                "torrent_id": torrent.id,
                "info_hash": torrent.info_hash,
                "name": torrent.name,
                "archived": torrent.archive_state == TorrentArchiveState.ARCHIVED,
                "qbit_present": bool(present),
                "clients": [client.name for _, client in present],
            }
        )
    return {
        "release_id": release.id,
        "release_name": release.raw_release_name,
        "edition": release.effective_edition,
        "quality": release.quality_definition.name if release.quality_definition else None,
        "release_group": release.release_group,
        "state": release.release_state.value,
        "directories": directories,
        "torrents": torrents,
    }


async def duplicate_preview(
    db: AsyncSession,
    resource_id: str,
    winner_release_id: UUID,
    losing_release_ids: list[UUID],
    *,
    delete_media: bool,
    remove_torrents: bool,
) -> dict[str, Any]:
    movie = await _load_movie_for_duplicate(db, resource_id)
    unique_losers = list(dict.fromkeys(losing_release_ids))
    if winner_release_id in unique_losers:
        raise AppError("INVALID_DUPLICATE_SELECTION", "The winner cannot also be a losing release.", status_code=422)
    releases = {item.id: item for item in movie.releases}
    winner = releases.get(winner_release_id)
    losers = [releases.get(item) for item in unique_losers]
    if winner is None or any(item is None for item in losers):
        raise AppError("INVALID_DUPLICATE_SELECTION", "Every selected release must belong to this Movie.", status_code=409)
    losers = [item for item in losers if item is not None]

    problem = await db.scalar(
        select(Problem).where(
            Problem.reason == "DUPLICATE_PHYSICAL_RELEASE",
            Problem.entity_type == "movie",
            Problem.entity_id == movie.id,
            Problem.status == ProblemStatus.OPEN,
        )
    )
    if problem is None:
        raise AppError("DUPLICATE_NO_LONGER_PRESENT", "This Movie no longer has an open physical-duplicate problem.", status_code=409)

    candidate_ids = {UUID(value) for value in problem.details.get("release_ids", []) if _is_uuid(value)}
    if candidate_ids and (winner.id not in candidate_ids or not set(unique_losers).issubset(candidate_ids)):
        raise AppError("STALE_DUPLICATE_SELECTION", "The duplicate candidates changed; refresh before resolving.", status_code=409)

    warnings: list[str] = []
    winner_preview = await _release_preview(db, winner, inventory=False)
    if not any(directory["exists"] for directory in winner_preview["directories"]):
        raise AppError(
            "DUPLICATE_WINNER_NOT_PRESENT",
            "The selected winning release is no longer physically present; refresh the duplicate before resolving it.",
            status_code=409,
        )
    loser_previews = [await _release_preview(db, loser, inventory=delete_media) for loser in losers]

    if delete_media:
        for loser, preview in zip(losers, loser_previews, strict=True):
            if not loser.directories:
                warnings.append(f"{loser.raw_release_name} has no associated media directory to delete.")
            for directory in loser.directories:
                root = await db.get(StorageRoot, directory.storage_root_id)
                if root is None or root.access_mode != AccessMode.READ_WRITE:
                    raise AppError("ROOT_READ_ONLY", f"Storage root for {directory.resolved_path} is not configured read-write.", status_code=409)
                path = Path(directory.resolved_path)
                root_path = Path(root.resolved_root_path)
                if not _inside_root(path, root_path) or path.resolve() == root_path.resolve():
                    raise AppError("UNSAFE_DELETE_TARGET", "A losing directory is not a safe child directory of its configured storage root.", status_code=409)
    if remove_torrents:
        for preview in loser_previews:
            for torrent in preview["torrents"]:
                if torrent["qbit_present"] and not torrent["archived"]:
                    raise AppError(
                        "TORRENT_ARCHIVE_REQUIRED",
                        f"Torrent {torrent['info_hash']} must be archived before it can be removed from qBittorrent.",
                        status_code=409,
                    )

    expiry = int(time.time()) + CONFIRMATION_TTL_SECONDS
    inventory_hash = _inventory_digest(loser_previews)
    token_payload = {
        "purpose": "resolve_movie_duplicate",
        "movie_id": str(movie.id),
        "winner_release_id": str(winner.id),
        "losing_release_ids": [str(item.id) for item in losers],
        "delete_media": delete_media,
        "remove_torrents": remove_torrents,
        "inventory_hash": inventory_hash,
        "exp": expiry,
    }
    token = sign_confirmation_payload(token_payload, get_settings().secret_key)
    return {
        "movie_id": movie.id,
        "movie_title": f"{movie.title} ({movie.year})" if movie.year else movie.title,
        "winner": winner_preview,
        "losers": loser_previews,
        "delete_media": delete_media,
        "remove_torrents": remove_torrents,
        "torrent_backups_will_be_kept": True,
        "confirmation_token": token,
        "expires_at": _iso_from_epoch(expiry),
        "warnings": warnings,
    }


def _is_uuid(value: object) -> bool:
    try:
        UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def _iso_from_epoch(value: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


async def commit_duplicate_resolution(
    db: AsyncSession,
    resource_id: str,
    confirmation_token: str,
    *,
    qbit_client_factory: Callable[..., Any],
) -> dict[str, Any]:
    payload = verify_confirmation_payload(confirmation_token, get_settings().secret_key)
    if payload is None or payload.get("purpose") != "resolve_movie_duplicate":
        raise AppError("INVALID_CONFIRMATION", "Duplicate confirmation token is invalid.", status_code=409)
    try:
        exp = int(payload["exp"])
        movie_id = UUID(str(payload["movie_id"]))
        winner_id = UUID(str(payload["winner_release_id"]))
        loser_ids = [UUID(str(item)) for item in payload["losing_release_ids"]]
        delete_media = bool(payload["delete_media"])
        remove_torrents = bool(payload["remove_torrents"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError("INVALID_CONFIRMATION", "Duplicate confirmation token is malformed.", status_code=409) from exc
    if exp < int(time.time()):
        raise AppError("CONFIRMATION_EXPIRED", "Duplicate preview expired; review the current filesystem again.", status_code=409)

    movie = await _load_movie_for_duplicate(db, resource_id)
    if movie.id != movie_id:
        raise AppError("INVALID_CONFIRMATION", "Confirmation token belongs to another Movie.", status_code=409)
    releases = {item.id: item for item in movie.releases}
    winner = releases.get(winner_id)
    losers = [releases.get(item) for item in loser_ids]
    if winner is None or any(item is None for item in losers):
        raise AppError("STALE_DUPLICATE_SELECTION", "Duplicate release state changed; generate a new preview.", status_code=409)
    losers = [item for item in losers if item is not None]

    fresh_previews = [await _release_preview(db, loser, inventory=delete_media) for loser in losers]
    if _inventory_digest(fresh_previews) != payload.get("inventory_hash"):
        raise AppError("DELETE_PREVIEW_STALE", "The losing media changed since preview; review the directory contents again.", status_code=409)

    problem = await db.scalar(
        select(Problem).where(
            Problem.reason == "DUPLICATE_PHYSICAL_RELEASE",
            Problem.entity_type == "movie",
            Problem.entity_id == movie.id,
            Problem.status == ProblemStatus.OPEN,
        )
    )
    if problem is None:
        raise AppError("DUPLICATE_NO_LONGER_PRESENT", "The duplicate problem is no longer open.", status_code=409)

    # Never delete the selected losing copy if the winner disappeared after
    # preview.  A duplicate decision must always retain at least one physical
    # copy unless the user starts a completely separate removal workflow.
    fresh_winner = await _release_preview(db, winner, inventory=False)
    if not any(directory["exists"] for directory in fresh_winner["directories"]):
        raise AppError(
            "DUPLICATE_WINNER_NOT_PRESENT",
            "The selected winning release disappeared after preview; no losing media was deleted.",
            status_code=409,
        )

    winner.release_state = ReleaseState.CURRENT
    winner.became_current_at = winner.became_current_at or utcnow()
    deleted_directories: list[str] = []
    removed_torrents: list[str] = []
    warnings: list[str] = []
    qbit_removal_failed = False

    # qBittorrent removal is deliberately attempted before filesystem
    # deletion when the user selected both. qBittorrent is told never to
    # delete data; if removal fails, the reviewed filesystem deletion is
    # skipped so a partial cross-system failure cannot destroy the media.
    if remove_torrents:
        torrent_rows = (
            await db.execute(
                select(Torrent, TorrentClientObservation)
                .join(MovieReleaseTorrent, MovieReleaseTorrent.torrent_id == Torrent.id)
                .join(TorrentClientObservation, TorrentClientObservation.torrent_id == Torrent.id)
                .where(
                    MovieReleaseTorrent.movie_release_id.in_([item.id for item in losers]),
                    TorrentClientObservation.is_present.is_(True),
                )
            )
        ).all()
        # Validate every archive before producing any external side effect.
        for torrent, _observation in torrent_rows:
            if torrent.archive_state != TorrentArchiveState.ARCHIVED:
                raise AppError("TORRENT_ARCHIVE_REQUIRED", "A selected torrent is no longer safely archived.", status_code=409)
        for torrent, observation in torrent_rows:
            client = get_integration_config_store().get_download_client(observation.download_client_id)
            if client is None or not client.enabled:
                warnings.append(f"qBittorrent client {observation.download_client_id} is no longer configured; torrent was not removed.")
                qbit_removal_failed = True
                continue
            adapter = None
            try:
                adapter = qbit_client_factory(client.url, client.username or "", client.password or "")
                await adapter.remove_torrent(torrent.info_hash, delete_files=False)
                observation.is_present = False
                observation.removed_at = utcnow()
                removed_torrents.append(torrent.info_hash)
                await resolve_problem(db, "QBIT_REMOVE_FAILED", "torrent", torrent.id)
                await create_event(
                    db,
                    "torrent.removed_by_user",
                    entity_type="torrent",
                    entity_id=torrent.id,
                    message="Removed duplicate torrent from qBittorrent while retaining its archive.",
                    details={"client_id": str(client.id), "info_hash": torrent.info_hash, "archive_retained": True},
                )
            except Exception as exc:
                qbit_removal_failed = True
                warnings.append(f"Could not remove {torrent.info_hash} from {client.name}: {exc}")
                await open_problem(
                    db,
                    reason="QBIT_REMOVE_FAILED",
                    entity_type="torrent",
                    entity_id=torrent.id,
                    severity=Severity.WARNING,
                    message="qBittorrent removal failed during duplicate resolution.",
                    details={"client": client.name, "error": str(exc), "archive_retained": True},
                )
            finally:
                if adapter is not None:
                    await adapter.close()

    perform_media_delete = delete_media and not qbit_removal_failed
    if delete_media and qbit_removal_failed:
        warnings.append("Media deletion was skipped because at least one requested qBittorrent removal failed. Review the remaining duplicate and retry when qBittorrent is reachable.")

    if perform_media_delete:
        for loser in losers:
            for directory in loser.directories:
                root = await db.get(StorageRoot, directory.storage_root_id)
                if root is None or root.access_mode != AccessMode.READ_WRITE:
                    raise AppError("ROOT_READ_ONLY", "Storage root is no longer writable; generate a new preview.", status_code=409)
                path = Path(directory.resolved_path)
                root_path = Path(root.resolved_root_path)
                if not _inside_root(path, root_path) or path.resolve() == root_path.resolve() or path.is_symlink():
                    raise AppError("UNSAFE_DELETE_TARGET", "Refusing to delete a path that is not a safe child directory of the configured storage root.", status_code=409)
                if path.exists():
                    await asyncio.to_thread(shutil.rmtree, path)
                    deleted_directories.append(str(path))
                directory.exists = False
                directory.missing_since = directory.missing_since or utcnow()
                directory.last_exists_check_at = utcnow()
                directory.missing_check_count = max(directory.missing_check_count, root.missing_grace_checks)
                for media_file in directory.files:
                    media_file.exists = False
                    media_file.missing_since = media_file.missing_since or utcnow()
                    media_file.last_exists_check_at = utcnow()
            loser.release_state = ReleaseState.REMOVED
            loser.removed_at = utcnow()

    if not perform_media_delete:
        for loser in losers:
            if loser.release_state != ReleaseState.REMOVED:
                loser.release_state = ReleaseState.DUPLICATE

    physical_losers = [
        loser for loser in losers if any(Path(directory.resolved_path).exists() for directory in loser.directories)
    ]
    duplicate_resolved = not physical_losers
    if duplicate_resolved:
        problem.status = ProblemStatus.RESOLVED
        problem.resolved_at = utcnow()
        problem.resolution = {
            "action": "choose_duplicate_winner",
            "winner_release_id": str(winner.id),
            "losing_release_ids": [str(item.id) for item in losers],
            "deleted_media": bool(deleted_directories),
            "removed_torrents": bool(removed_torrents),
            "torrent_archive_retained": True,
        }
        await create_event(
            db,
            "duplicate.resolved",
            entity_type="movie",
            entity_id=movie.id,
            message=f"Resolved duplicate releases for {movie.title}; selected the retained copy explicitly.",
            details=problem.resolution,
        )
        queue_live_event(
            db,
            "problem.resolved",
            entity_type=problem.entity_type,
            entity_id=problem.entity_id,
            data={"problem_id": str(problem.id), "reason": problem.reason},
        )
    else:
        problem.details = {
            **dict(problem.details or {}),
            "preferred_release_id": str(winner.id),
            "physical_duplicate_remains": True,
        }
        queue_live_event(
            db,
            "problem.updated",
            entity_type=problem.entity_type,
            entity_id=problem.entity_id,
            data={"problem_id": str(problem.id), "reason": problem.reason},
        )
        await create_event(
            db,
            "duplicate.winner_selected",
            entity_type="movie",
            entity_id=movie.id,
            message="A preferred duplicate release was selected, but the losing copy remains on disk.",
            details={"winner_release_id": str(winner.id), "losing_release_ids": [str(item.id) for item in losers]},
        )

    movie.revision += 1
    await db.flush()
    return {
        "movie_id": movie.id,
        "winner_release_id": winner.id,
        "losing_release_ids": [item.id for item in losers],
        "duplicate_resolved": duplicate_resolved,
        "deleted_directories": deleted_directories,
        "removed_torrents": removed_torrents,
        "warnings": warnings,
        "problem_status": problem.status.value,
    }


async def run_duplicate_resolution(
    job_id: UUID,
    resource_id: str,
    confirmation_token: str,
    *,
    qbit_client_factory: Callable[..., Any],
) -> None:
    """Run the explicitly confirmed duplicate decision as a durable Job."""

    async def worker(db, job) -> None:
        await checkpoint_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={
                "current": 0,
                "total": 1,
                "percent": 0,
                "stage": "duplicate_resolution",
                "detail": "Applying the confirmed duplicate resolution…",
            },
        )

        try:
            result = await commit_duplicate_resolution(
                db,
                resource_id,
                confirmation_token,
                qbit_client_factory=qbit_client_factory,
            )
            summary = {
                "movie_id": str(result["movie_id"]),
                "winner_release_id": str(result["winner_release_id"]),
                "losing_release_ids": [str(item) for item in result["losing_release_ids"]],
                "duplicate_resolved": bool(result["duplicate_resolved"]),
                "deleted_directories": [str(item) for item in result["deleted_directories"]],
                "removed_torrents": [str(item) for item in result["removed_torrents"]],
                "warnings": [str(item) for item in result["warnings"]],
                "problem_status": str(result["problem_status"]),
                "message": "Duplicate resolution completed.",
            }
            await checkpoint_job(
                db,
                job,
                status=JobStatus.COMPLETED,
                progress={
                    "current": 1,
                    "total": 1,
                    "percent": 100,
                    "stage": "completed",
                    "detail": summary["message"],
                },
                summary=summary,
            )
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            raise JobFailure(
                exc.code,
                exc.message,
                details=exc.details,
                progress={
                    "current": 0,
                    "total": 1,
                    "percent": 0,
                    "stage": "failed",
                    "detail": "Duplicate resolution could not be applied.",
                },
            ) from exc
        except Exception as exc:
            raise JobFailure(
                "DUPLICATE_RESOLUTION_FAILED",
                str(exc),
                progress={
                    "current": 0,
                    "total": 1,
                    "percent": 0,
                    "stage": "failed",
                    "detail": "Duplicate resolution failed.",
                },
            ) from exc

    await run_job(
        job_id,
        worker,
        failure_code="DUPLICATE_RESOLUTION_FAILED",
        failure_message="Duplicate resolution failed.",
    )
