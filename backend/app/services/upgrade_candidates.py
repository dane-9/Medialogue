from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.integrations.torznab import SearchResult as TorznabSearchResult, TorznabClient
from app.models.domain import (
    AccessMode,
    CustomFormat as CustomFormatModel,
    Episode,
    Job,
    JobStatus,
    MediaDirectory,
    MediaScope,
    MediaType,
    Movie,
    MovieRelease,
    ReleaseScope,
    ReleaseState,
    Season,
    Show,
    ShowRelease,
    UpgradeCandidate,
)
from app.parser import parse_release
from app.services.custom_formats import CustomFormat as EvaluationFormat, evaluate_custom_formats
from app.services.events import publish_live_event
from app.services.integration_state import ConfiguredIndexer, list_configured_indexers
from app.services.jobs import run_job, update_job
from app.services.quality_profiles import EffectiveProfile, evaluate_current_release_score, load_effective_profile, minimum_quality_status
from app.services.search import SearchTarget, _published_at, _query_indexer

TorznabClientFactory = Callable[..., TorznabClient]


@dataclass(frozen=True, slots=True)
class CurrentReleaseEvidence:
    release_id: UUID
    release_name: str
    quality: str
    quality_preference: int
    custom_format_score: int
    score_snapshot: dict[str, Any]
    parse_snapshot: dict[str, Any]
    release_group: str
    release_scope: ReleaseScope | None = None


@dataclass(frozen=True, slots=True)
class UpgradeTarget:
    target_key: str
    media_type: MediaType
    profile_entity_id: UUID
    movie_id: UUID | None
    season_id: UUID | None
    search_target: SearchTarget
    releases: tuple[CurrentReleaseEvidence, ...]
    reference: CurrentReleaseEvidence
    execution_eligible: bool
    execution_block_reason: str | None
    season_pack_upgrades: bool


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    indexer_id: UUID
    indexer_name: str
    indexer_priority: int
    result: TorznabSearchResult
    quality: str
    quality_preference: int
    custom_format_score: int
    parsed: Any
    revision_rank: int
    upgrade_types: tuple[str, ...]
    added_custom_formats: tuple[dict[str, Any], ...]
    snapshot: dict[str, Any]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _quality_preference(quality: str | None, quality_order: tuple[str, ...]) -> int | None:
    if not quality:
        return None
    try:
        return quality_order.index(quality)
    except ValueError:
        return None


