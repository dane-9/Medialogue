from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.domain import (
    CustomFormat,
    MediaProfileOverride,
    MediaType,
    ProfileCustomFormatScore,
    QualityDefinition,
    QualityProfile,
)
from app.parser.quality import QUALITY_BY_NAME


@dataclass(frozen=True, slots=True)
class EffectiveProfile:
    media_type: MediaType
    entity_id: UUID
    assignment_id: UUID | None
    assignment_revision: int | None
    profile_id: UUID | None
    profile_name: str | None
    profile_revision: int | None
    minimum_quality_definition_id: UUID | None
    minimum_quality_name: str | None
    profile_minimum_quality_definition_id: UUID | None
    profile_minimum_quality_name: str | None
    minimum_quality_overridden: bool
    profile_scores: dict[str, int]
    overrides: dict[str, int]
    effective_scores: dict[str, int]
    score_names: dict[str, str]
    enabled_formats: dict[str, bool]

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "media_type": self.media_type.value,
            "entity_id": str(self.entity_id),
            "assignment_id": str(self.assignment_id) if self.assignment_id else None,
            "assignment_revision": self.assignment_revision,
            "profile_id": str(self.profile_id) if self.profile_id else None,
            "profile_name": self.profile_name,
            "profile_revision": self.profile_revision,
            "minimum_quality_definition_id": str(self.minimum_quality_definition_id) if self.minimum_quality_definition_id else None,
            "minimum_quality": self.minimum_quality_name,
            "profile_minimum_quality_definition_id": str(self.profile_minimum_quality_definition_id) if self.profile_minimum_quality_definition_id else None,
            "profile_minimum_quality": self.profile_minimum_quality_name,
            "minimum_quality_overridden": self.minimum_quality_overridden,
            "profile_scores": dict(self.profile_scores),
            "score_overrides": dict(self.overrides),
            "effective_scores": dict(self.effective_scores),
            "score_names": dict(self.score_names),
            "enabled_formats": dict(self.enabled_formats),
        }


async def get_assignment(
    db: AsyncSession,
    *,
    media_type: MediaType,
    entity_id: UUID,
    for_update: bool = False,
) -> MediaProfileOverride | None:
    statement = select(MediaProfileOverride).where(MediaProfileOverride.media_type == media_type)
    if media_type == MediaType.MOVIES:
        statement = statement.where(MediaProfileOverride.movie_id == entity_id)
    else:
        statement = statement.where(MediaProfileOverride.show_id == entity_id)
    if for_update:
        statement = statement.with_for_update()
    return await db.scalar(statement)


async def load_effective_profile(
    db: AsyncSession,
    *,
    media_type: MediaType,
    entity_id: UUID,
) -> EffectiveProfile:
    assignment = await get_assignment(db, media_type=media_type, entity_id=entity_id)
    profile: QualityProfile | None = None
    if assignment and assignment.quality_profile_id:
        profile = await db.scalar(
            select(QualityProfile)
            .options(
                selectinload(QualityProfile.minimum_quality_definition),
                selectinload(QualityProfile.custom_format_scores).selectinload(ProfileCustomFormatScore.custom_format),
            )
            .where(QualityProfile.id == assignment.quality_profile_id)
        )

    profile_scores: dict[str, int] = {}
    score_names: dict[str, str] = {}
    enabled_formats: dict[str, bool] = {}
    if profile:
        for item in profile.custom_format_scores:
            key = str(item.custom_format_id)
            profile_scores[key] = int(item.score)
            if item.custom_format is not None:
                score_names[key] = item.custom_format.name
                enabled_formats[key] = bool(item.custom_format.enabled)

    override_definition = dict(assignment.override_definition or {}) if assignment else {}
    raw_overrides = override_definition.get("custom_format_scores") or {}
    overrides = {str(key): int(value) for key, value in raw_overrides.items()}

    # Resolve names/enabled state for override-only formats too.
    missing_format_ids = []
    for key in overrides:
        if key not in score_names:
            try:
                missing_format_ids.append(UUID(key))
            except ValueError:
                continue
    if missing_format_ids:
        rows = (await db.scalars(select(CustomFormat).where(CustomFormat.id.in_(missing_format_ids)))).all()
        for row in rows:
            key = str(row.id)
            score_names[key] = row.name
            enabled_formats[key] = row.enabled

    effective_scores = dict(profile_scores)
    effective_scores.update(overrides)

    profile_minimum_id = profile.minimum_quality_definition_id if profile else None
    profile_minimum_name = profile.minimum_quality_definition.name if profile and profile.minimum_quality_definition else None
    override_minimum_raw = override_definition.get("minimum_quality_definition_id")
    override_minimum_id: UUID | None = None
    override_minimum_name: str | None = None
    if override_minimum_raw:
        try:
            override_minimum_id = UUID(str(override_minimum_raw))
        except ValueError:
            override_minimum_id = None
        if override_minimum_id:
            qd = await db.get(QualityDefinition, override_minimum_id)
            if qd is not None and qd.enabled:
                override_minimum_name = qd.name
            else:
                override_minimum_id = None

    return EffectiveProfile(
        media_type=media_type,
        entity_id=entity_id,
        assignment_id=assignment.id if assignment else None,
        assignment_revision=assignment.revision if assignment else None,
        profile_id=profile.id if profile else None,
        profile_name=profile.name if profile else None,
        profile_revision=profile.revision if profile else None,
        minimum_quality_definition_id=override_minimum_id or profile_minimum_id,
        minimum_quality_name=override_minimum_name or profile_minimum_name,
        profile_minimum_quality_definition_id=profile_minimum_id,
        profile_minimum_quality_name=profile_minimum_name,
        minimum_quality_overridden=override_minimum_id is not None,
        profile_scores=profile_scores,
        overrides=overrides,
        effective_scores=effective_scores,
        score_names=score_names,
        enabled_formats=enabled_formats,
    )


