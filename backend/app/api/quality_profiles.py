from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import (
    CustomFormat,
    MediaProfileOverride,
    MediaType,
    Movie,
    ProfileCustomFormatScore,
    QualityDefinition,
    QualityProfile,
    Show,
)
from app.schemas.quality_profiles import (
    EffectiveCustomFormatScore,
    MediaProfileSettingsResponse,
    MediaProfileSettingsUpdate,
    QualityDefinitionResponse,
    QualityProfileCreate,
    QualityProfileResponse,
    QualityProfileScoreResponse,
    QualityProfileUpdate,
)
from app.services.events import create_event
from app.services.quality_profiles import (
    assigned_title_count,
    get_assignment,
    load_effective_profile,
    profile_name_exists,
    refresh_movie_release_scores,
    refresh_show_release_scores,
    validate_profile_references,
)

router = APIRouter(tags=["quality profiles"])


def _quality_response(row: QualityDefinition | None) -> QualityDefinitionResponse | None:
    if row is None:
        return None
    return QualityDefinitionResponse(
        id=row.id,
        name=row.name,
        resolution=row.resolution,
        source=row.source,
        modifier=row.modifier,
        scan_type=row.scan_type,
        rank=row.rank,
        enabled=row.enabled,
    )


async def _profile_response(db: AsyncSession, row: QualityProfile) -> QualityProfileResponse:
    # Ensure relationships are available for API calls that loaded a row via get().
    loaded = await db.scalar(
        select(QualityProfile)
        .options(
            selectinload(QualityProfile.minimum_quality_definition),
            selectinload(QualityProfile.custom_format_scores).selectinload(ProfileCustomFormatScore.custom_format),
        )
        .where(QualityProfile.id == row.id)
    )
    if loaded is None:
        raise AppError("NOT_FOUND", "Quality Profile was not found.", status_code=404)
    scores = sorted(
        (
            QualityProfileScoreResponse(
                custom_format_id=item.custom_format_id,
                custom_format_name=item.custom_format.name if item.custom_format else "Deleted Custom Format",
                score=item.score,
                enabled=bool(item.custom_format.enabled) if item.custom_format else False,
            )
            for item in loaded.custom_format_scores
        ),
        key=lambda item: item.custom_format_name.casefold(),
    )
    return QualityProfileResponse(
        id=loaded.id,
        name=loaded.name,
        minimum_quality_definition=_quality_response(loaded.minimum_quality_definition),
        custom_format_scores=scores,
        assigned_titles=await assigned_title_count(db, loaded.id),
        revision=loaded.revision,
        created_at=loaded.created_at,
        updated_at=loaded.updated_at,
    )


