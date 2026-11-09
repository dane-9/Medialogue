from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import session as db_session
from app.models.domain import (
    CustomFormat,
    InteractiveSearchResult,
    MediaProfileOverride,
    MediaType,
    MovieRelease,
    ProfileCustomFormatScore,
    QualityDefinition,
    QualityProfile,
    ShowRelease,
)
from app.services.events import publish_live_event
from app.services.jobs import with_lock_retries
from collections.abc import Iterator, Sequence

logger = logging.getLogger(__name__)


def is_marked_default(profile: QualityProfile) -> bool:
    return bool((profile.settings or {}).get("default_profile"))


def resolve_default_profile(profiles: Sequence[QualityProfile]) -> QualityProfile | None:
    """Return the profile explicitly configured as the default."""

    return next((row for row in profiles if is_marked_default(row)), None)


# Read-only work that scores many titles at once (the Replace Media queue and
# its sidebar count) would otherwise load the same profiles and quality list
# once per release. Inside `profiles_cached()` each is read once and reused.
# It is opt-in, never session-wide: a request that edits a profile and then
# rescores titles must see its own edit.
_PROFILE_CACHE: ContextVar[dict[Any, Any] | None] = ContextVar("quality_profile_cache", default=None)


@contextmanager
def profiles_cached() -> Iterator[None]:
    """Reuse loaded profiles and quality definitions until the block ends."""

    if _PROFILE_CACHE.get() is not None:
        yield
        return
    token = _PROFILE_CACHE.set({})
    try:
        yield
    finally:
        _PROFILE_CACHE.reset(token)


async def _cached(key: Any, load):
    cache = _PROFILE_CACHE.get()
    if cache is None:
        return await load()
    if key not in cache:
        cache[key] = await load()
    return cache[key]


async def load_default_profile(db: AsyncSession) -> QualityProfile | None:
    return await _cached(("default-profile", id(db)), lambda: _load_default_profile(db))


async def _load_default_profile(db: AsyncSession) -> QualityProfile | None:
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
    if not for_update and _PROFILE_CACHE.get() is not None:
        # Scoring a whole queue asks once per title; read every assignment of
        # this media type in one query instead.
        async def load_all() -> dict[UUID, MediaProfileOverride]:
            rows = (await db.scalars(select(MediaProfileOverride).where(MediaProfileOverride.media_type == media_type))).all()
            return {(row.movie_id if media_type == MediaType.MOVIES else row.show_id): row for row in rows}

        return (await _cached(("assignments", id(db), media_type), load_all)).get(entity_id)
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
    return await _cached(("effective", id(db), media_type, entity_id),
                         lambda: _load_effective_profile(db, media_type=media_type, entity_id=entity_id))


async def _load_effective_profile(
    db: AsyncSession,
    *,
    media_type: MediaType,
    entity_id: UUID,
) -> EffectiveProfile:
    assignment = await get_assignment(db, media_type=media_type, entity_id=entity_id)
    profile: QualityProfile | None = None
    if assignment and assignment.quality_profile_id:
        profile_id = assignment.quality_profile_id
        profile = await _cached(("profile", id(db), profile_id), lambda: db.scalar(
            select(QualityProfile)
            .options(
                selectinload(QualityProfile.minimum_quality_definition),
                selectinload(QualityProfile.custom_format_scores).selectinload(ProfileCustomFormatScore.custom_format),
            )
            .where(QualityProfile.id == profile_id)
        ))
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

    async def load_qualities():
        return (
            await db.scalars(
                select(QualityDefinition)
                .where(QualityDefinition.enabled.is_(True))
                .order_by(QualityDefinition.rank.desc(), QualityDefinition.name)
            )
        ).all()

    quality_rows = await _cached(("qualities", id(db)), load_qualities)
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
            if qd is not None and qd.enabled and qd.name in quality_order:
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

    A profile's top-to-bottom quality order is authoritative. Without an
    order snapshot, the minimum-quality result is unknown.
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