def _matched_formats(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("breakdown") or snapshot.get("formats") or []
    return {
        str(item.get("custom_format_id")): dict(item)
        for item in rows
        if item.get("custom_format_id") and item.get("matched")
    }


def _revision_rank(parsed: Any) -> int:
    attrs = parsed.attributes
    numbered = max(
        int(getattr(attrs, "repack_revision", 0) or 0),
        int(getattr(attrs, "proper_revision", 0) or 0),
        int(getattr(attrs, "rerip_revision", 0) or 0),
    )
    return numbered + int(bool(attrs.real))


def _release_group_key(value: str | None) -> str | None:
    normalized = str(value or "").strip().casefold()
    return None if normalized in {"", "nogroup", "nogrp"} else normalized


def _same_release_group(reference: CurrentReleaseEvidence, candidate_parsed: Any) -> bool:
    current = _release_group_key(reference.release_group)
    candidate = _release_group_key(candidate_parsed.release_group)
    return current is not None and candidate is not None and current == candidate


def _revision_context_matches(target: UpgradeTarget, candidate_parsed: Any) -> bool:
    if not _same_release_group(target.reference, candidate_parsed):
        return False
    # A season pack is a new consolidation artifact, not a revision of one of
    # its component episode torrents. Revision credit only applies when the
    # current season is already represented by a pack from the same group.
    if target.media_type == MediaType.SHOWS:
        return all(item.release_scope == ReleaseScope.SEASON_PACK for item in target.releases)
    return True


def _current_revision_reasons(reference: CurrentReleaseEvidence, candidate_parsed: Any) -> list[str]:
    current = parse_release(reference.release_name)
    if not _same_release_group(reference, candidate_parsed):
        return []
    if _revision_rank(candidate_parsed) <= _revision_rank(current):
        return []
    attrs = candidate_parsed.attributes
    reasons: list[str] = []
    if attrs.repack:
        reasons.append("repack")
    if attrs.proper:
        reasons.append("proper")
    if attrs.rerip or attrs.real:
        reasons.append("revision")
    return reasons


def _revision_dependent_format(format_definition: EvaluationFormat) -> bool:
    """Whether a Custom Format's match depends on a revision marker."""

    for condition in format_definition.conditions:
        if condition.negate:
            continue
        evidence = " ".join(
            str(value or "")
            for value in (condition.name, condition.value, condition.pattern)
        ).casefold()
        if any(marker in evidence for marker in ("repack", "proper", "rerip", "real")):
            return True
    return False


def _added_or_improved_formats(
    current_snapshot: dict[str, Any],
    candidate_formats: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    current = _matched_formats(current_snapshot)
    improved: list[dict[str, Any]] = []
    for item in candidate_formats:
        if not item.get("matched"):
            continue
        key = str(item.get("custom_format_id") or "")
        if not key:
            continue
        previous = current.get(key)
        contribution = int(item.get("contribution") or 0)
        previous_contribution = int((previous or {}).get("contribution") or 0)
        if previous is not None and contribution <= previous_contribution:
            continue
        improved.append(
            {
                "custom_format_id": key,
                "custom_format_name": item.get("custom_format_name"),
                "contribution": contribution,
                "previous_contribution": previous_contribution if previous is not None else None,
                "delta": contribution - previous_contribution,
                "new": previous is None,
            }
        )
    improved.sort(key=lambda item: (-int(item["delta"]), str(item.get("custom_format_name") or "").casefold()))
    return tuple(improved)


def _season_airing_eligible(season: Season, episodes: list[Episode], *, today: date) -> tuple[bool, str | None]:
    """Require complete, known episode metadata before proposing a season pack."""

    expected = int((season.metadata_json or {}).get("episode_count") or 0)
    if season.season_number <= 0:
        return False, "Specials are not eligible for season-pack upgrades."
    if expected <= 0 or len(episodes) < expected:
        return False, "Season episode metadata is incomplete."
    monitored = [episode for episode in episodes if episode.monitored]
    if not monitored:
        return False, "The season has no monitored regular episodes."
    if any(episode.air_date is None for episode in monitored):
        return False, "At least one monitored episode has an unknown air date."
    if any(episode.air_date and episode.air_date > today for episode in monitored):
        return False, "The season is still airing."
    return True, None


def _read_only_guard(releases: list[MovieRelease | ShowRelease]) -> tuple[bool, str | None]:
    for release in releases:
        for directory in release.directories:
            root = directory.storage_root
            if root is not None and root.access_mode == AccessMode.READ_ONLY:
                return False, f"Media folder {root.name} is read-only."
    return True, None


async def _release_evidence(
    db: AsyncSession,
    *,
    media_type: MediaType,
    entity_id: UUID,
    release: MovieRelease | ShowRelease,
    quality_order: tuple[str, ...],
) -> CurrentReleaseEvidence | None:
    score, score_snapshot = await evaluate_current_release_score(
        db,
        media_type=media_type,
        entity_id=entity_id,
        release_name=release.raw_release_name,
    )
    parsed = parse_release(release.raw_release_name)
    quality = (
        release.quality_definition.name
        if release.quality_definition is not None
        else parsed.quality.canonical
    )
    preference = _quality_preference(quality, quality_order)
    if quality is None or preference is None:
        return None
    return CurrentReleaseEvidence(
        release_id=release.id,
        release_name=release.raw_release_name,
        quality=quality,
        quality_preference=preference,
        custom_format_score=int(score),
        score_snapshot=score_snapshot,
        parse_snapshot=parsed.to_dict(),
        release_group=parsed.release_group,
        release_scope=release.release_scope if isinstance(release, ShowRelease) else None,
    )


async def _movie_targets(db: AsyncSession) -> tuple[list[UpgradeTarget], int]:
    movies = (
        await db.scalars(
            select(Movie)
            .where(Movie.monitored.is_(True))
            .order_by(Movie.sort_title, Movie.id)
        )
    ).all()
    targets: list[UpgradeTarget] = []
    skipped_quality = 0
    for movie in movies:
        profile = await load_effective_profile(db, media_type=MediaType.MOVIES, entity_id=movie.id)
        releases = (
            await db.scalars(
                select(MovieRelease)
                .options(
                    selectinload(MovieRelease.quality_definition),
                    selectinload(MovieRelease.directories).selectinload(MediaDirectory.storage_root),
                )
                .where(MovieRelease.movie_id == movie.id, MovieRelease.release_state == ReleaseState.CURRENT)
                .order_by(MovieRelease.first_seen_at, MovieRelease.id)
            )
        ).all()
        for release in releases:
            evidence = await _release_evidence(
                db,
                media_type=MediaType.MOVIES,
                entity_id=movie.id,
                release=release,
                quality_order=profile.quality_order,
            )
            if evidence is None:
                skipped_quality += 1
                continue
            execution_eligible, block_reason = _read_only_guard([release])
            targets.append(
                UpgradeTarget(
                    target_key=f"movie-release:{release.id}",
                    media_type=MediaType.MOVIES,
                    profile_entity_id=movie.id,
                    movie_id=movie.id,
                    season_id=None,
                    search_target=SearchTarget(
                        media_type=MediaType.MOVIES,
                        entity_type="movie",
                        entity_id=movie.id,
                        profile_entity_id=movie.id,
                        title=movie.title,
                        year=movie.year,
                        tmdb_id=movie.tmdb_id,
                        original_language=movie.original_language,
                    ),
                    releases=(evidence,),
                    reference=evidence,
                    execution_eligible=execution_eligible,
                    execution_block_reason=block_reason,
                    season_pack_upgrades=profile.season_pack_upgrades,
                )
            )
    return targets, skipped_quality


async def _show_targets(db: AsyncSession, *, today: date) -> tuple[list[UpgradeTarget], int, int]:
    shows = (
        await db.scalars(select(Show).where(Show.monitored.is_(True)).order_by(Show.title, Show.id))
    ).all()
    targets: list[UpgradeTarget] = []
    skipped_airing = 0
    skipped_quality = 0
    for show in shows:
        profile = await load_effective_profile(db, media_type=MediaType.SHOWS, entity_id=show.id)
        seasons = (
            await db.scalars(
                select(Season)
                .where(Season.show_id == show.id, Season.monitored.is_(True))
                .order_by(Season.season_number)
            )
        ).all()
        for season in seasons:
            episodes = (
                await db.scalars(select(Episode).where(Episode.season_id == season.id).order_by(Episode.episode_number))
            ).all()
            airing_ok, _ = _season_airing_eligible(season, list(episodes), today=today)
            if not airing_ok:
                skipped_airing += 1
                continue
            releases = (
                await db.scalars(
                    select(ShowRelease)
                    .options(
                        selectinload(ShowRelease.quality_definition),
                        selectinload(ShowRelease.directories).selectinload(MediaDirectory.storage_root),
                    )
                    .where(
                        ShowRelease.show_id == show.id,
                        ShowRelease.season_id == season.id,
                        ShowRelease.release_state == ReleaseState.CURRENT,
                    )
                    .order_by(ShowRelease.first_seen_at, ShowRelease.id)
                )
            ).all()
            if not releases:
                continue
            evidence: list[CurrentReleaseEvidence] = []
            for release in releases:
                item = await _release_evidence(
                    db,
                    media_type=MediaType.SHOWS,
                    entity_id=show.id,
                    release=release,
                    quality_order=profile.quality_order,
                )
                if item is not None:
                    evidence.append(item)
            if not evidence:
                skipped_quality += 1
                continue
            # Highest quality anywhere in the season is the hard floor for a pack.
            reference = sorted(
                evidence,
                key=lambda item: (
                    item.quality_preference,
                    -item.custom_format_score,
                    -_revision_rank(parse_release(item.release_name)),
                    item.release_name.casefold(),
                ),
            )[0]
            execution_eligible, block_reason = _read_only_guard(list(releases))
            targets.append(
                UpgradeTarget(
                    target_key=f"show-season:{season.id}",
                    media_type=MediaType.SHOWS,
                    profile_entity_id=show.id,
                    movie_id=None,
                    season_id=season.id,
                    search_target=SearchTarget(
                        media_type=MediaType.SHOWS,
                        entity_type="season",
                        entity_id=season.id,
                        profile_entity_id=show.id,
                        title=show.title,
                        year=show.year,
                        tmdb_id=show.tmdb_id,
                        season=season.season_number,
                        original_language=show.original_language,
                    ),
                    releases=tuple(evidence),
                    reference=reference,
                    execution_eligible=execution_eligible,
                    execution_block_reason=block_reason,
                    season_pack_upgrades=profile.season_pack_upgrades,
                )
            )
    return targets, skipped_airing, skipped_quality


async def _evaluation_policy(
    db: AsyncSession,
    *,
    media_type: MediaType,
    entity_id: UUID,
) -> tuple[EffectiveProfile, list[EvaluationFormat]]:
    profile = await load_effective_profile(db, media_type=media_type, entity_id=entity_id)
    eligible = (
        (MediaScope.MOVIES, MediaScope.BOTH)
        if media_type == MediaType.MOVIES
        else (MediaScope.SHOWS, MediaScope.BOTH)
    )
    rows = (
        await db.scalars(
            select(CustomFormatModel)
            .where(CustomFormatModel.enabled.is_(True), CustomFormatModel.media_scope.in_(eligible))
            .order_by(CustomFormatModel.name)
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
    return profile, formats


def _score_candidate(
    *,
    target: UpgradeTarget,
    profile: EffectiveProfile,
    formats: list[EvaluationFormat],
    indexer: ConfiguredIndexer,
    result: TorznabSearchResult,
) -> ScoredCandidate | None:
    if not result.title.strip() or not result.download_url:
        return None
    parsed = parse_release(result.title)
    quality = parsed.quality.canonical
    preference = _quality_preference(quality, profile.quality_order)
    if quality is None or preference is None:
        return None
    # A season pack may never replace the highest-quality episode with a lower
    # quality release, regardless of Custom Format score.
    if preference > target.reference.quality_preference:
        return None

    evaluation = evaluate_custom_formats(
        formats,
        parsed,
        profile_scores=profile.profile_scores,
        score_overrides=profile.overrides,
        context={"indexer": indexer.name},
    )
    same_release_group = _same_release_group(target.reference, parsed)
    revision_context_matches = _revision_context_matches(target, parsed)
    formats_by_id = {str(item.id): item for item in formats}
    format_snapshots: list[dict[str, Any]] = []
    for item in evaluation.formats:
        key = str(item.custom_format_id)
        configured = int(profile.profile_scores.get(key, 0))
        override = profile.overrides.get(key)
        evidence = item.to_dict()
        evidence["profile_score"] = configured
        evidence["override_score"] = int(override) if override is not None else None
        evidence["effective_score"] = int(override if override is not None else configured)
        evidence["score_offset"] = int(item.score_offset)
        suppressed_revision = (
            item.matched
            and not revision_context_matches
            and (definition := formats_by_id.get(key)) is not None
            and _revision_dependent_format(definition)
        )
        evidence["contribution"] = 0 if suppressed_revision else int(item.score_contribution)
        evidence["suppressed_for_upgrade"] = bool(suppressed_revision)
        evidence["suppression_reason"] = (
            "Revision markers only receive upgrade credit for the same release group and release scope."
            if suppressed_revision
            else None
        )
        evidence.pop("score", None)
        evidence.pop("configured_score", None)
        format_snapshots.append(evidence)

    score = sum(int(item.get("contribution") or 0) for item in format_snapshots)
    reasons: list[str] = []
    if preference < target.reference.quality_preference:
        reasons.append("quality")
    if score > target.reference.custom_format_score:
        reasons.append("custom_format")
    if (
        target.media_type == MediaType.SHOWS
        and target.season_pack_upgrades
        and any(release.release_scope != ReleaseScope.SEASON_PACK for release in target.releases)
    ):
        reasons.append("season_pack")

    revision_reasons = (
        _current_revision_reasons(target.reference, parsed)
        if revision_context_matches
        else []
    )
    # A revision by itself must not sacrifice Custom Format score. Quality and
    # season-pack reasons have their own explicit policy and may legitimately do so.
    if score >= target.reference.custom_format_score or "quality" in reasons or "season_pack" in reasons:
        reasons.extend(revision_reasons)
    reasons = list(dict.fromkeys(reasons))
    if not reasons:
        return None

    added_formats = _added_or_improved_formats(target.reference.score_snapshot, format_snapshots)
    floor_status = minimum_quality_status(quality, profile.minimum_quality_name, profile.quality_order)
    minimum_score_met = (
        score >= profile.minimum_custom_format_score
        if profile.minimum_custom_format_score is not None
        else None
    )
    snapshot = {
        "schema_version": 1,
        "profile": profile.snapshot(),
        "current": {
            "release_ids": [str(item.release_id) for item in target.releases],
            "reference_release_id": str(target.reference.release_id),
            "release_name": target.reference.release_name,
            "release_group": target.reference.release_group,
            "quality": target.reference.quality,
            "quality_preference": target.reference.quality_preference,
            "custom_format_score": target.reference.custom_format_score,
            "score_snapshot": target.reference.score_snapshot,
        },
        "candidate": {
            "quality": quality,
            "quality_preference": preference,
            "custom_format_score": score,
            "minimum_quality_met": floor_status,
            "minimum_custom_format_score_met": minimum_score_met,
            "parse": parsed.to_dict(),
            "release_group": parsed.release_group,
            "same_release_group": same_release_group,
            "revision_context_eligible": revision_context_matches,
            "formats": format_snapshots,
        },
        "upgrade_types": reasons,
        "execution_eligible": target.execution_eligible,
        "execution_block_reason": target.execution_block_reason,
    }
    return ScoredCandidate(
        indexer_id=indexer.id,
        indexer_name=indexer.name,
        indexer_priority=int(indexer.priority),
        result=result,
        quality=quality,
        quality_preference=preference,
        custom_format_score=score,
        parsed=parsed,
        revision_rank=_revision_rank(parsed) if revision_context_matches else 0,
        upgrade_types=tuple(reasons),
        added_custom_formats=added_formats,
        snapshot=snapshot,
    )


def _candidate_sort_key(candidate: ScoredCandidate) -> tuple[Any, ...]:
    published = _published_at(candidate.result.published)
    published_rank = -(published.timestamp() if published else 0.0)
    return (
        candidate.quality_preference,
        -candidate.custom_format_score,
        -candidate.revision_rank,
        -(candidate.result.seeders if candidate.result.seeders is not None else -1),
        published_rank,
        candidate.indexer_priority,
        candidate.result.title.casefold(),
        (candidate.result.guid or candidate.result.download_url or candidate.result.title).casefold(),
    )


async def _search_target(
    db: AsyncSession,
    *,
    target: UpgradeTarget,
    indexers: list[ConfiguredIndexer],
    client_factory: TorznabClientFactory,
) -> tuple[ScoredCandidate | None, int, bool]:
    profile, formats = await _evaluation_policy(db, media_type=target.media_type, entity_id=target.profile_entity_id)
    eligible_scopes = (
        {MediaScope.MOVIES, MediaScope.BOTH}
        if target.media_type == MediaType.MOVIES
        else {MediaScope.SHOWS, MediaScope.BOTH}
    )
    scoped = [
        indexer
        for indexer in indexers
        if indexer.enabled and indexer.enable_interactive_search and indexer.scope in eligible_scopes
    ]
    if not scoped:
        return None, 0, False
    outcomes = await asyncio.gather(
        *[_query_indexer(indexer, target.search_target, client_factory=client_factory) for indexer in scoped]
    )
    candidates: list[ScoredCandidate] = []
    failures = 0
    successful_indexers = 0
    by_id = {indexer.id: indexer for indexer in scoped}
    for indexer_id, outcome in outcomes:
        if outcome.get("status") != "completed":
            failures += 1
            continue
        successful_indexers += 1
        indexer = by_id[indexer_id]
        for result in outcome.get("results") or []:
            scored = _score_candidate(target=target, profile=profile, formats=formats, indexer=indexer, result=result)
            if scored is not None:
                candidates.append(scored)
    if not candidates:
        return None, failures, successful_indexers > 0
    candidates.sort(key=_candidate_sort_key)
    return candidates[0], failures, True


async def _persist_candidate(
    db: AsyncSession,
    *,
    target: UpgradeTarget,
    candidate: ScoredCandidate | None,
    searched_at: datetime,
) -> tuple[bool, bool]:
    row = await db.scalar(select(UpgradeCandidate).where(UpgradeCandidate.target_key == target.target_key))
    if row is not None and row.execution_state in {"submitted", "downloading", "validating", "handoff_attention", "cleanup_required"}:
        # A search may run while an explicitly selected upgrade is still in
        # flight. Never replace or remove the candidate snapshot that anchors
        # that destructive workflow.
        row.last_searched_at = searched_at
        return True, False
    if candidate is None:
        if row is None:
            return False, False
        await db.delete(row)
        return False, True
    guid = (candidate.result.guid or candidate.result.download_url or candidate.result.title).strip()
    if row is None:
        row = UpgradeCandidate(
            target_key=target.target_key,
            media_type=target.media_type,
            movie_id=target.movie_id,
            season_id=target.season_id,
            current_release_ids=[],
            current_release_name=target.reference.release_name,
            indexer_name=candidate.indexer_name,
            guid=guid,
            title=candidate.result.title,
        )
        db.add(row)
    row.media_type = target.media_type
    row.movie_id = target.movie_id
    row.season_id = target.season_id
    row.current_release_ids = [str(item.release_id) for item in target.releases]
    row.current_release_name = target.reference.release_name
    row.current_quality = target.reference.quality
    row.current_custom_format_score = target.reference.custom_format_score
    row.indexer_id = candidate.indexer_id
    row.indexer_name = candidate.indexer_name
    row.guid = guid
    row.download_url = candidate.result.download_url
    row.title = candidate.result.title
    row.quality = candidate.quality
    row.custom_format_score = candidate.custom_format_score
    row.score_delta = candidate.custom_format_score - target.reference.custom_format_score
    row.size = candidate.result.size
    row.seeders = candidate.result.seeders
    row.leechers = candidate.result.leechers
    row.published_at = _published_at(candidate.result.published)
    row.upgrade_types = list(candidate.upgrade_types)
    row.added_custom_formats = [dict(item) for item in candidate.added_custom_formats]
    row.execution_eligible = target.execution_eligible
    row.execution_block_reason = target.execution_block_reason
    row.comparison_snapshot = candidate.snapshot
    row.last_searched_at = searched_at
    await db.flush()
    publish_live_event(
        "upgrade.candidate",
        entity_type="upgrade_candidate",
        entity_id=row.id,
        data={
            "candidate_id": str(row.id),
            "media_type": row.media_type.value,
            "movie_id": str(row.movie_id) if row.movie_id else None,
            "season_id": str(row.season_id) if row.season_id else None,
            "upgrade_types": list(row.upgrade_types or []),
            "execution_eligible": bool(row.execution_eligible),
        },
    )
    return True, False


async def _worker(
    db: AsyncSession,
    job: Job,
    *,
    client_factory: TorznabClientFactory,
    today: date,
) -> None:
    await update_job(db, job, status=JobStatus.RUNNING, progress={"completed": 0, "total": 0, "percent": 0})
    await db.commit()

    movie_targets, movie_skipped_quality = await _movie_targets(db)
    show_targets, skipped_airing, show_skipped_quality = await _show_targets(db, today=today)
    targets = [*movie_targets, *show_targets]
    valid_keys = {target.target_key for target in targets}
    inactive = (
        UpgradeCandidate.execution_state.is_(None)
        | UpgradeCandidate.execution_state.in_(["completed", "failed"])
    )
    if valid_keys:
        await db.execute(
            delete(UpgradeCandidate).where(UpgradeCandidate.target_key.not_in(valid_keys), inactive)
        )
    else:
        await db.execute(delete(UpgradeCandidate).where(inactive))
    await db.commit()

    indexers = await list_configured_indexers(db)
    found = removed = failures = searched = 0
    total = len(targets)
    for position, target in enumerate(targets, start=1):
        best, target_failures, completed_search = await _search_target(
            db,
            target=target,
            indexers=indexers,
            client_factory=client_factory,
        )
        failures += target_failures
        if completed_search:
            searched += 1
            has_candidate, removed_candidate = await _persist_candidate(
                db,
                target=target,
                candidate=best,
                searched_at=utcnow(),
            )
            found += int(has_candidate)
            removed += int(removed_candidate)
        summary = {
            "targets_total": total,
            "targets_searched": searched,
            "candidates_found": found,
            "candidates_removed": removed,
            "indexer_failures": failures,
            "skipped_airing_or_incomplete": skipped_airing,
            "skipped_unknown_current_quality": movie_skipped_quality + show_skipped_quality,
            "automatic_grabs": False,
        }
        await update_job(
            db,
            job,
            progress={
                "completed": position,
                "total": total,
                "percent": round((position / total) * 100, 1) if total else 100,
            },
            summary=summary,
        )
        await db.commit()

    summary = {
        "targets_total": total,
        "targets_searched": searched,
        "candidates_found": found,
        "candidates_removed": removed,
        "indexer_failures": failures,
        "skipped_airing_or_incomplete": skipped_airing,
        "skipped_unknown_current_quality": movie_skipped_quality + show_skipped_quality,
        "automatic_grabs": False,
    }
    await update_job(
        db,
        job,
        status=JobStatus.COMPLETED,
        progress={"completed": total, "total": total, "percent": 100},
        summary=summary,
    )


async def run_upgrade_search_job(
    job_id: UUID,
    *,
    client_factory: TorznabClientFactory = TorznabClient,
    today: date | None = None,
) -> None:
    """Evaluate the library and retain only each target's best known upgrade."""

    run_date = today or utcnow().date()

    async def worker(db: AsyncSession, job: Job) -> None:
        await _worker(db, job, client_factory=client_factory, today=run_date)

    await run_job(
        job_id,
        worker,
        failure_code="UPGRADE_SEARCH_FAILED",
        failure_message="Upgrade candidate search failed.",
        failure_progress={"completed": 0, "total": 0, "percent": 0},
    )


__all__ = [
    "ScoredCandidate",
    "UpgradeTarget",
    "_candidate_sort_key",
    "_score_candidate",
    "_season_airing_eligible",
    "run_upgrade_search_job",
]
