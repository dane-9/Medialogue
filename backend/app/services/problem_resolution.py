from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.integration_config import get_integration_config_store
from app.core.security import consume_confirmation_jti, sign_confirmation_payload, verify_confirmation_payload
from app.integrations.filesystem import FilesystemObserver
from app.models.domain import AccessMode, IdentityState, JobStatus, MediaDirectory, Movie, MovieRelease, MovieReleaseTorrent, Problem, ProblemStatus, ReleaseState, Show, StorageRoot, Torrent, TorrentArchiveState, TorrentClientObservation, utcnow
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
                    raise AppError("ROOT_READ_ONLY", f"Media folder for {directory.resolved_path} is not configured read-write.", status_code=409)
                path = Path(directory.resolved_path)
                root_path = Path(root.resolved_root_path)
                if not _inside_root(path, root_path) or path.resolve() == root_path.resolve():
                    raise AppError("UNSAFE_DELETE_TARGET", "A losing directory is not safely inside its configured media folder.", status_code=409)
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
    qbittorrent_client_factory: Callable[..., Any],
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

    await consume_confirmation_jti(db, payload)

    winner.release_state = ReleaseState.CURRENT
    winner.became_current_at = winner.became_current_at or utcnow()
    deleted_directories: list[str] = []
    removed_torrents: list[str] = []
    warnings: list[str] = []
    qbittorrent_removal_failed = False

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
        for torrent, _observation in torrent_rows:
            if torrent.archive_state != TorrentArchiveState.ARCHIVED:
                raise AppError("TORRENT_ARCHIVE_REQUIRED", "A selected torrent is no longer safely archived.", status_code=409)
        for torrent, observation in torrent_rows:
            client = get_integration_config_store().get_download_client(observation.download_client_id)
            if client is None or not client.enabled:
                warnings.append(f"qBittorrent client {observation.download_client_id} is no longer configured; torrent was not removed.")
                qbittorrent_removal_failed = True
                continue
            adapter = None
            try:
                adapter = qbittorrent_client_factory(client.url, client.username or "", client.password or "")
                await adapter.remove_torrent(torrent.info_hash, delete_files=False)
                observation.is_present = False
                observation.removed_at = utcnow()
                removed_torrents.append(torrent.info_hash)
                await create_event(
                    db,
                    "torrent.removed_by_user",
                    entity_type="torrent",
                    entity_id=torrent.id,
                    message="Removed duplicate torrent from qBittorrent while retaining its archive.",
                    details={"client_id": str(client.id), "info_hash": torrent.info_hash, "archive_retained": True},
                )
            except Exception as exc:
                qbittorrent_removal_failed = True
                warnings.append(f"Could not remove {torrent.info_hash} from {client.name}: {exc}")
            finally:
                if adapter is not None:
                    await adapter.close()

    perform_media_delete = delete_media and not qbittorrent_removal_failed
    if delete_media and qbittorrent_removal_failed:
        warnings.append("Media deletion was skipped because at least one requested qBittorrent removal failed. Review the remaining duplicate and retry when qBittorrent is reachable.")

    if perform_media_delete:
        for loser in losers:
            for directory in loser.directories:
                root = await db.get(StorageRoot, directory.storage_root_id)
                if root is None or root.access_mode != AccessMode.READ_WRITE:
                    raise AppError("ROOT_READ_ONLY", "Media folder is no longer writable; generate a new preview.", status_code=409)
                path = Path(directory.resolved_path)
                root_path = Path(root.resolved_root_path)
                if not _inside_root(path, root_path) or path.resolve() == root_path.resolve() or path.is_symlink():
                    raise AppError("UNSAFE_DELETE_TARGET", "Refusing to delete a path that is not safely inside the configured media folder.", status_code=409)
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
    qbittorrent_client_factory: Callable[..., Any],
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
                qbittorrent_client_factory=qbittorrent_client_factory,
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