async def _enabled_formats(db: AsyncSession) -> list[CustomFormat]:
    return list((await db.scalars(select(CustomFormat).where(CustomFormat.enabled.is_(True)).order_by(CustomFormat.name))).all())


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
    async def build_formats() -> list[EvaluationFormat]:
        return [
            EvaluationFormat.from_dict(
                {
                    "id": str(row.id),
                    "name": row.name,
                    "description": row.description,
                    "enabled": row.enabled,
                    "condition_definition": dict(row.condition_definition or {}),
                }
            )
            for row in await _enabled_formats(db)
        ]

    formats = await _cached(("enabled-formats", id(db)), build_formats)
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


def preferred_release_name(release: MovieRelease | ShowRelease) -> str:
    """Return the most faithful name Medialogue has for release scoring.

    Tracker/indexer titles retain tokens that may be absent after a client or
    filesystem renames the payload.  Use the durable filesystem name only when
    no tracker title can be recovered.
    """

    tracker_title = str(release.tracker_title or "").strip()
    selection_snapshot = getattr(release, "selection_snapshot", None)
    if not tracker_title and isinstance(selection_snapshot, dict):
        tracker_title = str(selection_snapshot.get("release_name") or "").strip()

    state = inspect(release)
    if not tracker_title and "torrents" not in state.unloaded:
        torrents = sorted(
            release.torrents,
            key=lambda item: (item.last_seen_at, item.id),
            reverse=True,
        )
        tracker_title = next(
            (
                str((torrent.metadata_json or {}).get("tracker_title") or "").strip()
                for torrent in torrents
                if str((torrent.metadata_json or {}).get("tracker_title") or "").strip()
            ),
            "",
        )
    if tracker_title and not release.tracker_title:
        release.tracker_title = tracker_title
    return tracker_title or release.raw_release_name


async def release_indexer_name(
    db: AsyncSession,
    release: MovieRelease | ShowRelease,
) -> str | None:
    """Recover the originating indexer so stored and upgrade scores agree."""

    selection_snapshot = getattr(release, "selection_snapshot", None)
    if isinstance(selection_snapshot, dict):
        snapshot_name = str(selection_snapshot.get("indexer_name") or "").strip()
        if snapshot_name:
            return snapshot_name

    state = inspect(release)
    if "torrents" in state.unloaded:
        return None
    for torrent in sorted(release.torrents, key=lambda item: (item.last_seen_at, item.id), reverse=True):
        metadata_name = str((torrent.metadata_json or {}).get("indexer_name") or "").strip()
        if metadata_name:
            return metadata_name

    info_hashes = [torrent.info_hash for torrent in release.torrents if torrent.info_hash]
    if not info_hashes:
        return None
    if _PROFILE_CACHE.get() is not None:
        # Scoring a whole queue: every grab's indexer, newest first, in one query.
        async def load_grabs() -> dict[str, tuple[datetime, str | None]]:
            grabs: dict[str, tuple[datetime, str | None]] = {}
            for info_hash, selected_at, indexer_name in (await db.execute(
                select(InteractiveSearchResult.submitted_info_hash, InteractiveSearchResult.selected_at, InteractiveSearchResult.indexer_name)
                .where(InteractiveSearchResult.selected_at.is_not(None), InteractiveSearchResult.submitted_info_hash.is_not(None))
                .order_by(InteractiveSearchResult.selected_at.desc())
            )).all():
                grabs.setdefault(info_hash, (selected_at, indexer_name))
            return grabs

        grabs = await _cached(("grab-indexers", id(db)), load_grabs)
        found = [grabs[value] for value in info_hashes if value in grabs]
        newest = max(found, key=lambda item: item[0], default=None)
        return str(newest[1]).strip() if newest is not None and newest[1] else None
    selected = await db.scalar(
        select(InteractiveSearchResult)
        .where(
            InteractiveSearchResult.selected_at.is_not(None),
            InteractiveSearchResult.submitted_info_hash.in_(info_hashes),
        )
        .order_by(InteractiveSearchResult.selected_at.desc())
        .limit(1)
    )
    return str(selected.indexer_name).strip() if selected is not None and selected.indexer_name else None


