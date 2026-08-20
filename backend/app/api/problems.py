from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.api.tmdb import get_tmdb_client_factory
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Episode, MediaDirectory, Movie, MovieRelease, Problem, ProblemStatus, Severity, Show, ShowRelease, StorageRoot, Torrent
from app.schemas.common import Collection
from app.schemas.problems import ProblemResolveRequest, ProblemResponse
from app.services.events import publish_live_event
from app.services.problem_resolution import available_actions, resolve_explicit_problem_action

router = APIRouter(prefix="/problems", tags=["problems"])


def _parse_severity(value: str) -> Severity:
    aliases = {"high": Severity.ERROR, "medium": Severity.WARNING, "low": Severity.INFO}
    try:
        return aliases.get(value.casefold(), Severity(value.casefold()))
    except ValueError as exc:
        raise AppError("INVALID_SEVERITY", "Unknown problem severity.", status_code=422) from exc


async def _problem_subject(db: AsyncSession, problem: Problem) -> str | None:
    if problem.entity_id is None:
        return None
    if problem.entity_type == "movie":
        movie = await db.get(Movie, problem.entity_id)
        return f"{movie.title} ({movie.year})" if movie and movie.year else movie.title if movie else None
    if problem.entity_type == "show":
        show = await db.get(Show, problem.entity_id)
        return f"{show.title} ({show.year})" if show and show.year else show.title if show else None
    if problem.entity_type == "episode":
        row = (
            await db.execute(
                select(Episode, Show).join(Show, Show.id == Episode.show_id).where(Episode.id == problem.entity_id)
            )
        ).first()
        if row:
            episode, show = row
            return f"{show.title} S{episode.season_number:02d}E{episode.episode_number:02d}"
    if problem.entity_type == "movie_release":
        row = (
            await db.execute(
                select(MovieRelease, Movie).join(Movie, Movie.id == MovieRelease.movie_id).where(MovieRelease.id == problem.entity_id)
            )
        ).first()
        if row:
            release, movie = row
            return f"{movie.title} · {release.raw_release_name}"
    if problem.entity_type == "show_release":
        row = (
            await db.execute(
                select(ShowRelease, Show).join(Show, Show.id == ShowRelease.show_id).where(ShowRelease.id == problem.entity_id)
            )
        ).first()
        if row:
            release, show = row
            return f"{show.title} · {release.raw_release_name}"
    if problem.entity_type == "torrent":
        torrent = await db.get(Torrent, problem.entity_id)
        return torrent.name if torrent else None
    if problem.entity_type == "media_directory":
        directory = await db.get(MediaDirectory, problem.entity_id)
        return directory.resolved_path if directory else None
    if problem.entity_type == "storage_root":
        root = await db.get(StorageRoot, problem.entity_id)
        return f"{root.name} · {root.resolved_root_path}" if root else None
    return None


async def _response(db: AsyncSession, problem: Problem) -> ProblemResponse:
    subject = await _problem_subject(db, problem)
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
    if status_filter:
        try:
            parsed = ProblemStatus(status_filter)
        except ValueError as exc:
            raise AppError("INVALID_STATUS", "Unknown problem status.", status_code=422) from exc
        query = query.where(Problem.status == parsed)
        count_query = count_query.where(Problem.status == parsed)
    total = await db.scalar(count_query) or 0
    rows = (await db.scalars(query.order_by(Problem.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return Collection(items=[await _response(db, row) for row in rows], page=page, page_size=page_size, total=total, pages=(total + page_size - 1) // page_size)


@router.get("/count")
async def count_problems(
    status_filter: str | None = Query(default="open", alias="status"),
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
    return {"count": int(await db.scalar(query) or 0)}


@router.delete("")
async def delete_problems(
    status_filter: str | None = Query(default="open", alias="status"),
    reason: str | None = None,
    entity_type: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Permanently remove Problem records only.

    This never deletes, moves, renames, or otherwise changes media.  Problems
    can be recreated later if a subsequent reconciliation observes the same
    unresolved condition again.
    """

    filters = []
    if status_filter:
        try:
            filters.append(Problem.status == ProblemStatus(status_filter))
        except ValueError as exc:
            raise AppError("INVALID_STATUS", "Unknown problem status.", status_code=422) from exc
    if reason:
        filters.append(Problem.reason == reason)
    if entity_type:
        filters.append(Problem.entity_type == entity_type)
    if severity:
        filters.append(Problem.severity == _parse_severity(severity))
    if category and category != "all":
        if category == "duplicates":
            filters.append(Problem.reason.contains("DUPLICATE"))
        elif category == "identity":
            filters.append(
                or_(
                    Problem.reason.contains("IDENTITY"),
                    Problem.reason.contains("CONFIDENCE"),
                    Problem.reason.contains("TMDB"),
                )
            )
        elif category == "paths":
            filters.append(or_(Problem.reason.contains("PATH"), Problem.reason.contains("ROOT")))
        else:
            filters.append(Problem.reason == category)
    statement = delete(Problem)
    if filters:
        statement = statement.where(*filters)
    result = await db.execute(statement)
    deleted_count = int(result.rowcount or 0)
    await db.commit()
    if deleted_count:
        publish_live_event("problem.deleted", entity_type="problem", data={"count": deleted_count})
    return {"deleted": deleted_count}


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
    return await _response(db, problem)


@router.post("/{problem_id}/resolve", response_model=ProblemResponse)
async def resolve_problem(
    problem_id: UUID,
    payload: ProblemResolveRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
    tmdb_client_factory=Depends(get_tmdb_client_factory),
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
    await db.commit()
    return await _response(db, problem)