@router.get("/quality-definitions", response_model=list[QualityDefinitionResponse])
async def list_quality_definitions(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[QualityDefinitionResponse]:
    rows = (
        await db.scalars(
            select(QualityDefinition)
            .where(QualityDefinition.enabled.is_(True))
            .order_by(QualityDefinition.rank.desc(), QualityDefinition.name)
        )
    ).all()
    return [_quality_response(row) for row in rows if row is not None]


@router.get("/quality-definitions/{quality_id}", response_model=QualityDefinitionResponse)
async def get_quality_definition(
    quality_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> QualityDefinitionResponse:
    row = await db.get(QualityDefinition, quality_id)
    if row is None or not row.enabled:
        raise AppError("NOT_FOUND", "Quality Definition was not found.", status_code=404)
    return _quality_response(row)  # type: ignore[return-value]


@router.get("/quality-profiles", response_model=list[QualityProfileResponse])
async def list_quality_profiles(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[QualityProfileResponse]:
    rows = (await db.scalars(select(QualityProfile).order_by(QualityProfile.name))).all()
    return [await _profile_response(db, row) for row in rows]


@router.post("/quality-profiles", response_model=QualityProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_quality_profile(
    payload: QualityProfileCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> QualityProfileResponse:
    if await profile_name_exists(db, payload.name):
        raise AppError("QUALITY_PROFILE_NAME_EXISTS", "A Quality Profile with this name already exists.", status_code=409)
    try:
        await validate_profile_references(
            db,
            minimum_quality_definition_id=payload.minimum_quality_definition_id,
            score_format_ids=[item.custom_format_id for item in payload.custom_format_scores],
        )
    except ValueError as exc:
        raise AppError("INVALID_QUALITY_PROFILE", str(exc), status_code=422) from exc
    if len({item.custom_format_id for item in payload.custom_format_scores}) != len(payload.custom_format_scores):
        raise AppError("INVALID_QUALITY_PROFILE", "Each Custom Format can appear only once in a profile.", status_code=422)
    row = QualityProfile(
        name=payload.name,
        minimum_quality_definition_id=payload.minimum_quality_definition_id,
        settings={},
        revision=1,
    )
    db.add(row)
    await db.flush()
    for item in payload.custom_format_scores:
        db.add(ProfileCustomFormatScore(profile_id=row.id, custom_format_id=item.custom_format_id, score=item.score))
    await create_event(
        db,
        "quality_profile.created",
        entity_type="quality_profile",
        entity_id=row.id,
        message=f"Quality Profile {row.name} was created.",
        details={"minimum_quality_definition_id": str(row.minimum_quality_definition_id) if row.minimum_quality_definition_id else None},
    )
    await db.commit()
    return await _profile_response(db, row)


@router.get("/quality-profiles/{profile_id}", response_model=QualityProfileResponse)
async def get_quality_profile(
    profile_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> QualityProfileResponse:
    row = await db.get(QualityProfile, profile_id)
    if row is None:
        raise AppError("NOT_FOUND", "Quality Profile was not found.", status_code=404)
    return await _profile_response(db, row)


@router.patch("/quality-profiles/{profile_id}", response_model=QualityProfileResponse)
async def update_quality_profile(
    profile_id: UUID,
    payload: QualityProfileUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> QualityProfileResponse:
    row = await db.scalar(select(QualityProfile).where(QualityProfile.id == profile_id).with_for_update())
    if row is None:
        raise AppError("NOT_FOUND", "Quality Profile was not found.", status_code=404)
    if payload.expected_revision is not None and payload.expected_revision != row.revision:
        raise AppError("REVISION_CONFLICT", "Quality Profile changed since it was loaded.", status_code=409)
    if payload.name is not None:
        if await profile_name_exists(db, payload.name, excluding=row.id):
            raise AppError("QUALITY_PROFILE_NAME_EXISTS", "A Quality Profile with this name already exists.", status_code=409)
        row.name = payload.name
    if "minimum_quality_definition_id" in payload.model_fields_set:
        try:
            await validate_profile_references(
                db,
                minimum_quality_definition_id=payload.minimum_quality_definition_id,
                score_format_ids=[],
            )
        except ValueError as exc:
            raise AppError("INVALID_QUALITY_PROFILE", str(exc), status_code=422) from exc
        row.minimum_quality_definition_id = payload.minimum_quality_definition_id
    if payload.custom_format_scores is not None:
        if len({item.custom_format_id for item in payload.custom_format_scores}) != len(payload.custom_format_scores):
            raise AppError("INVALID_QUALITY_PROFILE", "Each Custom Format can appear only once in a profile.", status_code=422)
        try:
            await validate_profile_references(
                db,
                minimum_quality_definition_id=None,
                score_format_ids=[item.custom_format_id for item in payload.custom_format_scores],
            )
        except ValueError as exc:
            raise AppError("INVALID_QUALITY_PROFILE", str(exc), status_code=422) from exc
        await db.execute(delete(ProfileCustomFormatScore).where(ProfileCustomFormatScore.profile_id == row.id))
        for item in payload.custom_format_scores:
            db.add(ProfileCustomFormatScore(profile_id=row.id, custom_format_id=item.custom_format_id, score=item.score))
    row.revision += 1
    await db.flush()
    assignments = (
        await db.scalars(select(MediaProfileOverride).where(MediaProfileOverride.quality_profile_id == row.id))
    ).all()
    for assignment in assignments:
        if assignment.movie_id:
            await refresh_movie_release_scores(db, assignment.movie_id)
        elif assignment.show_id:
            await refresh_show_release_scores(db, assignment.show_id)
    await create_event(
        db,
        "quality_profile.updated",
        entity_type="quality_profile",
        entity_id=row.id,
        message=f"Quality Profile {row.name} was updated.",
        details={"revision": row.revision, "assigned_titles": len(assignments)},
    )
    await db.commit()
    return await _profile_response(db, row)


@router.delete("/quality-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quality_profile(
    profile_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> Response:
    row = await db.get(QualityProfile, profile_id)
    if row is None:
        raise AppError("NOT_FOUND", "Quality Profile was not found.", status_code=404)
    name = row.name
    assignments = (
        await db.scalars(select(MediaProfileOverride).where(MediaProfileOverride.quality_profile_id == row.id))
    ).all()
    # Keep title-level overrides when their base profile is deleted. They are
    # intentional title rules and should immediately become override-only.
    for assignment in assignments:
        assignment.quality_profile_id = None
        assignment.revision += 1
    await db.execute(delete(ProfileCustomFormatScore).where(ProfileCustomFormatScore.profile_id == row.id))
    await db.delete(row)
    await db.flush()
    for assignment in assignments:
        if assignment.movie_id:
            await refresh_movie_release_scores(db, assignment.movie_id)
        elif assignment.show_id:
            await refresh_show_release_scores(db, assignment.show_id)
    await create_event(
        db,
        "quality_profile.deleted",
        entity_type="quality_profile",
        entity_id=profile_id,
        message=f"Quality Profile {name} was deleted.",
        details={"affected_titles": len(assignments), "title_overrides_retained": True},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _resolve_target(db: AsyncSession, media_type: MediaType, resource_id: str) -> Movie | Show:
    if media_type == MediaType.MOVIES:
        row: Movie | None = None
        if resource_id.isdigit():
            row = await db.scalar(select(Movie).where(Movie.tmdb_id == int(resource_id)))
        else:
            try:
                row = await db.get(Movie, UUID(resource_id))
            except ValueError:
                row = None
    else:
        row = None
        if resource_id.isdigit():
            row = await db.scalar(select(Show).where(Show.tmdb_id == int(resource_id)))
        else:
            try:
                row = await db.get(Show, UUID(resource_id))
            except ValueError:
                row = None
    if row is None:
        raise AppError("NOT_FOUND", f"{media_type.value[:-1].title()} was not found.", status_code=404)
    return row


async def _settings_response(db: AsyncSession, media_type: MediaType, entity_id: UUID) -> MediaProfileSettingsResponse:
    effective = await load_effective_profile(db, media_type=media_type, entity_id=entity_id)
    all_ids: list[UUID] = []
    for key in effective.effective_scores:
        try:
            all_ids.append(UUID(key))
        except ValueError:
            pass
    rows = (await db.scalars(select(CustomFormat).where(CustomFormat.id.in_(all_ids)))).all() if all_ids else []
    formats = {str(row.id): row for row in rows}
    score_rows: list[EffectiveCustomFormatScore] = []
    for key in sorted(effective.effective_scores, key=lambda item: effective.score_names.get(item, item).casefold()):
        try:
            cf_id = UUID(key)
        except ValueError:
            continue
        profile_score = int(effective.profile_scores.get(key, 0))
        override = effective.overrides.get(key)
        row = formats.get(key)
        score_rows.append(
            EffectiveCustomFormatScore(
                custom_format_id=cf_id,
                custom_format_name=effective.score_names.get(key, row.name if row else "Deleted Custom Format"),
                profile_score=profile_score,
                override_score=int(override) if override is not None else None,
                effective_score=int(effective.effective_scores[key]),
                enabled=bool(row.enabled) if row else bool(effective.enabled_formats.get(key, False)),
            )
        )
    minimum = await db.get(QualityDefinition, effective.minimum_quality_definition_id) if effective.minimum_quality_definition_id else None
    profile_minimum = await db.get(QualityDefinition, effective.profile_minimum_quality_definition_id) if effective.profile_minimum_quality_definition_id else None
    return MediaProfileSettingsResponse(
        media_type=media_type.value,
        entity_id=entity_id,
        quality_profile_id=effective.profile_id,
        quality_profile_name=effective.profile_name,
        minimum_quality_definition=_quality_response(minimum),
        profile_minimum_quality_definition=_quality_response(profile_minimum),
        minimum_quality_overridden=effective.minimum_quality_overridden,
        custom_format_scores=score_rows,
        revision=effective.assignment_revision or 0,
    )


@router.get("/movies/{resource_id}/profile-settings", response_model=MediaProfileSettingsResponse)
async def get_movie_profile_settings(
    resource_id: str,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MediaProfileSettingsResponse:
    movie = await _resolve_target(db, MediaType.MOVIES, resource_id)
    return await _settings_response(db, MediaType.MOVIES, movie.id)


@router.put("/movies/{resource_id}/profile-settings", response_model=MediaProfileSettingsResponse)
async def set_movie_profile_settings(
    resource_id: str,
    payload: MediaProfileSettingsUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> MediaProfileSettingsResponse:
    movie = await _resolve_target(db, MediaType.MOVIES, resource_id)
    await _save_settings(db, MediaType.MOVIES, movie.id, payload)
    return await _settings_response(db, MediaType.MOVIES, movie.id)


@router.get("/shows/{resource_id}/profile-settings", response_model=MediaProfileSettingsResponse)
async def get_show_profile_settings(
    resource_id: str,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MediaProfileSettingsResponse:
    show = await _resolve_target(db, MediaType.SHOWS, resource_id)
    return await _settings_response(db, MediaType.SHOWS, show.id)


@router.put("/shows/{resource_id}/profile-settings", response_model=MediaProfileSettingsResponse)
async def set_show_profile_settings(
    resource_id: str,
    payload: MediaProfileSettingsUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> MediaProfileSettingsResponse:
    show = await _resolve_target(db, MediaType.SHOWS, resource_id)
    await _save_settings(db, MediaType.SHOWS, show.id, payload)
    return await _settings_response(db, MediaType.SHOWS, show.id)


async def _save_settings(
    db: AsyncSession,
    media_type: MediaType,
    entity_id: UUID,
    payload: MediaProfileSettingsUpdate,
) -> None:
    if payload.quality_profile_id is not None and await db.get(QualityProfile, payload.quality_profile_id) is None:
        raise AppError("INVALID_QUALITY_PROFILE", "Quality Profile does not exist.", status_code=422)
    try:
        await validate_profile_references(
            db,
            minimum_quality_definition_id=payload.minimum_quality_definition_override_id,
            score_format_ids=list(payload.custom_format_score_overrides),
        )
    except ValueError as exc:
        raise AppError("INVALID_PROFILE_OVERRIDE", str(exc), status_code=422) from exc
    row = await get_assignment(db, media_type=media_type, entity_id=entity_id, for_update=True)
    if row is None:
        if payload.expected_revision not in {None, 0}:
            raise AppError("REVISION_CONFLICT", "Profile assignment changed since it was loaded.", status_code=409)
        row = MediaProfileOverride(
            media_type=media_type,
            movie_id=entity_id if media_type == MediaType.MOVIES else None,
            show_id=entity_id if media_type == MediaType.SHOWS else None,
            revision=1,
        )
        db.add(row)
    else:
        if payload.expected_revision is not None and payload.expected_revision != row.revision:
            raise AppError("REVISION_CONFLICT", "Profile assignment changed since it was loaded.", status_code=409)
        row.revision += 1
    row.quality_profile_id = payload.quality_profile_id
    row.override_definition = {
        "minimum_quality_definition_id": str(payload.minimum_quality_definition_override_id) if payload.minimum_quality_definition_override_id else None,
        "custom_format_scores": {str(key): int(value) for key, value in payload.custom_format_score_overrides.items()},
    }
    await db.flush()
    if media_type == MediaType.MOVIES:
        await refresh_movie_release_scores(db, entity_id)
    else:
        await refresh_show_release_scores(db, entity_id)
    await create_event(
        db,
        "quality_profile.assignment_updated",
        entity_type="movie" if media_type == MediaType.MOVIES else "show",
        entity_id=entity_id,
        message="Quality Profile settings were updated.",
        details={
            "quality_profile_id": str(row.quality_profile_id) if row.quality_profile_id else None,
            "minimum_quality_override_id": str(payload.minimum_quality_definition_override_id) if payload.minimum_quality_definition_override_id else None,
            "custom_format_override_count": len(payload.custom_format_score_overrides),
            "revision": row.revision,
        },
    )
    await db.commit()
