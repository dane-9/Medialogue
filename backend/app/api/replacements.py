from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime

from app.schemas.upgrades import MediaHistoryItem, UpgradeSearchResultResponse
from app.services.media_history import finished_history, history_snapshot
from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import Job, JobStatus, ReplacementReview, Torrent, UpgradeCandidate
from app.schemas.jobs import JobAcceptedResponse
from app.schemas.replacements import (
    ReplacementCountResponse,
    ReplacementFinishResponse,
    ReplacementItem,
    ReplacementQueueResponse,
    ReplacementReason,
    ReplacementRetireResponse,
    ReplacementReviewResponse,
)
from app.services.events import create_event
from app.services.jobs import create_job
from app.services.runtime_jobs import launch_runtime_job
from app.services.replacements import UNCOUNTED_REASONS, SEARCH_JOB_TYPE, replacement_queue, run_replacement_search, search_replacement_now
from app.api import downloads as downloads_api

router = APIRouter(prefix="/replacements", tags=["replacements"])


class ReplacementSearchRequest(BaseModel):
    target_key: str | None = Field(default=None, max_length=150)


class ReviewTarget(BaseModel):
    target_key: str = Field(min_length=1, max_length=150)
    #: The reason being ignored; the title comes back if a different one appears.
    reason: ReplacementReason


class ReplacementReviewRequest(BaseModel):
    targets: list[ReviewTarget] = Field(min_length=1, max_length=250)
    action: Literal["ignore", "restore"]


@router.get("", response_model=ReplacementQueueResponse)
async def queue(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ReplacementQueueResponse:
    from app.core.integration_config import get_integration_config_store

    colors = {str(item.id): item.color for item in get_integration_config_store().list_indexers()}
    items = [
        ReplacementItem.model_validate({
            **item,
            "indexer_color": colors.get(item.get("indexer_id") or ""),
            "current_indexer_color": colors.get(item.get("current_indexer_id") or ""),
        })
        for item in await replacement_queue(db)
    ]
    counts = {"needs_search": 0, "found": 0, "in_progress": 0, "dismissed": 0}
    for item in items:
        counts[item.tab] += 1
    return ReplacementQueueResponse(items=items, counts=counts)


@router.get("/count", response_model=ReplacementCountResponse)
async def count(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ReplacementCountResponse:
    """Items waiting on the user, for the sidebar badge."""

    # Titles that never had a torrent are listed but not counted: a library
    # imported from disk would otherwise keep the badge at a number nobody can
    # clear in one sitting. Nor are trumped ones: their files are still safe.
    return ReplacementCountResponse(count=sum(
        1 for item in await replacement_queue(db, details=False, counted_only=True)
        if item["needs_attention"] and item["reason_code"] not in UNCOUNTED_REASONS
    ))


@router.post("/search", response_model=JobAcceptedResponse, status_code=202)
async def search(payload: ReplacementSearchRequest, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)):
    running = (await db.scalars(select(Job).where(Job.job_type == SEARCH_JOB_TYPE,
        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])))).all()
    for active in running:
        active_key = (active.summary or {}).get("target_key")
        # A running search over the whole queue already covers this request,
        # and one item is never searched twice at once. A search for a
        # different item must not be answered with another item's job.
        if not active_key or active_key == payload.target_key:
            return JobAcceptedResponse(job_id=active.id)
    job = await create_job(db, SEARCH_JOB_TYPE, cancellable=False, summary={
        "target_key": payload.target_key,
        "message": "Searching for a replacement" if payload.target_key else "Searching for replacements",
    })
    await db.commit()
    launch_runtime_job(job.id, lambda: run_replacement_search(job.id, target_key=payload.target_key))
    return JobAcceptedResponse(job_id=job.id)


class ReplacementSearchNowRequest(BaseModel):
    target_key: str = Field(min_length=1, max_length=150)


class ReplacementSearchNowResponse(BaseModel):
    #: The kept replacement, when one qualified.
    candidate_id: UUID | None = None
    searched_at: datetime
    results: list[UpgradeSearchResultResponse]
    indexer_failures: int = 0


