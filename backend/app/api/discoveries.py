from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
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
    Job,
    JobStatus,
    MediaDirectory,
    MediaDiscovery,
    MediaRole,
    MediaType,
    MediaProfileOverride,
    Movie,
    MovieRelease,
    NewMediaState,
    QualityProfile,
    ReleaseState,
    Show,
)
from app.parser import EDITION_OPTIONS
from app.schemas.common import Collection
from app.schemas.discoveries import (
    MediaDiscoveryBulk,
    MediaDiscoveryBulkFailure,
    MediaDiscoveryBulkResult,
    MediaDiscoveryCandidate,
    MediaDiscoveryCount,
    MediaDiscoveryFile,
    MediaDiscoveryEditionUpdate,
    MediaDiscoveryQualityProfile,
    MediaDiscoveryQualityProfileBulk,
    MediaDiscoveryResponse,
    MediaDiscoverySearch,
    MediaDiscoverySelection,
)
from app.services.discoveries import DISCOVERY_CANDIDATE_LIMIT
from app.services.movies import add_movie_from_tmdb
from app.services.reconciliation import prefetch_plex_snapshot, reconcile_movie_directory
from app.services.shows import add_show_from_tmdb, reconcile_show_directory
from app.services.tmdb import get_tmdb_configuration
from app.services.jobs import create_job, update_job
from app.services.runtime_jobs import launch_runtime_job
from app.schemas.jobs import JobAcceptedResponse, JobResponse

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


def _parsed_edition(discovery: MediaDiscovery) -> str | None:
    parse = discovery.parse_snapshot if isinstance(discovery.parse_snapshot, dict) else {}
    value = parse.get("edition")
    return value if isinstance(value, str) and value.strip() else None


def _effective_edition(discovery: MediaDiscovery) -> str | None:
    return discovery.manual_edition_override if discovery.edition_override_active else _parsed_edition(discovery)


