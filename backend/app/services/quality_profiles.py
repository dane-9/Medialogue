from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
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
from collections.abc import Sequence

from app.services.builtin_formats import DEFAULT_QUALITY_PROFILE_MARKER


#: Marks the profile bootstrap created. Provenance only: it stops reseeding, and
#: is deliberately separate from which profile is currently the default, so the
#: user can hand that role to a profile they made themselves.
def is_seeded_profile(profile: QualityProfile) -> bool:
    return (profile.settings or {}).get("system_profile") == DEFAULT_QUALITY_PROFILE_MARKER


def is_marked_default(profile: QualityProfile) -> bool:
    return bool((profile.settings or {}).get("default_profile"))


def resolve_default_profile(profiles: Sequence[QualityProfile]) -> QualityProfile | None:
    """Which profile titles without one of their own inherit.

    The user's explicit choice wins. Falling back to the seeded profile keeps
    installs that never made a choice behaving exactly as they did before the
    role became selectable, so no migration is needed.
    """

    chosen = next((row for row in profiles if is_marked_default(row)), None)
    return chosen or next((row for row in profiles if is_seeded_profile(row)), None)


async def load_default_profile(db: AsyncSession) -> QualityProfile | None:
    rows = (
        await db.scalars(
            select(QualityProfile).options(
                selectinload(QualityProfile.minimum_quality_definition),
                selectinload(QualityProfile.custom_format_scores).selectinload(ProfileCustomFormatScore.custom_format),
            )
        )
    ).all()
    return resolve_default_profile(rows)


@dataclass(frozen=True, slots=True)
class EffectiveProfile:
    media_type: MediaType
    entity_id: UUID | None
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
    minimum_custom_format_score: int | None
    profile_minimum_custom_format_score: int | None
    minimum_custom_format_score_overridden: bool
    season_pack_upgrades: bool
    quality_order: tuple[str, ...]
    profile_scores: dict[str, int]
    overrides: dict[str, int]
    effective_scores: dict[str, int]
    score_names: dict[str, str]
    enabled_formats: dict[str, bool]
    #: True when the title has no profile of its own and is using the default.
    profile_inherited: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "media_type": self.media_type.value,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "assignment_id": str(self.assignment_id) if self.assignment_id else None,
            "assignment_revision": self.assignment_revision,
            "profile_id": str(self.profile_id) if self.profile_id else None,
            "profile_name": self.profile_name,
            "profile_revision": self.profile_revision,
            "profile_inherited": self.profile_inherited,
            "minimum_quality_definition_id": str(self.minimum_quality_definition_id) if self.minimum_quality_definition_id else None,
            "minimum_quality": self.minimum_quality_name,
            "profile_minimum_quality_definition_id": str(self.profile_minimum_quality_definition_id) if self.profile_minimum_quality_definition_id else None,
            "profile_minimum_quality": self.profile_minimum_quality_name,
            "minimum_quality_overridden": self.minimum_quality_overridden,
            "minimum_custom_format_score": self.minimum_custom_format_score,
            "profile_minimum_custom_format_score": self.profile_minimum_custom_format_score,
            "minimum_custom_format_score_overridden": self.minimum_custom_format_score_overridden,
            "season_pack_upgrades": self.season_pack_upgrades,
            "quality_order": list(self.quality_order),
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
    # No explicit assignment means "inherit the default", not "score nothing".
    # Resolved here rather than written as a row per title so that an inherited
    # profile stays distinguishable from one the user actually chose, which is
    # what the assigned-title counts report.
    profile_inherited = profile is None
    if profile is None:
        profile = await load_default_profile(db)

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
    profile_minimum_custom_format_score = (
        int(profile.settings["minimum_custom_format_score"])
        if profile and (profile.settings or {}).get("minimum_custom_format_score") is not None
        else None
    )
    override_minimum_custom_format_score = (
        int(override_definition["minimum_custom_format_score"])
        if override_definition.get("minimum_custom_format_score") is not None
        else None
    )

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

    quality_rows = (
        await db.scalars(
            select(QualityDefinition)
            .where(QualityDefinition.enabled.is_(True))
            .order_by(QualityDefinition.rank.desc(), QualityDefinition.name)
        )
    ).all()
    quality_by_id = {str(item.id): item for item in quality_rows}
    configured_quality_ids = (profile.settings or {}).get("quality_definition_ids") if profile else None
    if isinstance(configured_quality_ids, list):
        quality_order = tuple(
            quality_by_id[str(quality_id)].name
            for quality_id in configured_quality_ids
            if str(quality_id) in quality_by_id
        )
    else:
        quality_order = tuple(item.name for item in quality_rows)

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
        minimum_custom_format_score=(
            override_minimum_custom_format_score
            if override_minimum_custom_format_score is not None
            else profile_minimum_custom_format_score
        ),
        profile_minimum_custom_format_score=profile_minimum_custom_format_score,
        minimum_custom_format_score_overridden=override_minimum_custom_format_score is not None,
        season_pack_upgrades=bool((profile.settings or {}).get("season_pack_upgrades", True)) if profile else True,
        quality_order=quality_order,
        profile_scores=profile_scores,
        overrides=overrides,
        effective_scores=effective_scores,
        score_names=score_names,
        enabled_formats=enabled_formats,
        profile_inherited=profile_inherited and profile is not None,
    )