@router.post("/search-now", response_model=ReplacementSearchNowResponse)
async def search_now(payload: ReplacementSearchNowRequest, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)):
    """Search the trackers for one title now and list every release found."""

    from types import SimpleNamespace
    from uuid import uuid4

    from app.api.upgrades import _search_result_response

    row, results, searched_at, failures = await search_replacement_now(db, payload.target_key)
    # Nothing kept means nothing is selected; the rows still describe themselves.
    anchor = row or SimpleNamespace(id=uuid4(), guid=None, indexer_id=None, title=None)
    return ReplacementSearchNowResponse(
        candidate_id=row.id if row else None,
        searched_at=searched_at,
        results=[_search_result_response(anchor, value, position=index) for index, value in enumerate(results)],
        indexer_failures=failures,
    )


@router.get("/history", response_model=list[MediaHistoryItem])
async def history(_: AdminUser = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[MediaHistoryItem]:
    """Finished replacements, newest first."""

    return [MediaHistoryItem.model_validate(item) for item in await finished_history(db, replacements=True)]


@router.post("/review", response_model=ReplacementReviewResponse)
async def review(payload: ReplacementReviewRequest, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> ReplacementReviewResponse:
    for target in payload.targets:
        review = await db.get(ReplacementReview, target.target_key)
        if review is None:
            review = ReplacementReview(target_key=target.target_key)
            db.add(review)
        review.ignored_reason = target.reason if payload.action == "ignore" else None
    await db.commit()
    return ReplacementReviewResponse(updated=len(payload.targets))


@router.post("/{candidate_id}/retire-torrents", response_model=ReplacementRetireResponse)
async def retire(candidate_id: UUID, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db),
                 client_factory=Depends(downloads_api.get_qbittorrent_client_factory)) -> ReplacementRetireResponse:
    from app.services.upgrade_execution import advance_upgrade_handoff
    row = await db.get(UpgradeCandidate, candidate_id)
    if not row or not row.target_key.startswith("replace:") or row.execution_state != "cleanup_required" or not row.torrent_id:
        raise AppError("REPLACEMENT_NOT_READY", "The replacement must be validated before retiring old torrents.", status_code=409)
    torrent = await db.get(Torrent, row.torrent_id)
    row.execution_state = "validating"
    await db.flush()
    await advance_upgrade_handoff(db, torrent, qbittorrent_client_factory=client_factory, allow_torrent_removal=True)
    await db.commit()
    return ReplacementRetireResponse(state=row.execution_state, error=row.execution_error)


@router.post("/{candidate_id}/finish", response_model=ReplacementFinishResponse)
async def finish(candidate_id: UUID, _: object = Depends(require_csrf), db: AsyncSession = Depends(get_db)) -> ReplacementFinishResponse:
    from app.services.torrent_archive import torrent_archive_complete
    from app.services.upgrade_cleanup import _replacement_cleanup_guard
    row = await db.get(UpgradeCandidate, candidate_id)
    if not row or not row.target_key.startswith("replace:") or row.execution_state != "cleanup_required":
        raise AppError("REPLACEMENT_NOT_READY", "The replacement must be validated before finishing.", status_code=409)
    torrent = await db.get(Torrent, row.torrent_id) if row.torrent_id else None
    blocked, _ = await _replacement_cleanup_guard(db, row)
    if not torrent or not torrent_archive_complete(torrent) or blocked:
        raise AppError("REPLACEMENT_NOT_READY", "The replacement files or archive could not be verified. " + " ".join(blocked), status_code=409)
    row.execution_state = "completed"
    row.execution_error = None
    await create_event(
        db,
        "replacement.finished",
        entity_type="upgrade_candidate",
        entity_id=row.id,
        message=f"Replacement finished for {row.current_release_name}; old torrents, media and archives were kept.",
        details={"candidate_id": str(row.id), "old_media_retained": True,
                 "current_release_ids": list(row.current_release_ids or []),
                 "history": await history_snapshot(db, row)},
    )
    await db.commit()
    return ReplacementFinishResponse(state="completed", old_media_retained=True)