def _edition_slot(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    return "" if normalized in {"", "theatrical cut"} else normalized


async def _profile_names(db: AsyncSession, discoveries: Sequence[MediaDiscovery]) -> dict[UUID, str]:
    """Resolve the chosen profiles in one query rather than per row."""

    ids = {item.quality_profile_id for item in discoveries if item.quality_profile_id is not None}
    if not ids:
        return {}
    rows = await db.execute(select(QualityProfile.id, QualityProfile.name).where(QualityProfile.id.in_(ids)))
    return {row.id: row.name for row in rows}


def _response(
    discovery: MediaDiscovery,
    *,
    library_edition_conflict: bool = False,
    profile_names: Mapping[UUID, str] | None = None,
) -> MediaDiscoveryResponse:
    directory = discovery.media_directory
    root = directory.storage_root
    raw_candidates = [value for value in discovery.candidates if isinstance(value, dict)]
    candidates = [value for value in (_candidate(item) for item in raw_candidates) if value is not None]
    by_id = {item.tmdb_id: item for item in candidates}
    parse = discovery.parse_snapshot or {}
    quality = parse.get("quality") if isinstance(parse.get("quality"), dict) else {}
    hdr = parse.get("hdr") if isinstance(parse.get("hdr"), dict) else {}
    hdr_values = hdr.get("values") if isinstance(hdr.get("values"), list) else []
    present = [file for file in directory.files if file.exists]
    videos = {MediaRole.MOVIE_VIDEO, MediaRole.EPISODE_VIDEO, MediaRole.MULTI_EPISODE_VIDEO}
    # Video first, then everything else, each alphabetical: the media is what a
    # user looks for, and subtitles should never push it off the list.
    ordered = sorted(present, key=lambda file: (file.media_role not in videos, file.relative_path.casefold()))
    return MediaDiscoveryResponse(
        id=discovery.id,
        directory_id=directory.id,
        media_type=discovery.media_type.value,
        path=directory.resolved_path,
        root_name=root.name if root is not None else "Unlinked root",
        exists=directory.exists,
        new_media_state=discovery.new_media_state.value,
        parsed_title=discovery.parsed_title,
        parsed_year=discovery.parsed_year,
        confidence=float(discovery.confidence) if discovery.confidence is not None else None,
        quality=quality.get("canonical") if isinstance(quality.get("canonical"), str) else None,
        edition=_effective_edition(discovery),
        parsed_edition=_parsed_edition(discovery),
        edition_override_active=discovery.edition_override_active,
        manual_edition_override=discovery.manual_edition_override,
        library_edition_conflict=library_edition_conflict,
        # A profile deleted after it was chosen reads back as unset rather than
        # as a dangling id the UI would have to render as a bare UUID.
        quality_profile_id=discovery.quality_profile_id if (profile_names or {}).get(discovery.quality_profile_id) else None,
        quality_profile_name=(profile_names or {}).get(discovery.quality_profile_id),
        hdr=[str(value) for value in hdr_values if value],
        suggested=by_id.get(discovery.suggested_tmdb_id),
        selected=by_id.get(discovery.selected_tmdb_id),
        candidates=candidates[:DISCOVERY_CANDIDATE_LIMIT],
        files=[MediaDiscoveryFile(relative_path=file.relative_path, role=file.media_role.value) for file in ordered[:40]],
        file_count=len(present),
        video_count=sum(1 for file in present if file.media_role in videos),
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
        raise AppError("DISCOVERY_NOT_FOUND", "That New Media item no longer exists.", status_code=404)
    return discovery


async def _library_conflict_ids(db: AsyncSession, discoveries: list[MediaDiscovery]) -> set[UUID]:
    movie_rows = [item for item in discoveries if item.media_type == MediaType.MOVIES and item.selected_tmdb_id]
    tmdb_ids = {int(item.selected_tmdb_id) for item in movie_rows if item.selected_tmdb_id is not None}
    if not tmdb_ids:
        return set()
    movies = (
        await db.scalars(
            select(Movie)
            .options(selectinload(Movie.releases).selectinload(MovieRelease.directories))
            .where(Movie.tmdb_id.in_(tmdb_ids))
        )
    ).unique().all()
    occupied = {
        (int(movie.tmdb_id), _edition_slot(release.effective_edition))
        for movie in movies
        if movie.tmdb_id is not None
        for release in movie.releases
        if release.release_state in {ReleaseState.CURRENT, ReleaseState.DUPLICATE}
        and any(directory.exists for directory in release.directories)
    }
    return {
        item.id
        for item in movie_rows
        if (int(item.selected_tmdb_id), _edition_slot(_effective_edition(item))) in occupied
    }


async def _current_response(db: AsyncSession, discovery: MediaDiscovery) -> MediaDiscoveryResponse:
    conflicts = await _library_conflict_ids(db, [discovery])
    return _response(
        discovery,
        library_edition_conflict=discovery.id in conflicts,
        profile_names=await _profile_names(db, [discovery]),
    )


@router.get("/count", response_model=MediaDiscoveryCount)
async def count_discoveries(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MediaDiscoveryCount:
    count = int(
        await db.scalar(
            select(func.count())
            .select_from(MediaDiscovery)
            .join(MediaDirectory, MediaDirectory.id == MediaDiscovery.media_directory_id)
            .where(
                MediaDiscovery.new_media_state == NewMediaState.PENDING,
                MediaDirectory.exists.is_(True),
            )
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
        new_media_state = NewMediaState(state)
    except ValueError as exc:
        raise AppError("INVALID_DISCOVERY_STATE", "Unknown New Media state.", status_code=422) from exc
    conditions = [MediaDiscovery.new_media_state == new_media_state]
    if new_media_state == NewMediaState.PENDING:
        # A confirmed-missing folder is not actionable New Media work and must
        # disappear from the queue as soon as the inventory says the path is gone.
        conditions.append(MediaDiscovery.media_directory.has(MediaDirectory.exists.is_(True)))
    if media_type is not None:
        try:
            conditions.append(MediaDiscovery.media_type == MediaType(media_type))
        except ValueError as exc:
            raise AppError("INVALID_MEDIA_TYPE", "Unknown media type.", status_code=422) from exc
    # Count and page in SQL: loading every discovery, with every file of every
    # folder, to keep one page of them made each page cost the whole queue.
    total = int(await db.scalar(select(func.count()).select_from(MediaDiscovery).where(*conditions)) or 0)
    statement = (
        _query()
        .where(*conditions)
        .order_by(MediaDiscovery.created_at, MediaDiscovery.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    page_rows = list((await db.scalars(statement)).unique().all())
    library_conflicts = await _library_conflict_ids(db, page_rows)
    page_profiles = await _profile_names(db, page_rows)
    items = [
        _response(item, library_edition_conflict=item.id in library_conflicts, profile_names=page_profiles)
        for item in page_rows
    ]
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
    if discovery.new_media_state != NewMediaState.PENDING:
        raise AppError("DISCOVERY_CLOSED", "That New Media item is no longer pending.", status_code=409)
    known = next(
        (item for item in discovery.candidates if isinstance(item, dict) and item.get("tmdb_id") == payload.tmdb_id),
        None,
    )
    if known is not None:
        chosen = dict(known)
    elif payload.title:
        chosen = {
            "tmdb_id": payload.tmdb_id,
            "title": payload.title,
            "original_title": payload.original_title,
            "year": payload.year,
            "overview": None,
            "poster_ref": payload.poster_ref,
        }
    else:
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
        chosen = _candidate_payload(metadata)

    values = [item for item in discovery.candidates if isinstance(item, dict)]
    if any(item.get("tmdb_id") == payload.tmdb_id for item in values):
        values = [chosen if item.get("tmdb_id") == payload.tmdb_id else item for item in values]
    else:
        values.append(chosen)
    discovery.candidates = values[:DISCOVERY_CANDIDATE_LIMIT]
    discovery.selected_tmdb_id = payload.tmdb_id
    await db.commit()
    await db.refresh(discovery)
    return await _current_response(db, discovery)


@router.patch("/{discovery_id}/edition", response_model=MediaDiscoveryResponse)
async def set_discovery_edition(
    discovery_id: UUID,
    payload: MediaDiscoveryEditionUpdate,
    _: object = Depends(require_csrf),
    __: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MediaDiscoveryResponse:
    discovery = await _get_discovery(db, discovery_id)
    if discovery.new_media_state != NewMediaState.PENDING or discovery.media_type != MediaType.MOVIES:
        raise AppError("DISCOVERY_EDITION_NOT_ALLOWED", "Only pending New Media movies have editions.", status_code=409)
    if payload.mode == "automatic":
        discovery.edition_override_active = False
        discovery.manual_edition_override = None
    elif payload.mode == "standard":
        discovery.edition_override_active = True
        discovery.manual_edition_override = None
    else:
        edition = (payload.edition or "").strip()
        if not edition:
            raise AppError("EDITION_REQUIRED", "Choose an edition or enter a custom edition name.", status_code=422)
        discovery.edition_override_active = True
        discovery.manual_edition_override = next(
            (item for item in EDITION_OPTIONS if item.casefold() == edition.casefold()),
            edition,
        )
    await db.commit()
    await db.refresh(discovery)
    return await _current_response(db, discovery)


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


async def _apply_new_media_profile(
    db: AsyncSession,
    media: Movie | Show,
    quality_profile_id: UUID | None,
) -> None:
    """Give a newly created title the profile chosen in New Media.

    Only for titles this import just created: adding media to a title already in
    the library must not silently rewrite scoring the user configured earlier.
    A profile deleted between selection and approval is skipped rather than
    written as a dangling id.
    """

    if quality_profile_id is None:
        return
    if await db.scalar(select(QualityProfile.id).where(QualityProfile.id == quality_profile_id)) is None:
        return
    is_movie = isinstance(media, Movie)
    db.add(
        MediaProfileOverride(
            media_type=MediaType.MOVIES if is_movie else MediaType.SHOWS,
            movie_id=media.id if is_movie else None,
            show_id=media.id if not is_movie else None,
            quality_profile_id=quality_profile_id,
            override_definition={},
        )
    )
    await db.flush()


async def _approve_one(db: AsyncSession, discovery: MediaDiscovery, client_factory) -> MediaDiscovery:
    """Add one approved New Media directory to the library.

    Extracted from the single-item route so bulk approval runs exactly the same
    checks. A queue of thirty confident matches should not be a different code
    path from approving one of them, or the two drift.
    """
    if discovery.new_media_state != NewMediaState.PENDING:
        raise AppError("DISCOVERY_CLOSED", "That New Media item is no longer pending.", status_code=409)
    directory = discovery.media_directory
    root = directory.storage_root
    if root is None:
        raise AppError("DISCOVERY_UNLINKED", "Reconnect this directory to a media folder before adding it.", status_code=409)
    if not directory.exists:
        raise AppError("DISCOVERY_MISSING", "This directory is no longer present on disk.", status_code=409)
    if discovery.selected_tmdb_id is None:
        raise AppError("DISCOVERY_MATCH_REQUIRED", "Choose the identity before adding it.", status_code=409)
    if discovery.media_type == MediaType.MOVIES and discovery.id in await _library_conflict_ids(db, [discovery]):
        raise AppError(
            "DISCOVERY_MOVIE_EDITION_CONFLICT",
            "This movie edition is already in the library. Choose a different edition or leave this folder out.",
            status_code=409,
        )

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
            await _apply_new_media_profile(db, media, discovery.quality_profile_id)
        await reconcile_movie_directory(
            db,
            root,
            observation,
            movie_hint=media,
            manual_identity=True,
            edition_override_active=discovery.edition_override_active,
            manual_edition_override=discovery.manual_edition_override,
        )
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
            await _apply_new_media_profile(db, media, discovery.quality_profile_id)
        await reconcile_show_directory(db, root, observation, show_hint=media)

    discovery.new_media_state = NewMediaState.APPROVED
    discovery.approved_media_id = media.id
    discovery.reviewed_at = utcnow()
    await db.commit()
    await db.refresh(discovery)
    return discovery


async def _ignore_one(db: AsyncSession, discovery: MediaDiscovery) -> MediaDiscovery:
    if discovery.new_media_state != NewMediaState.PENDING:
        raise AppError("DISCOVERY_CLOSED", "That New Media item is no longer pending.", status_code=409)
    discovery.new_media_state = NewMediaState.IGNORED
    discovery.reviewed_at = utcnow()
    await db.commit()
    await db.refresh(discovery)
    return discovery


async def _restore_one(db: AsyncSession, discovery: MediaDiscovery) -> MediaDiscovery:
    """Put an ignored item back in the queue, so a mis-click is recoverable."""

    if discovery.new_media_state != NewMediaState.IGNORED:
        raise AppError("DISCOVERY_NOT_IGNORED", "Only an ignored item can be restored.", status_code=409)
    discovery.new_media_state = NewMediaState.PENDING
    discovery.reviewed_at = None
    await db.commit()
    await db.refresh(discovery)
    return discovery


async def _validated_profile_id(db: AsyncSession, quality_profile_id: UUID | None) -> UUID | None:
    if quality_profile_id is None:
        return None
    exists = await db.scalar(select(QualityProfile.id).where(QualityProfile.id == quality_profile_id))
    if exists is None:
        raise AppError("QUALITY_PROFILE_NOT_FOUND", "That Quality Profile no longer exists.", status_code=404)
    return exists


@router.patch("/{discovery_id}/quality-profile", response_model=MediaDiscoveryResponse)
async def set_discovery_quality_profile(
    discovery_id: UUID,
    payload: MediaDiscoveryQualityProfile,
    _: object = Depends(require_csrf),
    __: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MediaDiscoveryResponse:
    """Choose the Quality Profile the added title will be created with."""
    discovery = await _get_discovery(db, discovery_id)
    if discovery.new_media_state != NewMediaState.PENDING:
        raise AppError("DISCOVERY_CLOSED", "That New Media item is no longer pending.", status_code=409)
    discovery.quality_profile_id = await _validated_profile_id(db, payload.quality_profile_id)
    await db.commit()
    await db.refresh(discovery)
    return await _current_response(db, discovery)


@router.post("/quality-profile", response_model=MediaDiscoveryBulkResult)
async def set_discoveries_quality_profile(
    payload: MediaDiscoveryQualityProfileBulk,
    _: object = Depends(require_csrf),
    __: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MediaDiscoveryBulkResult:
    """Apply one Quality Profile across a New Media batch.

    Validated once rather than per item: a profile that does not exist is a bad
    request for the whole call, not five hundred identical per-item failures.
    """
    profile_id = await _validated_profile_id(db, payload.quality_profile_id)
    updated: list[UUID] = []
    failures: list[MediaDiscoveryBulkFailure] = []
    for discovery_id in payload.ids:
        try:
            discovery = await _get_discovery(db, discovery_id)
            if discovery.new_media_state != NewMediaState.PENDING:
                raise AppError("DISCOVERY_CLOSED", "That New Media item is no longer pending.", status_code=409)
        except AppError as exc:
            failures.append(MediaDiscoveryBulkFailure(id=discovery_id, code=exc.code, message=exc.message))
            continue
        discovery.quality_profile_id = profile_id
        updated.append(discovery_id)
    await db.commit()
    return MediaDiscoveryBulkResult(succeeded=updated, failed=failures)


@router.post("/{discovery_id}/search", response_model=MediaDiscoveryResponse)
async def search_discovery_candidates(
    discovery_id: UUID,
    payload: MediaDiscoverySearch,
    _: object = Depends(require_csrf),
    __: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> MediaDiscoveryResponse:
    """Search TMDB for a folder and keep the results on the discovery.

    The results live with the item rather than in whoever's browser happened to
    run the search: they are the same for every viewer, they survive a reload,
    and they stay until someone searches again or the item leaves the queue.
    A scan only records the handful of candidates it considered, which is why
    opening a row otherwise offered the one answer you already had.
    """
    discovery = await _get_discovery(db, discovery_id)
    if discovery.new_media_state != NewMediaState.PENDING:
        raise AppError("DISCOVERY_CLOSED", "That New Media item is no longer pending.", status_code=409)

    query = (payload.query or discovery.parsed_title or "").strip()
    if not query:
        raise AppError("DISCOVERY_QUERY_REQUIRED", "There is nothing to search for.", status_code=422)

    configuration = await get_tmdb_configuration(db)
    if configuration is None or not configuration.enabled or not configuration.api_key:
        raise AppError("TMDB_NOT_CONFIGURED", "Configure TMDB before searching.", status_code=409)
    client = client_factory(configuration.api_key)
    try:
        matches = (
            await client.search_movie(query, None)
            if discovery.media_type == MediaType.MOVIES
            else await client.search_show(query, None)
        )
    except Exception as exc:
        label = "Movie" if discovery.media_type == MediaType.MOVIES else "Show"
        raise AppError("TMDB_UNAVAILABLE", f"TMDB {label} lookup failed: {exc}", status_code=503) from exc
    finally:
        await client.close()

    candidates = [_candidate_payload(item) for item in matches[:DISCOVERY_CANDIDATE_LIMIT]]
    if discovery.selected_tmdb_id is not None and not any(item["tmdb_id"] == discovery.selected_tmdb_id for item in candidates):
        kept = next(
            (item for item in discovery.candidates if isinstance(item, dict) and item.get("tmdb_id") == discovery.selected_tmdb_id),
            None,
        )
        if kept is not None:
            candidates.insert(0, dict(kept))
    discovery.candidates = candidates[:DISCOVERY_CANDIDATE_LIMIT]
    # A search that returns something always leaves a row with a match to judge,
    # never an empty one the user has to fill in themselves.
    if discovery.selected_tmdb_id is None and discovery.candidates:
        discovery.selected_tmdb_id = int(discovery.candidates[0]["tmdb_id"])
    await db.commit()
    await db.refresh(discovery)
    return await _current_response(db, discovery)


@router.post("/bulk", response_model=MediaDiscoveryBulkResult)
async def bulk_new_media(
    payload: MediaDiscoveryBulk,
    _: object = Depends(require_csrf),
    __: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    client_factory=Depends(get_tmdb_client_factory),
) -> MediaDiscoveryBulkResult:
    """Approve, ignore or restore many New Media directories in one request.

    Each item is committed on its own and a failure does not stop the rest: one
    title whose metadata fetch times out must not discard the twenty-nine that
    already succeeded, and the caller is told exactly which ones failed and why.
    """
    approved, failures = await _apply_bulk(db, payload.ids, payload.action, client_factory)
    return MediaDiscoveryBulkResult(succeeded=approved, failed=failures)


async def _apply_bulk(
    db: AsyncSession,
    ids: list[UUID],
    action: str,
    client_factory,
    *,
    prefetch: bool = True,
) -> tuple[list[UUID], list[MediaDiscoveryBulkFailure]]:
    """Approve, ignore or restore these discoveries, each committed on its own."""

    approved: list[UUID] = []
    failures: list[MediaDiscoveryBulkFailure] = []
    unavailable: set[UUID] = set()
    batch_collision_ids: set[UUID] = set()
    if action == "approve":
        if prefetch:
            await prefetch_plex_snapshot(db)
        loaded: list[MediaDiscovery] = []
        for discovery_id in ids:
            try:
                loaded.append(await _get_discovery(db, discovery_id))
            except AppError as exc:
                unavailable.add(discovery_id)
                failures.append(MediaDiscoveryBulkFailure(id=discovery_id, code=exc.code, message=exc.message))
        groups: dict[tuple[int, str], list[UUID]] = {}
        for discovery in loaded:
            if discovery.media_type != MediaType.MOVIES or discovery.selected_tmdb_id is None:
                continue
            key = (int(discovery.selected_tmdb_id), _edition_slot(_effective_edition(discovery)))
            groups.setdefault(key, []).append(discovery.id)
        batch_collision_ids = {
            discovery_id for ids in groups.values() if len(ids) > 1 for discovery_id in ids
        }
        failures.extend(
            MediaDiscoveryBulkFailure(
                id=discovery_id,
                code="DISCOVERY_BATCH_EDITION_CONFLICT",
                message="Another selected folder is the same movie edition. Leave one out or correct its edition.",
            )
            for discovery_id in ids
            if discovery_id in batch_collision_ids
        )
    for discovery_id in ids:
        if discovery_id in unavailable or discovery_id in batch_collision_ids:
            continue
        try:
            discovery = await _get_discovery(db, discovery_id)
            if action == "approve":
                await _approve_one(db, discovery, client_factory)
            elif action == "restore":
                await _restore_one(db, discovery)
            else:
                await _ignore_one(db, discovery)
            approved.append(discovery_id)
        except AppError as exc:
            await db.rollback()
            failures.append(MediaDiscoveryBulkFailure(id=discovery_id, code=exc.code, message=exc.message))
    return approved, failures



# --- Adding in the background ------------------------------------------------
#
# Adding a whole queue takes minutes (each new title is fetched from TMDB). Run
# from the browser it stopped when the page was left, and the page then offered
# to add the same folders again over the run still going. As a Job it carries
# on whatever the browser does, its progress shows on return, and only one runs.

ADD_JOB_TYPE = "new_media_add"
ADD_JOB_CHUNK = 25
ADD_JOB_FAILURES_KEPT = 50


class MediaDiscoveryAddJobRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=20000)


async def _active_add_job(db: AsyncSession) -> Job | None:
    return await db.scalar(
        select(Job)
        .where(Job.job_type == ADD_JOB_TYPE, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        .order_by(Job.created_at.desc())
        .limit(1)
    )


@router.post("/add-job", response_model=JobAcceptedResponse, status_code=202)
async def start_add_job(
    payload: MediaDiscoveryAddJobRequest,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> JobAcceptedResponse:
    """Add these folders to the library in the background; a running add is reused."""

    active = await _active_add_job(db)
    if active is not None:
        return JobAcceptedResponse(job_id=active.id)
    ids = list(dict.fromkeys(payload.ids))
    job = await create_job(db, ADD_JOB_TYPE, cancellable=True, summary={
        "total": len(ids), "done": 0, "added": 0, "failed": 0, "failures": [],
        "message": f"Adding {len(ids)} New Media {'folder' if len(ids) == 1 else 'folders'}",
    })
    await update_job(db, job, progress={"current": 0, "total": len(ids), "percent": 0})
    await db.commit()
    # The ids go to the worker, not the Job's summary, which every progress
    # event carries. After a restart the Job is interrupted and the folders not
    # yet added are simply still in New Media.
    launch_runtime_job(job.id, lambda: run_add_job(job.id, ids))
    return JobAcceptedResponse(job_id=job.id)


@router.get("/add-job", response_model=JobResponse | None)
async def current_add_job(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Job | None:
    """The add running now, if any, so a page opened mid-run shows it."""

    return await _active_add_job(db)


async def run_add_job(job_id: UUID, ids: list[UUID]) -> None:
    from app.db import session as db_session

    async with db_session.async_session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        await update_job(db, job, status=JobStatus.RUNNING)
        await db.commit()
        await prefetch_plex_snapshot(db)
        added = failed = 0
        failures: list[str] = []
        try:
            for start in range(0, len(ids), ADD_JOB_CHUNK):
                await db.refresh(job)
                if job.status == JobStatus.CANCELLED:
                    return
                chunk = ids[start : start + ADD_JOB_CHUNK]
                succeeded, chunk_failures = await _apply_bulk(db, chunk, "approve", get_tmdb_client_factory(), prefetch=False)
                added += len(succeeded)
                failed += len(chunk_failures)
                failures = (failures + [item.message for item in chunk_failures])[-ADD_JOB_FAILURES_KEPT:]
                done = start + len(chunk)
                await update_job(
                    db, job,
                    progress={"current": done, "total": len(ids), "percent": round(done * 100 / len(ids), 1)},
                    summary={**dict(job.summary or {}), "done": done, "added": added, "failed": failed, "failures": failures},
                )
                await db.commit()
            message = f"Added {added} to the library" + (f"; {failed} could not be added" if failed else "")
            await update_job(db, job, status=JobStatus.COMPLETED,
                             summary={**dict(job.summary or {}), "message": message})
            await db.commit()
        except Exception as exc:
            await db.rollback()
            job = await db.get(Job, job_id)
            if job is not None:
                await update_job(db, job, status=JobStatus.FAILED, error={"message": str(exc)})
                await db.commit()
            raise