async def refresh_movie_release_scores(db: AsyncSession, movie_id: UUID) -> int:
    releases = (
        await db.scalars(
            select(MovieRelease)
            .options(selectinload(MovieRelease.torrents))
            .where(MovieRelease.movie_id == movie_id)
        )
    ).all()
    for release in releases:
        score, snapshot = await evaluate_current_release_score(
            db,
            media_type=MediaType.MOVIES,
            entity_id=movie_id,
            release_name=preferred_release_name(release),
            indexer=await release_indexer_name(db, release),
        )
        release.current_custom_format_score = score
        parse_snapshot = dict(release.parse_snapshot or {})
        parse_snapshot["current_score_snapshot"] = snapshot
        release.parse_snapshot = parse_snapshot
    return len(releases)


async def refresh_show_release_scores(db: AsyncSession, show_id: UUID) -> int:
    releases = (
        await db.scalars(
            select(ShowRelease)
            .options(selectinload(ShowRelease.torrents))
            .where(ShowRelease.show_id == show_id)
        )
    ).all()
    for release in releases:
        score, snapshot = await evaluate_current_release_score(
            db,
            media_type=MediaType.SHOWS,
            entity_id=show_id,
            release_name=preferred_release_name(release),
            indexer=await release_indexer_name(db, release),
        )
        release.current_custom_format_score = score
        parse_snapshot = dict(release.parse_snapshot or {})
        parse_snapshot["current_score_snapshot"] = snapshot
        release.parse_snapshot = parse_snapshot
    return len(releases)


async def refresh_all_release_scores(db: AsyncSession, *, commit_every: int | None = 20) -> dict[str, int]:
    """Re-evaluate durable current scores for every release in the library.

    Custom Format edits are infrequent in this single-user application, while
    keeping the stored current score truthful is important. Search-time
    snapshots are deliberately not touched by this operation.

    Saved every ``commit_every`` titles, along with whatever the caller had
    pending: a large library takes minutes to score, and one transaction would
    hold SQLite's only write lock throughout, failing every other save.
    """

    # Include inherited/default-profile titles too.  Those titles deliberately
    # have no MediaProfileOverride row, but their effective scoring policy still
    # changes when the default profile or any Custom Format changes.
    movie_ids = set((await db.scalars(select(MovieRelease.movie_id).distinct())).all())
    show_ids = set((await db.scalars(select(ShowRelease.show_id).distinct())).all())
    movie_releases = 0
    show_releases = 0
    pending = 0

    async def title_done() -> None:
        nonlocal pending
        pending += 1
        if commit_every is not None and pending >= commit_every:
            pending = 0
            await db.commit()

    # Every release is scored against the same profiles and formats, and the
    # caller has already flushed its own edit.
    with profiles_cached():
        for movie_id in movie_ids:
            movie_releases += await refresh_movie_release_scores(db, movie_id)
            await title_done()
        for show_id in show_ids:
            show_releases += await refresh_show_release_scores(db, show_id)
            await title_done()
    return {
        "movies": len(movie_ids),
        "shows": len(show_ids),
        "movie_releases": movie_releases,
        "show_releases": show_releases,
    }


_rescore_running = False
_rescore_again = False


async def rescore_library_after_save() -> None:
    """Bring stored scores up to date once a save has been answered.

    Run as a background task so saving a profile or Custom Format does not wait
    for the whole library. Saves made during a run are covered by one more run
    rather than one each, since a run always reads the latest settings.
    """

    global _rescore_running, _rescore_again
    _rescore_again = True
    if _rescore_running:
        return
    _rescore_running = True
    try:
        while _rescore_again:
            _rescore_again = False

            async def run() -> None:
                async with db_session.async_session_factory() as db:
                    await refresh_all_release_scores(db)
                    await db.commit()

            try:
                await with_lock_retries(run)
            except Exception:
                logger.warning("could not rescore the library after a save", exc_info=True)
            publish_live_event("release.scores_refreshed", entity_type="library")
    finally:
        _rescore_running = False