async def load_quality_profile_for_search(
    db: AsyncSession,
    *,
    media_type: MediaType,
    profile_id: UUID,
) -> EffectiveProfile:
    """Load a Quality Profile without requiring a persisted Movie/Show.

    Manual download searches can begin from a TMDB result before the Movie
    exists in Medialogue. Those searches still need the same immutable quality
    and Custom Format policy snapshot as library searches, but there is no
    per-title assignment or override yet.
    """

    profile = await db.scalar(
        select(QualityProfile)
        .options(
            selectinload(QualityProfile.minimum_quality_definition),
            selectinload(QualityProfile.custom_format_scores).selectinload(ProfileCustomFormatScore.custom_format),
        )
        .where(QualityProfile.id == profile_id)
    )
    if profile is None:
        raise ValueError("Quality Profile does not exist")

    profile_scores: dict[str, int] = {}
    score_names: dict[str, str] = {}
    enabled_formats: dict[str, bool] = {}
    for item in profile.custom_format_scores:
        key = str(item.custom_format_id)
        profile_scores[key] = int(item.score)
        if item.custom_format is not None:
            score_names[key] = item.custom_format.name
            enabled_formats[key] = bool(item.custom_format.enabled)

    quality_rows = (
        await db.scalars(
            select(QualityDefinition)
            .where(QualityDefinition.enabled.is_(True))
            .order_by(QualityDefinition.rank.desc(), QualityDefinition.name)
        )
    ).all()
    quality_by_id = {str(item.id): item for item in quality_rows}
    configured_quality_ids = (profile.settings or {}).get("quality_definition_ids")
    if isinstance(configured_quality_ids, list):
        quality_order = tuple(
            quality_by_id[str(quality_id)].name
            for quality_id in configured_quality_ids
            if str(quality_id) in quality_by_id
        )
    else:
        quality_order = tuple(item.name for item in quality_rows)

    minimum_id = profile.minimum_quality_definition_id
    minimum_name = profile.minimum_quality_definition.name if profile.minimum_quality_definition else None
    minimum_custom_format_score = (
        int(profile.settings["minimum_custom_format_score"])
        if (profile.settings or {}).get("minimum_custom_format_score") is not None
        else None
    )
    return EffectiveProfile(
        media_type=media_type,
        entity_id=None,
        assignment_id=None,
        assignment_revision=None,
        profile_id=profile.id,
        profile_name=profile.name,
        profile_revision=profile.revision,
        minimum_quality_definition_id=minimum_id,
        minimum_quality_name=minimum_name,
        profile_minimum_quality_definition_id=minimum_id,
        profile_minimum_quality_name=minimum_name,
        minimum_quality_overridden=False,
        minimum_custom_format_score=minimum_custom_format_score,
        profile_minimum_custom_format_score=minimum_custom_format_score,
        minimum_custom_format_score_overridden=False,
        season_pack_upgrades=bool((profile.settings or {}).get("season_pack_upgrades", True)),
        quality_order=quality_order,
        profile_scores=profile_scores,
        overrides={},
        effective_scores=dict(profile_scores),
        score_names=score_names,
        enabled_formats=enabled_formats,
    )


