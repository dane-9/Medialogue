import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.api.downloads import get_qbittorrent_client_factory
from app.api.plex import get_plex_client_factory
from app.api.tmdb import get_tmdb_client_factory
from app.core.errors import AppError
from app.db import session as db_session
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import (
    Episode,
    EpisodeMediaMap,
    Job,
    JobStatus,
    MediaDirectory,
    MediaFile,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    Problem,
    ProblemStatus,
    ProblemWorkflow,
    Severity,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    StorageRoot,
    Torrent,
    TorrentClientObservation,
)
from app.schemas.common import Collection
from app.services.integration_state import get_configured_download_client
from app.schemas.problems import (
    ProblemRecheckAllResponse,
    ProblemResolveRequest,
    ProblemResponse,
    ProblemSummaryResponse,
)
from app.services.events import publish_live_event
from app.services.jobs import create_job, publish_job_status, update_job
from app.services.library_scan import active_storage_root_scan_job, run_storage_root_scan
from app.services.plex import get_plex_configuration, recheck_movie_plex, recheck_show_plex
from app.services.problem_resolution import available_actions, resolve_explicit_problem_action, run_confirmed_show_reconciliation
from app.services.qbittorrent import poll_download_client
from app.services.reconciliation import resolve_problem as resolve_observed_problem
from app.services.runtime_jobs import launch_runtime_job
from app.services.shows import recheck_episode_duplicate_problem
from app.services.tmdb import get_tmdb_configuration, sync_show_metadata

router = APIRouter(prefix="/problems", tags=["problems"])