def minimum_quality_status(candidate_quality: str | None, minimum_quality: str | None) -> bool | None:
    """Return whether a classified candidate satisfies the configured warning floor.

    The hardcoded rank is used only for this explicit minimum-quality warning.
    It is never an automatic upgrade preference or a download rejection rule.
    """

    if not minimum_quality:
        return None
    if not candidate_quality:
        return None
    candidate = QUALITY_BY_NAME.get(candidate_quality)
    minimum = QUALITY_BY_NAME.get(minimum_quality)
    if candidate is None or minimum is None:
        return None
    return int(candidate.rank) >= int(minimum.rank)


async def assigned_title_count(db: AsyncSession, profile_id: UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(MediaProfileOverride)
            .where(MediaProfileOverride.quality_profile_id == profile_id)
        )
        or 0
    )


async def validate_profile_references(
    db: AsyncSession,
    *,
    minimum_quality_definition_id: UUID | None,
    score_format_ids: list[UUID],
) -> None:
    if minimum_quality_definition_id is not None:
        quality = await db.get(QualityDefinition, minimum_quality_definition_id)
        if quality is None or not quality.enabled:
            raise ValueError("Minimum Quality Definition does not exist or is disabled")
    if score_format_ids:
        rows = (await db.scalars(select(CustomFormat.id).where(CustomFormat.id.in_(score_format_ids)))).all()
        found = set(rows)
        missing = [str(item) for item in score_format_ids if item not in found]
        if missing:
            raise ValueError(f"Custom Format does not exist: {', '.join(missing)}")


async def profile_name_exists(db: AsyncSession, name: str, *, excluding: UUID | None = None) -> bool:
    statement = select(QualityProfile.id).where(func.lower(QualityProfile.name) == name.strip().casefold())
    if excluding is not None:
        statement = statement.where(QualityProfile.id != excluding)
    return (await db.scalar(statement)) is not None


