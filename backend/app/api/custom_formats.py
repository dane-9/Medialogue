from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin, require_csrf
from app.core.errors import AppError
from app.core.custom_format_layout import read_custom_format_layout, save_custom_format_layout
from app.db.session import get_db
from app.models.auth import AdminUser
from app.models.domain import CustomFormat as CustomFormatModel
from app.models.domain import MediaProfileOverride, MediaScope, ProfileCustomFormatScore, QualityProfile
from app.schemas.common import Collection, DeleteResponse
from app.schemas.custom_formats import (
    CUSTOM_FORMAT_SCHEMA_VERSION,
    CustomFormatConditionResponse,
    CustomFormatCreate,
    CustomFormatEvaluationResponse,
    CustomFormatExportBundle,
    CustomFormatExportItem,
    CustomFormatImportBundle,
    CustomFormatImportResponse,
    CustomFormatLayout,
    CustomFormatSection,
    CustomFormatResponse,
    CustomFormatScope,
    CustomFormatTestAllRequest,
    CustomFormatTestAllResponse,
    CustomFormatTestDefinition,
    CustomFormatTestRequest,
    CustomFormatTestResponse,
    CustomFormatUpdate,
)
from app.services.custom_formats import (
    CustomFormat as EvaluationFormat,
    FormatCondition,
    evaluate_custom_format,
    evaluate_custom_formats,
    validate_custom_format,
)
from app.services.events import create_event
from app.services.quality_profiles import refresh_all_release_scores

router = APIRouter(prefix="/custom-formats", tags=["custom-formats"])


def _section_id(name: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in name).strip("-") or "section"


def _default_layout(rows: list[CustomFormatModel]) -> CustomFormatLayout:
    from app.services.builtin_formats import BUILTIN_FORMATS

    builtin_groups = {item.key: item.group for item in BUILTIN_FORMATS}
    names = list(dict.fromkeys(item.group for item in BUILTIN_FORMATS))
    grouped: dict[str, list[UUID]] = {name: [] for name in names}
    for row in rows:
        grouped[builtin_groups.get(row.builtin_key, "Release")].append(row.id)
    return CustomFormatLayout(
        sections=[
            CustomFormatSection(id=_section_id(name), name=name, format_ids=grouped[name])
            for name in names
            if grouped[name]
        ]
    )


def _reconciled_layout(rows: list[CustomFormatModel], stored: dict[str, Any] | None) -> CustomFormatLayout:
    if stored is None:
        return _default_layout(rows)
    try:
        layout = CustomFormatLayout.model_validate({"sections": stored.get("sections")})
    except Exception as exc:
        raise AppError("CUSTOM_FORMAT_LAYOUT_INVALID", f"Stored Custom Format layout is invalid: {exc}", status_code=500) from exc
    known = {row.id for row in rows}
    seen: set[UUID] = set()
    sections: list[CustomFormatSection] = []
    for section in layout.sections:
        # Older installations had a permanent catch-all section named Custom.
        # Formats now choose a meaningful section directly, so discard that
        # legacy wrapper and reconcile its formats into Release below.
        if section.id.casefold() == "custom" and section.name.strip().casefold() == "custom":
            continue
        section_id = section.id
        section_name = section.name
        if section.id.casefold() == "hdr" and section.name.strip().casefold() == "hdr":
            section_id = "dynamic-range"
            section_name = "Dynamic Range"
        values = [format_id for format_id in section.format_ids if format_id in known and format_id not in seen]
        seen.update(values)
        sections.append(CustomFormatSection(id=section_id, name=section_name, format_ids=values))
    if not sections:
        return _default_layout(rows)
    missing = [row for row in rows if row.id not in seen]
    if missing:
        from app.services.builtin_formats import BUILTIN_FORMATS

        builtin_groups = {item.key: item.group for item in BUILTIN_FORMATS}
        fallback = next((section for section in sections if section.id == "release" or section.name.casefold() == "release"), sections[-1])
        for row in missing:
            group = builtin_groups.get(row.builtin_key)
            target = next(
                (
                    section
                    for section in sections
                    if group and (section.id == _section_id(group) or section.name.casefold() == group.casefold())
                ),
                fallback,
            )
            target.format_ids.append(row.id)
    return CustomFormatLayout(sections=sections)


def _scope(value: CustomFormatScope | str) -> MediaScope:
    return MediaScope(value.value if hasattr(value, "value") else str(value))


