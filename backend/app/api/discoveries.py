from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.api.tmdb import get_tmdb_client_factory
from app.core.errors import AppError
from app.db.session import get_db
from app.integrations.filesystem import DirectoryObservation
from app.models.auth import AdminUser
from app.models.domain import (
    DiscoveryReviewState,
    MediaDirectory,
    MediaDiscovery,
    MediaType,
    Movie,
    Show,
)
from app.schemas.common import Collection
from app.schemas.discoveries import (
    MediaDiscoveryCandidate,
    MediaDiscoveryCount,
    MediaDiscoveryResponse,
    MediaDiscoverySelection,
)
from app.services.movies import add_movie_from_tmdb
from app.services.reconciliation import reconcile_movie_directory
from app.services.shows import add_show_from_tmdb, reconcile_show_directory
from app.services.tmdb import get_tmdb_configuration

router = APIRouter(prefix="/discoveries", tags=["discoveries"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _candidate(value: dict[str, object] | None) -> MediaDiscoveryCandidate | None:
    if not value:
        return None
    tmdb_id = value.get("tmdb_id")
    title = value.get("title")
    if not isinstance(tmdb_id, int) or not isinstance(title, str):
        return None
    return MediaDiscoveryCandidate(
        tmdb_id=tmdb_id,
        title=title,
        original_title=value.get("original_title") if isinstance(value.get("original_title"), str) else None,
        year=value.get("year") if isinstance(value.get("year"), int) else None,
        overview=value.get("overview") if isinstance(value.get("overview"), str) else None,
        poster_ref=(
            value.get("poster_ref")
            if isinstance(value.get("poster_ref"), str)
            else value.get("poster_path") if isinstance(value.get("poster_path"), str) else None
        ),
    )


def _candidate_payload(item: object) -> dict[str, object]:
    return {
        "tmdb_id": int(getattr(item, "tmdb_id")),
        "title": str(getattr(item, "title")),
        "original_title": getattr(item, "original_title", None),
        "year": getattr(item, "year", None),
        "overview": getattr(item, "overview", None),
        "poster_ref": getattr(item, "poster_path", None),
    }


def _response(discovery: MediaDiscovery) -> MediaDiscoveryResponse:
    directory = discovery.media_directory
    root = directory.storage_root
    raw_candidates = [value for value in discovery.candidates if isinstance(value, dict)]
    candidates = [value for value in (_candidate(item) for item in raw_candidates) if value is not None]
    by_id = {item.tmdb_id: item for item in candidates}
    parse = discovery.parse_snapshot or {}
    quality = parse.get("quality") if isinstance(parse.get("quality"), dict) else {}
    hdr = parse.get("hdr") if isinstance(parse.get("hdr"), dict) else {}
    hdr_values = hdr.get("values") if isinstance(hdr.get("values"), list) else []
    return MediaDiscoveryResponse(
        id=discovery.id,
        directory_id=directory.id,
        media_type=discovery.media_type.value,
        path=directory.resolved_path,
        root_name=root.name if root is not None else "Unlinked root",
        exists=directory.exists,
        review_state=discovery.review_state.value,
        parsed_title=discovery.parsed_title,
        parsed_year=discovery.parsed_year,
        confidence=float(discovery.confidence) if discovery.confidence is not None else None,
        quality=quality.get("canonical") if isinstance(quality.get("canonical"), str) else None,
        edition=parse.get("edition") if isinstance(parse.get("edition"), str) else None,
        hdr=[str(value) for value in hdr_values if value],
        suggested=by_id.get(discovery.suggested_tmdb_id),
        selected=by_id.get(discovery.selected_tmdb_id),
        candidates=candidates[:6],
        created_at=discovery.created_at,
        updated_at=discovery.updated_at,
    )


def _query():
    return select(MediaDiscovery).options(
        selectinload(MediaDiscovery.media_directory).selectinload(MediaDirectory.storage_root),
        selectinload(MediaDiscovery.media_directory).selectinload(MediaDirectory.files),
    )


async def _get_discovery(db: AsyncSession, discovery_id: UUID) -> MediaDiscovery:
    discovery = await db.scalar(_query().where(MediaDiscovery.id == discovery_id))
    if discovery is None:
        raise AppError("DISCOVERY_NOT_FOUND", "That review item no longer exists.", status_code=404)
    return discovery


@router.get("/count", response_model=MediaDiscoveryCount)
async def count_discoveries(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MediaDiscoveryCount:
    count = int(
        await db.scalar(
            select(func.count()).select_from(MediaDiscovery).where(MediaDiscovery.review_state == DiscoveryReviewState.PENDING)
        )
        or 0
    )
    return MediaDiscoveryCount(count=count)


@router.get("", response_model=Collection[MediaDiscoveryResponse])
async def list_discoveries(
    state: str = Query("pending"),
    media_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[MediaDiscoveryResponse]:
    try:
        review_state = DiscoveryReviewState(state)
    except ValueError as exc:
        raise AppError("INVALID_DISCOVERY_STATE", "Unknown review state.", status_code=422) from exc
    statement = _query().where(MediaDiscovery.review_state == review_state)
    if media_type is not None:
        try:
            statement = statement.where(MediaDiscovery.media_type == MediaType(media_type))
        except ValueError as exc:
            raise AppError("INVALID_MEDIA_TYPE", "Unknown media type.", status_code=422) from exc
    statement = statement.order_by(MediaDiscovery.created_at)
    rows = (await db.scalars(statement)).unique().all()
    total = len(rows)
    start = (page - 1) * page_size
    items = [_response(item) for item in rows[start : start + page_size]]
    return Collection(items=items, page=page, page_size=page_size, total=total, pages=(total + page_size - 1) // page_size)


@router.post("/{discovery_id}/select", response_model=MediaDiscoveryResponse)
async def select_discovery(
    discovery_id: UUID,
    payload: MediaDiscoverySelection,
    _: object = Depends(require_csrf),
    __: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> MediaDiscoveryResponse:
    discovery = await _get_discovery(db, discovery_id)
    if discovery.review_state != DiscoveryReviewState.PENDING:
        raise AppError("DISCOVERY_CLOSED", "That review item is no longer pending.", status_code=409)
    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before changing a match.", status_code=409)
    client = client_factory(configuration.api_key)
    try:
        metadata = (
            await client.get_movie(payload.tmdb_id)
            if discovery.media_type == MediaType.MOVIES
            else await client.get_show(payload.tmdb_id)
        )
    except Exception as exc:
        label = "movie" if discovery.media_type == MediaType.MOVIES else "show"
        raise AppError("TMDB_UNAVAILABLE", f"Could not load that TMDB {label}: {exc}", status_code=503) from exc
    finally:
        await client.close()
    values = [item for item in discovery.candidates if isinstance(item, dict) and item.get("tmdb_id") != metadata.tmdb_id]
    values.insert(0, _candidate_payload(metadata))
    discovery.candidates = values[:6]
    discovery.selected_tmdb_id = metadata.tmdb_id
    await db.commit()
    await db.refresh(discovery)
    return _response(discovery)


def _directory_observation(directory: MediaDirectory) -> DirectoryObservation:
    media_files = tuple(sorted(file.relative_path for file in directory.files if file.exists))
    upper_parts = [{part.upper() for part in Path(value).parts} for value in media_files]
    return DirectoryObservation(
        path=directory.resolved_path,
        name=Path(directory.resolved_path).name,
        media_files=media_files,
        has_dvd_structure=any("VIDEO_TS" in parts for parts in upper_parts),
        has_bluray_structure=any("BDMV" in parts for parts in upper_parts),
    )


@router.post("/{discovery_id}/approve", response_model=MediaDiscoveryResponse)
async def approve_discovery(
    discovery_id: UUID,
    _: object = Depends(require_csrf),
    __: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> MediaDiscoveryResponse:
    discovery = await _get_discovery(db, discovery_id)
    if discovery.review_state != DiscoveryReviewState.PENDING:
        raise AppError("DISCOVERY_CLOSED", "That review item is no longer pending.", status_code=409)
    directory = discovery.media_directory
    root = directory.storage_root
    if root is None:
        raise AppError("DISCOVERY_UNLINKED", "Reconnect this directory to a storage root before adding it.", status_code=409)
    if not directory.exists:
        raise AppError("DISCOVERY_MISSING", "This directory is no longer present on disk.", status_code=409)
    if discovery.selected_tmdb_id is None:
        raise AppError("DISCOVERY_MATCH_REQUIRED", "Choose the identity before adding it.", status_code=409)

    observation = _directory_observation(directory)
    if discovery.media_type == MediaType.MOVIES:
        media = await db.scalar(select(Movie).where(Movie.tmdb_id == discovery.selected_tmdb_id))
        if media is None:
            configuration = await get_tmdb_configuration(db)
            if configuration is None or not configuration.enabled or not configuration.api_key:
                raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before adding a movie.", status_code=409)
            try:
                media = await add_movie_from_tmdb(
                    db,
                    discovery.selected_tmdb_id,
                    monitored=True,
                    client_factory=client_factory,
                    api_key=configuration.api_key,
                )
            except Exception as exc:
                await db.rollback()
                raise AppError("TMDB_MOVIE_METADATA_FAILED", f"Could not add the selected movie: {exc}", status_code=503) from exc
        await reconcile_movie_directory(db, root, observation, movie_hint=media, manual_identity=True)
    else:
        media = await db.scalar(select(Show).where(Show.tmdb_id == discovery.selected_tmdb_id))
        if media is None:
            try:
                media = await add_show_from_tmdb(
                    db,
                    discovery.selected_tmdb_id,
                    monitored=True,
                    client_factory=client_factory,
                )
            except Exception as exc:
                await db.rollback()
                raise AppError("TMDB_SHOW_METADATA_FAILED", f"Could not add the selected show: {exc}", status_code=503) from exc
        await reconcile_show_directory(db, root, observation, show_hint=media)

    discovery.review_state = DiscoveryReviewState.APPROVED
    discovery.approved_media_id = media.id
    discovery.reviewed_at = utcnow()
    await db.commit()
    await db.refresh(discovery)
    return _response(discovery)


@router.post("/{discovery_id}/ignore", response_model=MediaDiscoveryResponse)
async def ignore_discovery(
    discovery_id: UUID,
    _: object = Depends(require_csrf),
    __: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MediaDiscoveryResponse:
    discovery = await _get_discovery(db, discovery_id)
    if discovery.review_state != DiscoveryReviewState.PENDING:
        raise AppError("DISCOVERY_CLOSED", "That review item is no longer pending.", status_code=409)
    discovery.review_state = DiscoveryReviewState.IGNORED
    discovery.reviewed_at = utcnow()
    await db.commit()
    await db.refresh(discovery)
    return _response(discovery)