def minimum_quality_status(
    candidate_quality: str | None,
    minimum_quality: str | None,
    quality_order: tuple[str, ...] | list[str] | None = None,
) -> bool | None:
    """Return whether a classified candidate satisfies the configured warning floor.

    A profile's top-to-bottom quality order is authoritative. Hardcoded ranks
    remain only as a compatibility fallback for legacy callers without an
    order snapshot.
    """

    if not minimum_quality:
        return None
    if not candidate_quality:
        return None
    if not quality_order:
        return None
    try:
        return quality_order.index(candidate_quality) <= quality_order.index(minimum_quality)
    except ValueError:
        return None



async def assigned_titles_by_type(db: AsyncSession, profile_id: UUID) -> tuple[int, int]:
    """Movies and shows explicitly assigned to a profile.

    Split rather than totalled: deleting a profile drops these titles back to
    the default, and "3 movies and 1 show" says what is at stake in a way one
    number does not.
    """

    rows = (
        await db.execute(
            select(MediaProfileOverride.media_type, func.count())
            .where(MediaProfileOverride.quality_profile_id == profile_id)
            .group_by(MediaProfileOverride.media_type)
        )
    ).all()
    counts = {media_type: int(count) for media_type, count in rows}
    return counts.get(MediaType.MOVIES, 0), counts.get(MediaType.SHOWS, 0)


async def validate_profile_references(
    db: AsyncSession,
    *,
    minimum_quality_definition_id: UUID | None,
    score_format_ids: list[UUID],
    quality_definition_ids: list[UUID] | None = None,
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
    if quality_definition_ids is not None:
        if not quality_definition_ids:
            raise ValueError("A Quality Profile must use at least one quality")
        if len(set(quality_definition_ids)) != len(quality_definition_ids):
            raise ValueError("Each quality can appear only once in a profile")
        rows = (
            await db.scalars(
                select(QualityDefinition.id).where(
                    QualityDefinition.id.in_(quality_definition_ids),
                    QualityDefinition.enabled.is_(True),
                )
            )
        ).all()
        missing = [str(item) for item in quality_definition_ids if item not in set(rows)]
        if missing:
            raise ValueError(f"Quality Definition does not exist or is disabled: {', '.join(missing)}")
        if minimum_quality_definition_id is not None and minimum_quality_definition_id not in quality_definition_ids:
            raise ValueError("The minimum quality warning must be one of the profile's enabled qualities")


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
    from app.parser import parse_release

    effective = await load_effective_profile(db, media_type=media_type, entity_id=entity_id)
    # Compare enum values in Python-friendly form so this remains portable in
    # SQLite and the Python-side scoring model.
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
    parsed = parse_release(release_name)
    evaluation = evaluate_custom_formats(
        formats,
        parsed,
        profile_scores=effective.profile_scores,
        score_overrides=effective.overrides,
        context={"indexer": indexer} if indexer else {},
    )
    floor = minimum_quality_status(
        parsed.quality.canonical,
        effective.minimum_quality_name,
        effective.quality_order,
    )
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
                "score_offset": int(item.score_offset),
                "contribution": int(item.score_contribution),
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