def _normalized_conditions(values: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for value in values:
        raw = value.model_dump(mode="json", exclude_none=True) if hasattr(value, "model_dump") else dict(value)
        normalized.append(FormatCondition.from_dict(raw).to_dict())
    return normalized


def _definition(conditions: list[Any]) -> dict[str, Any]:
    return {
        "schema_version": CUSTOM_FORMAT_SCHEMA_VERSION,
        "conditions": _normalized_conditions(conditions),
    }


def _evaluation_format_from_model(row: CustomFormatModel) -> EvaluationFormat:
    return EvaluationFormat.from_dict(
        {
            "id": str(row.id),
            "name": row.name,
            "description": row.description,
            "media_scope": row.media_scope.value,
            "enabled": row.enabled,
            "condition_definition": dict(row.condition_definition or {}),
        }
    )


def _evaluation_format_from_test(value: CustomFormatTestDefinition) -> EvaluationFormat:
    return EvaluationFormat.from_dict(
        {
            "id": str(value.id) if value.id else None,
            "name": value.name,
            "description": value.description,
            "media_scope": value.media_scope.value,
            "enabled": value.enabled,
            "condition_definition": _definition(value.conditions),
        }
    )


def _evaluation_response(value: Any, *, include_profile_score: bool = False) -> CustomFormatEvaluationResponse:
    return CustomFormatEvaluationResponse(
        custom_format_id=value.custom_format_id,
        custom_format_name=value.custom_format_name,
        matched=value.matched,
        conditions=[condition.to_dict() for condition in value.conditions],
        group_results=dict(value.group_results),
        score_offset=int(value.score_offset),
        profile_score=int(value.score) if include_profile_score else None,
        contribution=int(value.score_contribution) if include_profile_score else None,
        error=value.error,
    )


async def _used_by_profiles(db: AsyncSession, custom_format_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(ProfileCustomFormatScore)
            .where(ProfileCustomFormatScore.custom_format_id == custom_format_id)
        )
        or 0
    )


async def _response(db: AsyncSession, row: CustomFormatModel) -> CustomFormatResponse:
    fmt = _evaluation_format_from_model(row)
    conditions = [CustomFormatConditionResponse(**condition.to_dict()) for condition in fmt.conditions]
    return CustomFormatResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        media_scope=CustomFormatScope(row.media_scope.value),
        enabled=row.enabled,
        builtin=row.builtin,
        schema_version=int((row.condition_definition or {}).get("schema_version", CUSTOM_FORMAT_SCHEMA_VERSION)),
        conditions=conditions,
        condition_count=len(conditions),
        used_by_profiles=await _used_by_profiles(db, row.id),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _ensure_name_available(db: AsyncSession, name: str, *, exclude_id: UUID | None = None) -> None:
    query = select(CustomFormatModel.id).where(func.lower(CustomFormatModel.name) == name.strip().lower())
    if exclude_id is not None:
        query = query.where(CustomFormatModel.id != exclude_id)
    if await db.scalar(query) is not None:
        raise AppError("CUSTOM_FORMAT_NAME_EXISTS", "A Custom Format with that name already exists.", status_code=409)


def _validate_evaluation_format(fmt: EvaluationFormat) -> None:
    errors = validate_custom_format(fmt)
    if errors:
        raise AppError(
            "INVALID_CUSTOM_FORMAT",
            "Custom Format definition is invalid.",
            status_code=422,
            details={"errors": list(errors)},
        )