async def evaluate_current_release_score(
    db: AsyncSession,
    *,
    media_type: MediaType,
    entity_id: UUID,
    release_name: str,
    indexer: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Evaluate a release under the currently configured profile/rules.

    Unlike Interactive Search snapshots, this result is intentionally current
    and may change after a profile, override, parser, or Custom Format edit.
    """

    from app.services.custom_formats import CustomFormat as EvaluationFormat, evaluate_custom_formats
    from app.parser import parse_release_name

    effective = await load_effective_profile(db, media_type=media_type, entity_id=entity_id)
    # Compare enum values in Python-friendly form so this remains portable in
    # both PostgreSQL and SQLite test databases.
    from app.models.domain import MediaScope

    eligible = (MediaScope.MOVIES, MediaScope.BOTH) if media_type == MediaType.MOVIES else (MediaScope.SHOWS, MediaScope.BOTH)
    rows = (
        await db.scalars(
            select(CustomFormat)
            .where(CustomFormat.enabled.is_(True), CustomFormat.media_scope.in_(eligible))
            .order_by(CustomFormat.name)
        )
    ).all()
    formats = [
        EvaluationFormat.from_dict(
            {
                "id": str(row.id),
                "name": row.name,
                "description": row.description,
                "media_scope": row.media_scope.value,
                "enabled": row.enabled,
                "condition_definition": dict(row.condition_definition or {}),
            }
        )
        for row in rows
    ]
    parsed = parse_release_name(release_name)
    evaluation = evaluate_custom_formats(
        formats,
        parsed,
        profile_scores=effective.profile_scores,
        score_overrides=effective.overrides,
        context={"indexer": indexer} if indexer else {},
    )
    floor = minimum_quality_status(parsed.quality.canonical, effective.minimum_quality_name)
    breakdown: list[dict[str, Any]] = []
    for item in evaluation.formats:
        key = str(item.custom_format_id)
        profile_score = int(effective.profile_scores.get(key, 0))
        override = effective.overrides.get(key)
        effective_score = int(override if override is not None else profile_score)
        breakdown.append(
            {
                "custom_format_id": key,
                "custom_format_name": item.custom_format_name,
                "matched": item.matched,
                "profile_score": profile_score,
                "override_score": int(override) if override is not None else None,
                "effective_score": effective_score,
                "contribution": effective_score if item.matched else 0,
                "conditions": [condition.to_dict() for condition in item.conditions],
            }
        )
    return int(evaluation.total_score), {
        "schema_version": 1,
        "profile_id": str(effective.profile_id) if effective.profile_id else None,
        "profile_name": effective.profile_name,
        "profile_revision": effective.profile_revision,
        "assignment_revision": effective.assignment_revision,
        "minimum_quality_definition_id": str(effective.minimum_quality_definition_id) if effective.minimum_quality_definition_id else None,
        "minimum_quality": effective.minimum_quality_name,
        "candidate_quality": parsed.quality.canonical,
        "minimum_quality_met": floor,
        "total_score": int(evaluation.total_score),
        "breakdown": breakdown,
    }


async def refresh_movie_release_scores(db: AsyncSession, movie_id: UUID) -> int:
    from app.models.domain import MovieRelease

    releases = (await db.scalars(select(MovieRelease).where(MovieRelease.movie_id == movie_id))).all()
    for release in releases:
        score, snapshot = await evaluate_current_release_score(
            db,
            media_type=MediaType.MOVIES,
            entity_id=movie_id,
            release_name=release.raw_release_name,
        )
        release.current_custom_format_score = score
        parse_snapshot = dict(release.parse_snapshot or {})
        parse_snapshot["current_score_snapshot"] = snapshot
        release.parse_snapshot = parse_snapshot
    return len(releases)


async def refresh_show_release_scores(db: AsyncSession, show_id: UUID) -> int:
    from app.models.domain import ShowRelease

    releases = (await db.scalars(select(ShowRelease).where(ShowRelease.show_id == show_id))).all()
    for release in releases:
        score, snapshot = await evaluate_current_release_score(
            db,
            media_type=MediaType.SHOWS,
            entity_id=show_id,
            release_name=release.raw_release_name,
        )
        release.current_custom_format_score = score
        parse_snapshot = dict(release.parse_snapshot or {})
        parse_snapshot["current_score_snapshot"] = snapshot
        release.parse_snapshot = parse_snapshot
    return len(releases)


async def refresh_all_release_scores(db: AsyncSession) -> dict[str, int]:
    """Re-evaluate durable current scores for every title with profile settings.

    Custom Format edits are infrequent in this single-user application, while
    keeping the stored current score truthful is important. Search-time
    snapshots are deliberately not touched by this operation.
    """

    assignments = (await db.scalars(select(MediaProfileOverride))).all()
    movie_ids = {row.movie_id for row in assignments if row.movie_id is not None}
    show_ids = {row.show_id for row in assignments if row.show_id is not None}
    movie_releases = 0
    show_releases = 0
    for movie_id in movie_ids:
        movie_releases += await refresh_movie_release_scores(db, movie_id)
    for show_id in show_ids:
        show_releases += await refresh_show_release_scores(db, show_id)
    return {
        "movies": len(movie_ids),
        "shows": len(show_ids),
        "movie_releases": movie_releases,
        "show_releases": show_releases,
    }