async def _problem_root_ids(db: AsyncSession, problem: Problem) -> set[UUID]:
    """Find the smallest storage-root set capable of re-evaluating a Problem."""

    if problem.entity_id is None:
        return set()
    if problem.entity_type == "storage_root":
        return {problem.entity_id}
    if problem.entity_type == "media_directory":
        root_id = await db.scalar(select(MediaDirectory.storage_root_id).where(MediaDirectory.id == problem.entity_id))
        return {root_id} if root_id else set()
    if problem.entity_type == "media_file":
        root_id = await db.scalar(
            select(MediaDirectory.storage_root_id)
            .join(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
            .where(MediaFile.id == problem.entity_id)
        )
        return {root_id} if root_id else set()
    if problem.entity_type == "movie_release":
        return set(
            (await db.scalars(select(MediaDirectory.storage_root_id).where(MediaDirectory.movie_release_id == problem.entity_id, MediaDirectory.storage_root_id.is_not(None)))).all()
        )
    if problem.entity_type == "show_release":
        direct = set(
            (await db.scalars(select(MediaDirectory.storage_root_id).where(MediaDirectory.show_release_id == problem.entity_id, MediaDirectory.storage_root_id.is_not(None)))).all()
        )
        mapped = set(
            (await db.scalars(
                select(MediaDirectory.storage_root_id)
                .join(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .where(EpisodeMediaMap.show_release_id == problem.entity_id, MediaDirectory.storage_root_id.is_not(None))
            )).all()
        )
        return direct | mapped
    if problem.entity_type == "movie":
        return set(
            (await db.scalars(
                select(MediaDirectory.storage_root_id)
                .join(MovieRelease, MovieRelease.id == MediaDirectory.movie_release_id)
                .where(MovieRelease.movie_id == problem.entity_id, MediaDirectory.storage_root_id.is_not(None))
            )).all()
        )
    if problem.entity_type == "show":
        release_roots = set(
            (await db.scalars(
                select(MediaDirectory.storage_root_id)
                .join(ShowRelease, ShowRelease.id == MediaDirectory.show_release_id)
                .where(ShowRelease.show_id == problem.entity_id, MediaDirectory.storage_root_id.is_not(None))
            )).all()
        )
        episode_roots = set(
            (await db.scalars(
                select(MediaDirectory.storage_root_id)
                .join(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .join(Episode, Episode.id == EpisodeMediaMap.episode_id)
                .where(Episode.show_id == problem.entity_id, MediaDirectory.storage_root_id.is_not(None))
            )).all()
        )
        return release_roots | episode_roots
    if problem.entity_type == "episode":
        return set(
            (await db.scalars(
                select(MediaDirectory.storage_root_id)
                .join(MediaFile, MediaFile.media_directory_id == MediaDirectory.id)
                .join(EpisodeMediaMap, EpisodeMediaMap.media_file_id == MediaFile.id)
                .where(EpisodeMediaMap.episode_id == problem.entity_id, MediaDirectory.storage_root_id.is_not(None))
            )).all()
        )
    return set()


async def _problem_download_client_ids(db: AsyncSession, problem: Problem) -> set[UUID]:
    """Find only qBittorrent clients whose evidence can affect this Problem."""

    if problem.entity_id is None:
        return set()
    torrent_ids: set[UUID] = set()
    if problem.entity_type == "torrent":
        torrent_ids.add(problem.entity_id)
    elif problem.entity_type == "movie_release":
        torrent_ids.update(
            (await db.scalars(
                select(MovieReleaseTorrent.torrent_id).where(MovieReleaseTorrent.movie_release_id == problem.entity_id)
            )).all()
        )
    elif problem.entity_type == "show_release":
        torrent_ids.update(
            (await db.scalars(
                select(ShowReleaseTorrent.torrent_id).where(ShowReleaseTorrent.show_release_id == problem.entity_id)
            )).all()
        )
    if not torrent_ids:
        return set()
    return set(
        (await db.scalars(
            select(TorrentClientObservation.download_client_id).where(TorrentClientObservation.torrent_id.in_(torrent_ids))
        )).all()
    )


async def _recheck_show_metadata_problem(
    db: AsyncSession,
    problem: Problem,
    *,
    tmdb_client_factory,
) -> str | None:
    """Retry the evidence source for TMDB Show metadata Problems in-place."""

    if problem.reason != "TMDB_SHOW_METADATA_UNAVAILABLE" or problem.entity_type != "show" or problem.entity_id is None:
        return None
    show = await db.get(Show, problem.entity_id)
    if show is None:
        return "The affected Show no longer exists."
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key or show.tmdb_id is None:
        return "TMDB is not configured for this Show."
    try:
        async with db.begin_nested():
            await sync_show_metadata(db, show, client_factory=tmdb_client_factory)
            await resolve_observed_problem(db, "TMDB_SHOW_METADATA_UNAVAILABLE", "show", show.id)
    except Exception as exc:
        return str(exc)
    return None




async def _recheck_plex_problem(
    db: AsyncSession,
    problem: Problem,
    *,
    plex_client_factory,
) -> tuple[bool, str | None]:
    """Re-evaluate Plex conflict evidence without rescanning unrelated roots."""

    if problem.reason != "PLEX_IDENTITY_MISMATCH" or problem.entity_id is None:
        return False, None
    configuration = await get_plex_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.token:
        return True, "Plex is not configured and enabled."

    movie: Movie | None = None
    show: Show | None = None
    if problem.entity_type == "movie":
        movie = await db.get(Movie, problem.entity_id)
    elif problem.entity_type == "show":
        show = await db.get(Show, problem.entity_id)
    elif problem.entity_type == "media_directory":
        directory = await db.get(MediaDirectory, problem.entity_id)
        if directory is not None and directory.movie_release_id is not None:
            movie_id = await db.scalar(select(MovieRelease.movie_id).where(MovieRelease.id == directory.movie_release_id))
            movie = await db.get(Movie, movie_id) if movie_id else None

    if movie is None and show is None:
        return False, None
    try:
        async with db.begin_nested():
            if movie is not None:
                result = await recheck_movie_plex(db, movie, configuration, client_factory=plex_client_factory)
            else:
                assert show is not None
                result = await recheck_show_plex(db, show, configuration, client_factory=plex_client_factory)
    except Exception as exc:
        return True, str(exc)
    if str(result.get("state") or "") == "unavailable":
        return True, configuration.last_error or "Plex is unavailable."
    return True, None


async def _queue_root_rechecks(db: AsyncSession, root_ids: set[UUID]) -> tuple[list[UUID], list[UUID]]:
    """Create targeted reconciliation jobs and return (new, already-active)."""

    new_job_ids: list[UUID] = []
    active_job_ids: list[UUID] = []
    for root_id in root_ids:
        root = await db.get(StorageRoot, root_id)
        if root is None or not root.enabled:
            continue
        existing = await active_storage_root_scan_job(db, root.id)
        if existing is not None:
            active_job_ids.append(existing.id)
            continue
        # A runtime lock without a discoverable active DB job is a transient
        # race (or a previous worker winding down). Queue behind that lock so a
        # recheck can never silently claim success without examining the root.
        job = await create_job(
            db,
            "reconciliation",
            summary={"storage_root_id": str(root.id), "path": root.resolved_root_path, "trigger": "problem_recheck"},
        )
        new_job_ids.append(job.id)
    return new_job_ids, active_job_ids


async def run_problem_recheck(
    job_id: UUID,
    problem_id: UUID,
    *,
    tmdb_client_factory,
    qbit_client_factory,
    plex_client_factory,
) -> None:
    """Run all evidence checks belonging to one Problem as a parent Job."""

    terminal_states = {JobStatus.CANCELLED, JobStatus.INTERRUPTED, JobStatus.COMPLETED, JobStatus.FAILED}
    async with db_session.async_session_factory() as db:
        job = await db.get(Job, job_id)
        problem = await db.get(Problem, problem_id)
        if job is None or problem is None or job.status in terminal_states:
            return

        try:
            await update_job(
                db,
                job,
                status=JobStatus.RUNNING,
                progress={"current": 0, "total": 1, "percent": 5, "stage": "checking_direct_evidence", "detail": "Checking the Problem's direct evidence sources…"},
            )
            await db.commit()
            publish_job_status(job)

            metadata_error = await _recheck_show_metadata_problem(
                db,
                problem,
                tmdb_client_factory=tmdb_client_factory,
            )
            plex_handled, plex_error = await _recheck_plex_problem(
                db,
                problem,
                plex_client_factory=plex_client_factory,
            )
            duplicate_handled = (
                problem.reason == "DUPLICATE_EPISODE_RELEASE"
                and problem.entity_type == "episode"
                and problem.entity_id is not None
            )
            duplicate_cleared = False
            if duplicate_handled:
                duplicate_cleared = await recheck_episode_duplicate_problem(db, problem.entity_id)
            direct_recheck = (
                problem.reason == "TMDB_SHOW_METADATA_UNAVAILABLE"
                or plex_handled
                or (duplicate_handled and duplicate_cleared)
            )
            root_ids = set() if direct_recheck else await _problem_root_ids(db, problem)
            download_client_ids = await _problem_download_client_ids(db, problem)
            new_job_ids, active_job_ids = await _queue_root_rechecks(db, root_ids)
            child_job_ids = [*new_job_ids, *active_job_ids]
            direct_errors = [item for item in (metadata_error, plex_error) if item]
            problem.resolution = {
                **dict(problem.resolution or {}),
                "recheck_job_ids": [str(item) for item in child_job_ids],
                "recheck_download_client_ids": [str(item) for item in sorted(download_client_ids, key=str)],
                **({"recheck_error": "; ".join(direct_errors)} if direct_errors else {}),
            }
            await db.commit()
            for child_id in new_job_ids:
                child = await db.get(Job, child_id)
                if child is not None:
                    publish_job_status(child)
                    root_id = UUID(str(child.summary["storage_root_id"]))
                    launch_runtime_job(child.id, lambda child_id=child.id, root_id=root_id: run_storage_root_scan(child_id, root_id))

            qbit_errors: list[str] = []
            if download_client_ids:
                await update_job(
                    db,
                    job,
                    progress={"current": 0, "total": 1, "percent": 20, "stage": "checking_download_clients", "detail": f"Checking {len(download_client_ids)} qBittorrent client{'s' if len(download_client_ids) != 1 else ''}…"},
                )
                await db.commit()
                publish_job_status(job)
            for client_id in download_client_ids:
                client = await get_configured_download_client(db, client_id)
                if client is None or not client.enabled:
                    continue
                try:
                    await poll_download_client(db, client, client_factory=qbit_client_factory)
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    qbit_errors.append(f"{client.name}: {exc}")

            failed_child_ids: list[str] = []
            pending = set(child_job_ids)
            seen = set(child_job_ids)
            last_completed = -1
            while pending:
                await asyncio.sleep(0.25)
                await db.refresh(job)
                if job.status == JobStatus.CANCELLED:
                    return
                for child_id in list(pending):
                    child = await db.get(Job, child_id)
                    if child is None:
                        continue
                    # Child scans write through independent sessions and this
                    # app deliberately uses expire_on_commit=False. Without an
                    # explicit refresh the identity map can retain RUNNING
                    # forever even though the durable Job already completed.
                    await db.refresh(child)
                    if child.status not in terminal_states:
                        continue
                    pending.remove(child_id)
                    if child.status != JobStatus.COMPLETED:
                        failed_child_ids.append(str(child.id))
                    followups = [UUID(str(item)) for item in (child.summary or {}).get("followup_job_ids", [])]
                    for followup_id in followups:
                        if followup_id not in seen:
                            seen.add(followup_id)
                            pending.add(followup_id)
                completed = len(seen) - len(pending)
                if completed != last_completed:
                    last_completed = completed
                    await update_job(
                        db,
                        job,
                        progress={"current": completed, "total": max(len(seen), 1), "percent": round(completed * 100 / max(len(seen), 1), 1), "stage": "waiting_for_evidence", "detail": f"Waiting for {len(pending)} evidence job{'s' if len(pending) != 1 else ''}…"},
                    )
                    await db.commit()
                    publish_job_status(job)

            if duplicate_handled and problem.entity_id is not None:
                duplicate_cleared = await recheck_episode_duplicate_problem(db, problem.entity_id)

            refreshed = await db.get(Problem, problem_id)
            if refreshed is not None:
                problem = refreshed
                await db.refresh(problem)
                all_errors = [*direct_errors, *qbit_errors]
                problem.resolution = {
                    **dict(problem.resolution or {}),
                    "recheck_job_ids": [str(item) for item in sorted(seen, key=str)],
                    **({"recheck_error": "; ".join(all_errors)} if all_errors else {}),
                }
                await db.commit()

            condition_cleared = problem.status != ProblemStatus.OPEN
            if condition_cleared:
                message = "Problem condition cleared."
            elif failed_child_ids or direct_errors or qbit_errors:
                message = "Problem evidence recheck completed with errors; the condition is still present."
            else:
                message = "Problem evidence recheck completed; the condition is still present."

            summary = {
                "problem_id": str(problem_id),
                "reason": problem.reason,
                "child_job_ids": [str(item) for item in sorted(seen, key=str)],
                "failed_child_job_ids": failed_child_ids,
                "direct_errors": direct_errors,
                "qbit_errors": qbit_errors,
                "condition_cleared": condition_cleared,
                "problem_status": problem.status.value,
                "message": message,
            }
            if failed_child_ids or direct_errors or qbit_errors:
                await update_job(
                    db,
                    job,
                    status=JobStatus.FAILED,
                    progress={"current": 1, "total": 1, "percent": 100, "stage": "failed", "detail": summary["message"]},
                    summary=summary,
                    error={"code": "PROBLEM_RECHECK_PARTIAL", "message": summary["message"]},
                )
            else:
                await update_job(
                    db,
                    job,
                    status=JobStatus.COMPLETED,
                    progress={"current": 1, "total": 1, "percent": 100, "stage": "completed", "detail": summary["message"]},
                    summary=summary,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await update_job(
                db,
                job,
                status=JobStatus.FAILED,
                progress={"current": 0, "total": 1, "percent": 0, "stage": "failed", "detail": "Problem evidence recheck failed."},
                error={"code": "PROBLEM_RECHECK_FAILED", "message": str(exc)},
            )
        await db.commit()
        publish_job_status(job)


def _parse_severity(value: str) -> Severity:
    aliases = {"high": Severity.ERROR, "medium": Severity.WARNING, "low": Severity.INFO}
    normalized = value.casefold()
    if normalized in aliases:
        return aliases[normalized]
    try:
        return Severity(normalized)
    except ValueError as exc:
        raise AppError("INVALID_SEVERITY", "Unknown problem severity.", status_code=422) from exc


async def _problem_subjects(db: AsyncSession, problems: list[Problem]) -> dict[tuple[str, UUID], str]:
    """Resolve subjects in bounded batches instead of one query per Problem."""

    by_type: dict[str, set[UUID]] = {}
    for problem in problems:
        if problem.entity_id is not None:
            by_type.setdefault(problem.entity_type, set()).add(problem.entity_id)
    subjects: dict[tuple[str, UUID], str] = {}

    movie_ids = by_type.get("movie", set())
    if movie_ids:
        for movie in (await db.scalars(select(Movie).where(Movie.id.in_(movie_ids)))).all():
            subjects[("movie", movie.id)] = f"{movie.title} ({movie.year})" if movie.year else movie.title

    show_ids = by_type.get("show", set())
    if show_ids:
        for show in (await db.scalars(select(Show).where(Show.id.in_(show_ids)))).all():
            subjects[("show", show.id)] = f"{show.title} ({show.year})" if show.year else show.title

    episode_ids = by_type.get("episode", set())
    if episode_ids:
        rows = (await db.execute(select(Episode, Show).join(Show, Show.id == Episode.show_id).where(Episode.id.in_(episode_ids)))).all()
        for episode, show in rows:
            subjects[("episode", episode.id)] = f"{show.title} S{episode.season_number:02d}E{episode.episode_number:02d}"

    movie_release_ids = by_type.get("movie_release", set())
    if movie_release_ids:
        rows = (await db.execute(select(MovieRelease, Movie).join(Movie, Movie.id == MovieRelease.movie_id).where(MovieRelease.id.in_(movie_release_ids)))).all()
        for release, movie in rows:
            subjects[("movie_release", release.id)] = f"{movie.title} · {release.raw_release_name}"

    show_release_ids = by_type.get("show_release", set())
    if show_release_ids:
        rows = (await db.execute(select(ShowRelease, Show).join(Show, Show.id == ShowRelease.show_id).where(ShowRelease.id.in_(show_release_ids)))).all()
        for release, show in rows:
            subjects[("show_release", release.id)] = f"{show.title} · {release.raw_release_name}"

    torrent_ids = by_type.get("torrent", set())
    if torrent_ids:
        for torrent in (await db.scalars(select(Torrent).where(Torrent.id.in_(torrent_ids)))).all():
            subjects[("torrent", torrent.id)] = torrent.name

    directory_ids = by_type.get("media_directory", set())
    if directory_ids:
        for directory in (await db.scalars(select(MediaDirectory).where(MediaDirectory.id.in_(directory_ids)))).all():
            subjects[("media_directory", directory.id)] = directory.resolved_path

    root_ids = by_type.get("storage_root", set())
    if root_ids:
        for root in (await db.scalars(select(StorageRoot).where(StorageRoot.id.in_(root_ids)))).all():
            subjects[("storage_root", root.id)] = f"{root.name} · {root.resolved_root_path}"

    media_file_ids = by_type.get("media_file", set())
    if media_file_ids:
        rows = (await db.execute(select(MediaFile, MediaDirectory).join(MediaDirectory, MediaDirectory.id == MediaFile.media_directory_id).where(MediaFile.id.in_(media_file_ids)))).all()
        for media_file, directory in rows:
            subjects[("media_file", media_file.id)] = f"{media_file.filename} · {directory.resolved_path}"
    return subjects


def _response(problem: Problem, subjects: dict[tuple[str, UUID], str]) -> ProblemResponse:
    subject = subjects.get((problem.entity_type, problem.entity_id)) if problem.entity_id else None
    return ProblemResponse.model_validate(problem).model_copy(
        update={"available_actions": available_actions(problem), "subject": subject}
    )


@router.get("", response_model=Collection[ProblemResponse])
async def list_problems(
    page: int = 1,
    page_size: int = 50,
    reason: str | None = None,
    entity_type: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    workflow: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[ProblemResponse]:
    page_size = min(max(page_size, 1), 250)
    query = select(Problem)
    count_query = select(func.count()).select_from(Problem)
    if reason:
        query = query.where(Problem.reason == reason)
        count_query = count_query.where(Problem.reason == reason)
    if entity_type:
        query = query.where(Problem.entity_type == entity_type)
        count_query = count_query.where(Problem.entity_type == entity_type)
    if severity:
        parsed_severity = _parse_severity(severity)
        query = query.where(Problem.severity == parsed_severity)
        count_query = count_query.where(Problem.severity == parsed_severity)
    if category and category != "all":
        if category == "duplicates":
            category_filter = Problem.reason.contains("DUPLICATE")
        elif category == "identity":
            category_filter = or_(
                Problem.reason.contains("IDENTITY"),
                Problem.reason.contains("CONFIDENCE"),
                Problem.reason.contains("TMDB"),
            )
        elif category == "paths":
            category_filter = or_(Problem.reason.contains("PATH"), Problem.reason.contains("ROOT"))
        else:
            category_filter = Problem.reason == category
        query = query.where(category_filter)
        count_query = count_query.where(category_filter)
    if workflow and workflow != "all":
        try:
            parsed_workflow = ProblemWorkflow(workflow)
        except ValueError as exc:
            raise AppError("INVALID_WORKFLOW", "Unknown problem workflow.", status_code=422) from exc
        query = query.where(Problem.workflow == parsed_workflow)
        count_query = count_query.where(Problem.workflow == parsed_workflow)
    if status_filter:
        try:
            parsed = ProblemStatus(status_filter)
        except ValueError as exc:
            raise AppError("INVALID_STATUS", "Unknown problem status.", status_code=422) from exc
        query = query.where(Problem.status == parsed)
        count_query = count_query.where(Problem.status == parsed)
    total = await db.scalar(count_query) or 0
    pages = (total + page_size - 1) // page_size
    if pages and page > pages:
        page = pages
    rows = (await db.scalars(query.order_by(Problem.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    subjects = await _problem_subjects(db, list(rows))
    return Collection(items=[_response(row, subjects) for row in rows], page=page, page_size=page_size, total=total, pages=pages)


@router.get("/count")
async def count_problems(
    status_filter: str | None = Query(default="open", alias="status"),
    workflow: str | None = None,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    query = select(func.count()).select_from(Problem)
    if status_filter:
        try:
            parsed = ProblemStatus(status_filter)
        except ValueError as exc:
            raise AppError("INVALID_STATUS", "Unknown problem status.", status_code=422) from exc
        query = query.where(Problem.status == parsed)
    if workflow and workflow != "all":
        try:
            query = query.where(Problem.workflow == ProblemWorkflow(workflow))
        except ValueError as exc:
            raise AppError("INVALID_WORKFLOW", "Unknown problem workflow.", status_code=422) from exc
    return {"count": int(await db.scalar(query) or 0)}


@router.get("/summary", response_model=ProblemSummaryResponse)
async def problem_summary(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ProblemSummaryResponse:
    open_count = int(
        await db.scalar(select(func.count()).select_from(Problem).where(Problem.status == ProblemStatus.OPEN)) or 0
    )
    suppressed_count = int(
        await db.scalar(select(func.count()).select_from(Problem).where(Problem.status == ProblemStatus.DISMISSED)) or 0
    )
    workflow_rows = (
        await db.execute(
            select(Problem.workflow, func.count())
            .where(Problem.status == ProblemStatus.OPEN)
            .group_by(Problem.workflow)
        )
    ).all()
    workflows = {item.value: 0 for item in ProblemWorkflow}
    workflows.update({workflow.value: int(count) for workflow, count in workflow_rows})
    return ProblemSummaryResponse(open=open_count, suppressed=suppressed_count, workflows=workflows)


@router.post("/recheck", response_model=ProblemRecheckAllResponse)
async def recheck_open_problems(
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    tmdb_client_factory=Depends(get_tmdb_client_factory),
    qbit_client_factory=Depends(get_qbittorrent_client_factory),
    plex_client_factory=Depends(get_plex_client_factory),
) -> ProblemRecheckAllResponse:
    problems = (await db.scalars(select(Problem).where(Problem.status == ProblemStatus.OPEN))).all()
    queued: list[tuple[Problem, Job]] = []
    for problem in problems:
        await resolve_explicit_problem_action(
            db,
            problem,
            "recheck",
            {},
            tmdb_client_factory=tmdb_client_factory,
        )
        parent_job = await create_job(
            db,
            "problem_recheck",
            cancellable=False,
            summary={
                "problem_id": str(problem.id),
                "reason": problem.reason,
                "trigger": "recheck_all_open_problems",
                "message": "Checking all evidence sources for this Problem…",
            },
        )
        problem.resolution = {**dict(problem.resolution or {}), "recheck_parent_job_id": str(parent_job.id)}
        queued.append((problem, parent_job))
    await db.commit()
    for problem, parent_job in queued:
        publish_job_status(parent_job)
        launch_runtime_job(
            parent_job.id,
            lambda problem=problem, parent_job=parent_job: run_problem_recheck(
                parent_job.id,
                problem.id,
                tmdb_client_factory=tmdb_client_factory,
                qbit_client_factory=qbit_client_factory,
                plex_client_factory=plex_client_factory,
            ),
        )
    return ProblemRecheckAllResponse(requested=len(queued), job_ids=[job.id for _, job in queued])


@router.delete("/{problem_id}")
async def delete_problem(
    problem_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    problem = await db.get(Problem, problem_id)
    if problem is None:
        raise AppError("NOT_FOUND", "Problem was not found.", status_code=404)
    await db.delete(problem)
    await db.commit()
    publish_live_event("problem.deleted", entity_type="problem", entity_id=problem_id, data={"count": 1})
    return {"id": str(problem_id)}


@router.get("/{problem_id}", response_model=ProblemResponse)
async def get_problem(problem_id: UUID, _: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ProblemResponse:
    problem = await db.get(Problem, problem_id)
    if problem is None:
        raise AppError("NOT_FOUND", "Problem was not found.", status_code=404)
    subjects = await _problem_subjects(db, [problem])
    return _response(problem, subjects)


@router.post("/{problem_id}/resolve", response_model=ProblemResponse)
async def resolve_problem(
    problem_id: UUID,
    payload: ProblemResolveRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    tmdb_client_factory=Depends(get_tmdb_client_factory),
    qbit_client_factory=Depends(get_qbittorrent_client_factory),
    plex_client_factory=Depends(get_plex_client_factory),
) -> ProblemResponse:
    problem = await db.get(Problem, problem_id)
    if problem is None:
        raise AppError("NOT_FOUND", "Problem was not found.", status_code=404)
    problem = await resolve_explicit_problem_action(
        db,
        problem,
        payload.action,
        payload.payload,
        tmdb_client_factory=tmdb_client_factory,
    )
    if payload.action == "recheck":
        parent_job = await create_job(
            db,
            "problem_recheck",
            cancellable=False,
            summary={
                "problem_id": str(problem.id),
                "reason": problem.reason,
                "message": "Checking all evidence sources for this Problem…",
            },
        )
        problem.resolution = {
            **dict(problem.resolution or {}),
            "recheck_parent_job_id": str(parent_job.id),
        }
        await db.commit()
        publish_job_status(parent_job)
        launch_runtime_job(
            parent_job.id,
            lambda: run_problem_recheck(
                parent_job.id,
                problem.id,
                tmdb_client_factory=tmdb_client_factory,
                qbit_client_factory=qbit_client_factory,
                plex_client_factory=plex_client_factory,
            ),
        )
    else:
        await db.commit()
        if payload.action == "confirm_show_match":
            followup_value = (problem.resolution or {}).get("followup_job_id")
            try:
                followup_job_id = UUID(str(followup_value))
            except (TypeError, ValueError):
                followup_job_id = None
            if followup_job_id is not None:
                followup_job = await db.get(Job, followup_job_id)
                if followup_job is not None:
                    publish_job_status(followup_job)
                launch_runtime_job(
                    followup_job_id,
                    lambda: run_confirmed_show_reconciliation(
                        followup_job_id,
                        tmdb_client_factory=tmdb_client_factory,
                    ),
                )
    subjects = await _problem_subjects(db, [problem])
    return _response(problem, subjects)