@router.get("", response_model=Collection[CustomFormatResponse])
async def list_custom_formats(
    page: int = 1,
    page_size: int = 50,
    scope: CustomFormatScope | None = None,
    enabled: bool | None = None,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Collection[CustomFormatResponse]:
    page = max(1, page)
    page_size = min(max(1, page_size), 250)
    conditions = []
    if scope is not None:
        if scope == CustomFormatScope.BOTH:
            conditions.append(CustomFormatModel.media_scope == MediaScope.BOTH)
        else:
            conditions.append(CustomFormatModel.media_scope.in_((_scope(scope), MediaScope.BOTH)))
    if enabled is not None:
        conditions.append(CustomFormatModel.enabled.is_(enabled))
    count_query = select(func.count()).select_from(CustomFormatModel)
    rows_query = select(CustomFormatModel)
    for condition in conditions:
        count_query = count_query.where(condition)
        rows_query = rows_query.where(condition)
    total = int(await db.scalar(count_query) or 0)
    rows = (
        await db.scalars(
            rows_query.order_by(func.lower(CustomFormatModel.name), CustomFormatModel.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Collection(
        items=[await _response(db, row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=(total + page_size - 1) // page_size,
    )


@router.get("/layout", response_model=CustomFormatLayout)
async def get_custom_format_layout(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomFormatLayout:
    rows = (await db.scalars(select(CustomFormatModel).order_by(func.lower(CustomFormatModel.name)))).all()
    return _reconciled_layout(rows, read_custom_format_layout())


@router.put("/layout", response_model=CustomFormatLayout)
async def update_custom_format_layout(
    payload: CustomFormatLayout,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> CustomFormatLayout:
    section_ids = [section.id.casefold() for section in payload.sections]
    section_names = [section.name.strip().casefold() for section in payload.sections]
    if len(set(section_ids)) != len(section_ids) or len(set(section_names)) != len(section_names):
        raise AppError("CUSTOM_FORMAT_SECTION_DUPLICATE", "Section names and identifiers must be unique.", status_code=422)

    format_ids = [format_id for section in payload.sections for format_id in section.format_ids]
    if len(set(format_ids)) != len(format_ids):
        raise AppError("CUSTOM_FORMAT_LAYOUT_DUPLICATE", "A Custom Format can appear in only one section.", status_code=422)
    known_ids = set((await db.scalars(select(CustomFormatModel.id))).all())
    if set(format_ids) != known_ids:
        raise AppError(
            "CUSTOM_FORMAT_LAYOUT_INCOMPLETE",
            "The layout must contain every current Custom Format exactly once.",
            status_code=409,
        )

    normalized = CustomFormatLayout(
        sections=[
            CustomFormatSection(id=section.id, name=section.name.strip(), format_ids=section.format_ids)
            for section in payload.sections
        ]
    )
    save_custom_format_layout([section.model_dump(mode="json") for section in normalized.sections])
    await create_event(
        db,
        "custom_format.layout_updated",
        entity_type="custom_format_layout",
        message="Custom Format sections and ordering were updated.",
        details={"section_count": len(normalized.sections), "format_count": len(format_ids)},
    )
    await db.commit()
    return normalized


@router.post("", response_model=CustomFormatResponse, status_code=status.HTTP_201_CREATED)
async def create_custom_format(
    payload: CustomFormatCreate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> CustomFormatResponse:
    name = payload.name.strip()
    await _ensure_name_available(db, name)
    definition = _definition(payload.conditions)
    fmt = EvaluationFormat.from_dict(
        {
            "name": name,
            "description": payload.description,
            "media_scope": payload.media_scope.value,
            "enabled": payload.enabled,
            "condition_definition": definition,
        }
    )
    _validate_evaluation_format(fmt)
    row = CustomFormatModel(
        name=name,
        description=payload.description.strip() if payload.description else None,
        media_scope=_scope(payload.media_scope),
        enabled=payload.enabled,
        condition_definition=definition,
        revision=1,
    )
    db.add(row)
    await db.flush()
    await create_event(
        db,
        "custom_format.created",
        entity_type="custom_format",
        entity_id=row.id,
        message=f"Custom Format {row.name} was created.",
        details={"media_scope": row.media_scope.value, "condition_count": len(fmt.conditions)},
    )
    await db.commit()
    return await _response(db, row)


# Static routes are declared before /{custom_format_id} so FastAPI never tries
# to interpret names such as "test" and "export" as UUIDs.
@router.post("/test", response_model=CustomFormatTestResponse)
async def test_custom_format(
    payload: CustomFormatTestRequest,
    _: AdminUser = Depends(require_admin),
) -> CustomFormatTestResponse:
    fmt = _evaluation_format_from_test(payload.custom_format)
    _validate_evaluation_format(fmt)
    context = {"indexer": payload.indexer, "languages": payload.languages}
    result = evaluate_custom_format(fmt, payload.release_name, context=context)
    parsed = evaluate_custom_formats([fmt], payload.release_name, context=context).parsed
    return CustomFormatTestResponse(parsed=parsed.to_dict(), evaluation=_evaluation_response(result))


@router.post("/test-all", response_model=CustomFormatTestAllResponse)
async def test_all_custom_formats(
    payload: CustomFormatTestAllRequest,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomFormatTestAllResponse:
    query = select(CustomFormatModel)
    if payload.media_scope == CustomFormatScope.MOVIES:
        query = query.where(CustomFormatModel.media_scope.in_((MediaScope.MOVIES, MediaScope.BOTH)))
    elif payload.media_scope == CustomFormatScope.SHOWS:
        query = query.where(CustomFormatModel.media_scope.in_((MediaScope.SHOWS, MediaScope.BOTH)))
    if not payload.include_disabled:
        query = query.where(CustomFormatModel.enabled.is_(True))
    rows = (await db.scalars(query.order_by(func.lower(CustomFormatModel.name)))).all()
    formats = [_evaluation_format_from_model(row) for row in rows]
    profile: QualityProfile | None = None
    profile_scores: dict[str, int] = {}
    if payload.quality_profile_id is not None:
        profile = await db.get(QualityProfile, payload.quality_profile_id)
        if profile is None:
            raise AppError("NOT_FOUND", "Quality Profile was not found.", status_code=404)
        score_rows = (
            await db.scalars(
                select(ProfileCustomFormatScore).where(ProfileCustomFormatScore.profile_id == profile.id)
            )
        ).all()
        profile_scores = {str(item.custom_format_id): int(item.score) for item in score_rows}

    result = evaluate_custom_formats(
        formats,
        payload.release_name,
        profile_scores=profile_scores,
        context={"indexer": payload.indexer, "languages": payload.languages},
        include_disabled=payload.include_disabled,
    )
    evaluations = [
        _evaluation_response(item, include_profile_score=profile is not None) for item in result.formats
    ]
    return CustomFormatTestAllResponse(
        parsed=result.parsed.to_dict(),
        formats=evaluations,
        matched_count=sum(1 for item in evaluations if item.matched),
        quality_profile_id=profile.id if profile else None,
        quality_profile_name=profile.name if profile else None,
        total_score=int(result.total_score) if profile else None,
    )


@router.get("/export", response_model=CustomFormatExportBundle)
async def export_all_custom_formats(
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomFormatExportBundle:
    rows = (await db.scalars(select(CustomFormatModel).order_by(func.lower(CustomFormatModel.name)))).all()
    return CustomFormatExportBundle(custom_formats=[_export_item(row) for row in rows])


@router.post("/import", response_model=CustomFormatImportResponse, status_code=status.HTTP_201_CREATED)
async def import_custom_formats(
    payload: CustomFormatImportBundle,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> CustomFormatImportResponse:
    if payload.application.casefold() != "medialogue":
        raise AppError("INVALID_CUSTOM_FORMAT_IMPORT", "This file is not a Medialogue Custom Format export.", status_code=422)
    if payload.schema_version != CUSTOM_FORMAT_SCHEMA_VERSION:
        raise AppError(
            "UNSUPPORTED_CUSTOM_FORMAT_SCHEMA",
            f"Custom Format schema {payload.schema_version} is not supported.",
            status_code=422,
        )
    names = [item.name.strip() for item in payload.custom_formats]
    if len({name.casefold() for name in names}) != len(names):
        raise AppError("DUPLICATE_CUSTOM_FORMAT_IMPORT", "Import contains duplicate Custom Format names.", status_code=422)
    for name in names:
        await _ensure_name_available(db, name)

    rows: list[CustomFormatModel] = []
    for item in payload.custom_formats:
        definition = _definition(item.conditions)
        fmt = EvaluationFormat.from_dict(
            {
                "name": item.name.strip(),
                "description": item.description,
                "media_scope": item.media_scope.value,
                "enabled": item.enabled,
                "condition_definition": definition,
            }
        )
        _validate_evaluation_format(fmt)
        row = CustomFormatModel(
            name=item.name.strip(),
            description=item.description.strip() if item.description else None,
            media_scope=_scope(item.media_scope),
            enabled=item.enabled,
            condition_definition=definition,
            revision=1,
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    for row in rows:
        await create_event(
            db,
            "custom_format.imported",
            entity_type="custom_format",
            entity_id=row.id,
            message=f"Custom Format {row.name} was imported.",
            details={"schema_version": CUSTOM_FORMAT_SCHEMA_VERSION},
        )
    await db.commit()
    imported = [await _response(db, row) for row in rows]
    return CustomFormatImportResponse(imported=imported, count=len(imported))


@router.get("/{custom_format_id}", response_model=CustomFormatResponse)
async def get_custom_format(
    custom_format_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomFormatResponse:
    row = await db.get(CustomFormatModel, custom_format_id)
    if row is None:
        raise AppError("NOT_FOUND", "Custom Format was not found.", status_code=404)
    return await _response(db, row)


@router.patch("/{custom_format_id}", response_model=CustomFormatResponse)
@router.put("/{custom_format_id}", response_model=CustomFormatResponse, include_in_schema=False)
async def update_custom_format(
    custom_format_id: UUID,
    payload: CustomFormatUpdate,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> CustomFormatResponse:
    row = await db.get(CustomFormatModel, custom_format_id)
    if row is None:
        raise AppError("NOT_FOUND", "Custom Format was not found.", status_code=404)
    if payload.expected_revision is not None and payload.expected_revision != row.revision:
        raise AppError("REVISION_CONFLICT", "Custom Format changed; refresh and try again.", status_code=409)
    values = payload.model_dump(exclude_unset=True)
    values.pop("expected_revision", None)
    if row.builtin:
        # Medialogue owns the definition of a built-in so pattern fixes can ship
        # to existing installs. Enabling and disabling stays with the operator.
        disallowed = sorted(key for key in values if key != "enabled")
        if disallowed:
            raise AppError(
                "BUILTIN_CUSTOM_FORMAT_READ_ONLY",
                f"{row.name} is a built-in Custom Format. Only 'enabled' can be changed; "
                f"duplicate it if you need a version you can edit. Rejected: {', '.join(disallowed)}.",
                status_code=409,
            )
    if "name" in values and values["name"] is not None:
        name = str(values["name"]).strip()
        await _ensure_name_available(db, name, exclude_id=row.id)
        row.name = name
    if "description" in values:
        description = values["description"]
        row.description = str(description).strip() if description else None
    if "media_scope" in values and values["media_scope"] is not None:
        row.media_scope = _scope(values["media_scope"])
    if "enabled" in values and values["enabled"] is not None:
        row.enabled = bool(values["enabled"])
    if payload.conditions is not None:
        row.condition_definition = _definition(payload.conditions)

    candidate = _evaluation_format_from_model(row)
    _validate_evaluation_format(candidate)
    row.revision += 1
    await create_event(
        db,
        "custom_format.updated",
        entity_type="custom_format",
        entity_id=row.id,
        message=f"Custom Format {row.name} was updated.",
        details={"revision": row.revision, "condition_count": len(candidate.conditions)},
    )
    await db.flush()
    await refresh_all_release_scores(db)
    await db.commit()
    return await _response(db, row)


@router.delete("/{custom_format_id}", response_model=DeleteResponse)
async def delete_custom_format(
    custom_format_id: UUID,
    _: object = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    row = await db.get(CustomFormatModel, custom_format_id)
    if row is None:
        raise AppError("NOT_FOUND", "Custom Format was not found.", status_code=404)
    if row.builtin:
        raise AppError(
            "BUILTIN_CUSTOM_FORMAT_READ_ONLY",
            f"{row.name} is a built-in Custom Format and cannot be deleted. Disable it instead.",
            status_code=409,
        )
    name = row.name
    await create_event(
        db,
        "custom_format.deleted",
        entity_type="custom_format",
        entity_id=row.id,
        message=f"Custom Format {name} was deleted.",
        details={"name": name},
    )

    # Profile rows are explicit application state, so do not rely solely on
    # database ON DELETE behavior. Remove profile scores and stale per-title
    # override keys while preserving every historical search/download snapshot.
    await db.execute(delete(ProfileCustomFormatScore).where(ProfileCustomFormatScore.custom_format_id == row.id))
    assignments = (await db.scalars(select(MediaProfileOverride))).all()
    key = str(row.id)
    for assignment in assignments:
        definition = dict(assignment.override_definition or {})
        overrides = dict(definition.get("custom_format_scores") or {})
        if key in overrides:
            overrides.pop(key, None)
            definition["custom_format_scores"] = overrides
            assignment.override_definition = definition
            assignment.revision += 1
    await db.delete(row)
    await db.flush()
    await refresh_all_release_scores(db)
    await db.commit()
    return DeleteResponse(id=custom_format_id)


def _export_item(row: CustomFormatModel) -> CustomFormatExportItem:
    fmt = _evaluation_format_from_model(row)
    return CustomFormatExportItem(
        name=row.name,
        description=row.description,
        media_scope=CustomFormatScope(row.media_scope.value),
        enabled=row.enabled,
        conditions=[condition.to_dict() for condition in fmt.conditions],
    )


@router.get("/{custom_format_id}/export", response_model=CustomFormatExportBundle)
async def export_custom_format(
    custom_format_id: UUID,
    _: AdminUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CustomFormatExportBundle:
    row = await db.get(CustomFormatModel, custom_format_id)
    if row is None:
        raise AppError("NOT_FOUND", "Custom Format was not found.", status_code=404)
    return CustomFormatExportBundle(custom_formats=[_export_item(row)])
