from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.integrations.torznab import SearchResult as TorznabSearchResult, TorznabClient
from app.models.domain import (
    AccessMode,
    CustomFormat as CustomFormatModel,
    Episode,
    EpisodeMediaMap,
    Job,
    JobStatus,
    MediaDirectory,
    MediaFile,
    MediaScope,
    MediaType,
    Movie,
    MovieRelease,
    QualityDefinition,
    ReleaseScope,
    ReleaseState,
    Season,
    Show,
    ShowRelease,
    UpgradeCandidate,
    UpgradeSearchMark,
)
from app.core.identity import identity_titles_equivalent
from app.parser import parse_release
from app.services.builtin_formats import BANNED_RELEASE_GROUP, BANNED_RELEASE_GROUPS_FORMAT, RELEASE_GROUP_FORMAT
from app.services.custom_formats import CustomFormat as EvaluationFormat, evaluate_custom_formats
from app.services.events import publish_live_event
from app.services.integration_state import ConfiguredIndexer, list_configured_indexers
from app.services.jobs import JobFailure, run_job, update_job
from app.services.movie_editions import same_edition
from app.services.quality_profiles import (
    EffectiveProfile,
    evaluate_current_release_score,
    load_effective_profile,
    minimum_quality_status,
    preferred_release_name,
    release_indexer_name,
)
from app.services.search import SearchTarget, _published_at, _query_indexer

TorznabClientFactory = Callable[..., TorznabClient]


@dataclass(frozen=True, slots=True)
class CurrentReleaseEvidence:
    release_id: UUID
    release_name: str
    quality: str
    quality_preference: float
    custom_format_score: int
    score_snapshot: dict[str, Any]
    parse_snapshot: dict[str, Any]
    release_group: str
    edition: str | None = None
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
    replacement: bool = False
    excluded_hashes: tuple[str, ...] = ()


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


#: The smallest Custom Format gain that makes an upgrade on its own; less is
#: not worth a whole new download.
MINIMUM_FORMAT_GAIN = 15
#: Groups ranked in tiers: an upgrade may not make any of them worse.
TIERED_GROUPS = frozenset({"Audio", "Dynamic Range"})
#: Groups a release name often leaves out (a bare folder name says nothing
#: about audio): with no tag on the current release, the group is unknown
#: rather than worst, so the candidate gets no credit there.
UNKNOWN_WHEN_UNTAGGED = frozenset({"Audio"})
#: Each Custom Format's group, filled when a search loads its formats.
_FORMAT_GROUPS: dict[str, str | None] = {}


def _release_group_points(rows: Any) -> int:
    """Points from release group tiers, which only choose between results."""

    return sum(
        int(item.get("contribution") or 0) for item in rows
        if item.get("matched", True) and _FORMAT_GROUPS.get(str(item.get("custom_format_id") or "")) == RELEASE_GROUP_FORMAT
    )


def _format_gain(current_snapshot: dict[str, Any], candidate_formats: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """The Custom Format gain that counts toward an upgrade, and the tiered
    groups the candidate makes worse."""

    def by_group(rows) -> dict[str | None, int]:
        sums: dict[str | None, int] = {}
        for item in rows:
            group = _FORMAT_GROUPS.get(str(item.get("custom_format_id") or ""))
            sums[group] = sums.get(group, 0) + int(item.get("contribution") or 0)
        return sums

    current = by_group(_matched_formats(current_snapshot).values())
    candidate = by_group(item for item in candidate_formats if item.get("matched"))
    gain = 0
    worse: list[str] = []
    for group in set(current) | set(candidate):
        if group == RELEASE_GROUP_FORMAT:
            continue
        before, after = current.get(group, 0), candidate.get(group, 0)
        if group in UNKNOWN_WHEN_UNTAGGED and group not in current:
            continue
        if group in TIERED_GROUPS and after < before:
            worse.append(str(group))
        gain += after - before
    return gain, sorted(worse)


def _matched_formats(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("breakdown") or []
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
    return numbered


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
    if attrs.rerip:
        reasons.append("revision")
    return reasons


def _is_revision_condition_match(condition: Any) -> bool:
    if condition.negated or not condition.matched:
        return False
    if condition.condition_type not in {"release_title", "release_attribute"}:
        return False
    matched = str(condition.regex_match or condition.evidence or "").strip().casefold()
    if not matched:
        return False
    return any(
        matched == marker or (matched.startswith(marker) and matched[len(marker):].isdigit())
        for marker in ("repack", "proper", "rerip")
    )


def _revision_dependent_match(evaluation: Any) -> bool:
    """Whether this successful CF match logically depends on a revision marker.

    Looking at arbitrary condition names/pattern text produced false positives
    (notably any unrelated word containing ``real``).  Suppression is an
    upgrade-comparison rule, so it is based on the candidate's actual matched
    evidence and on whether removing that revision evidence would make one of
    the format's condition groups fail.
    """

    groups: dict[str, list[Any]] = {}
    for condition in evaluation.conditions:
        groups.setdefault(condition.group or condition.condition_type, []).append(condition)
    for conditions in groups.values():
        required = [condition for condition in conditions if condition.required]
        if any(condition.effective_result and _is_revision_condition_match(condition) for condition in required):
            return True
        optional = [condition for condition in conditions if not condition.required]
        passing_optional = [condition for condition in optional if condition.effective_result]
        if passing_optional and all(_is_revision_condition_match(condition) for condition in passing_optional):
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


async def invalidate_idle_upgrade_candidates(
    db: AsyncSession,
    *,
    reason: str = "Scoring policy changed; search for upgrades again.",
) -> int:
    """Fail closed for cached recommendations after upgrade policy changes.

    Upgrade candidates are search-time snapshots. Quality Profile assignments,
    profile scoring, and Custom Format definitions can all change what qualifies
    as an upgrade, so an idle snapshot must not remain executable afterwards.
    Active destructive workflows are deliberately left untouched.
    """

    # Just the two columns: the rest of a row includes every stored search result.
    rows = (
        await db.scalars(
            select(UpgradeCandidate)
            .options(load_only(UpgradeCandidate.execution_eligible, UpgradeCandidate.execution_block_reason))
            .where(UpgradeCandidate.execution_state.is_(None))
        )
    ).all()
    for row in rows:
        row.execution_eligible = False
        row.execution_block_reason = reason
    await db.flush()
    return len(rows)


async def remove_unstarted_upgrades_from(db: AsyncSession, indexer_id: UUID) -> int:
    """Forget what a tracker offered, once it stops being used for upgrades.

    Started upgrades finish, and Replace Media keeps its own. A title losing
    its row is marked unsearched, so another tracker gets the first chance at
    it. Returns how many rows were removed.
    """

    rows = (await db.scalars(select(UpgradeCandidate).where(
        ~UpgradeCandidate.target_key.startswith("replace:"),
        or_(UpgradeCandidate.execution_state.is_(None), UpgradeCandidate.execution_state == "failed"),
    ))).all()
    removed = 0
    for row in rows:
        if row.indexer_id == indexer_id:
            await db.execute(delete(UpgradeSearchMark).where(UpgradeSearchMark.target_key == row.target_key))
            await db.delete(row)
            removed += 1
            continue
        kept = [entry for entry in row.search_results or [] if entry.get("indexer_id") != str(indexer_id)]
        if len(kept) != len(row.search_results or []):
            row.search_results = kept
    await db.flush()
    return removed


def _read_only_root(releases: list[MovieRelease | ShowRelease]) -> str | None:
    """The name of the read-only media folder holding these releases, if any.

    A read-only folder cannot receive a replacement, so there is nothing to
    decide about such a release and it is not searched at all. Returning the
    name lets the page say which folder was skipped instead of listing every
    release inside it.
    """

    for release in releases:
        directories = list(release.directories)
        if isinstance(release, ShowRelease):
            directories.extend(
                episode_map.media_file.media_directory
                for episode_map in release.episode_maps
                if episode_map.media_file is not None and episode_map.media_file.media_directory is not None
            )
        for directory in directories:
            root = directory.storage_root
            if root is not None and root.access_mode == AccessMode.READ_ONLY:
                return root.name
    return None


async def _release_evidence(
    db: AsyncSession,
    *,
    media_type: MediaType,
    entity_id: UUID,
    release: MovieRelease | ShowRelease,
    quality_order: tuple[str, ...],
) -> CurrentReleaseEvidence | None:
    release_name = preferred_release_name(release)
    score, score_snapshot = await evaluate_current_release_score(
        db,
        media_type=media_type,
        entity_id=entity_id,
        release_name=release_name,
        indexer=await release_indexer_name(db, release),
    )
    parsed = parse_release(release_name)
    quality = (
        release.quality_definition.name
        if release.quality_definition is not None
        else parsed.quality.canonical
    )
    preference = _quality_preference(quality, quality_order)
    if quality is not None and quality == parsed.quality.canonical and preference is None:
        # A profile limits replacement qualities, not the inventory we search
        # upgrades for. Place an excluded current quality just above the first
        # allowed quality of equal/lower global rank to prevent downgrades.
        definitions = (await db.scalars(select(QualityDefinition))).all()
        ranks = {item.name: item.rank for item in definitions}
        if quality in ranks:
            preference = next(
                (index - 0.5 for index, name in enumerate(quality_order)
                 if name not in ranks or ranks[name] <= ranks[quality]),
                float(len(quality_order)),
            )
    if quality is None or preference is None:
        return None
    return CurrentReleaseEvidence(
        release_id=release.id,
        release_name=release_name,
        quality=quality,
        quality_preference=preference,
        custom_format_score=int(score),
        score_snapshot=score_snapshot,
        parse_snapshot=parsed.to_dict(),
        release_group=parsed.release_group,
        edition=release.effective_edition if isinstance(release, MovieRelease) else None,
        release_scope=release.release_scope if isinstance(release, ShowRelease) else None,
    )


async def _movie_targets(
    db: AsyncSession,
    *,
    replacement: bool = False,
    movie_ids: set[UUID] | None = None,
) -> tuple[list[UpgradeTarget], int, set[str]]:
    """``movie_ids`` limits the scan (Replace Media only needs affected titles)."""

    if movie_ids is not None and not movie_ids:
        return [], 0, set()
    statement = select(Movie).where(Movie.monitored.is_(True) if not replacement else True)
    if movie_ids is not None:
        statement = statement.where(Movie.id.in_(movie_ids))
    movies = (await db.scalars(statement.order_by(Movie.sort_title, Movie.id))).all()
    targets: list[UpgradeTarget] = []
    skipped_quality = 0
    skipped_read_only: set[str] = set()
    # Every movie's releases in a few queries rather than a handful per movie.
    releases_by_movie: dict[UUID, list[MovieRelease]] = {}
    movie_ids_in_order = [movie.id for movie in movies]
    for index in range(0, len(movie_ids_in_order), 500):
        for release in (
            await db.scalars(
                select(MovieRelease)
                .options(
                    selectinload(MovieRelease.quality_definition),
                    selectinload(MovieRelease.directories).selectinload(MediaDirectory.storage_root),
                    selectinload(MovieRelease.torrents),
                )
                .where(
                    MovieRelease.movie_id.in_(movie_ids_in_order[index : index + 500]),
                    MovieRelease.release_state.in_([ReleaseState.CURRENT, ReleaseState.MISSING] if replacement else [ReleaseState.CURRENT]),
                )
                .order_by(MovieRelease.first_seen_at, MovieRelease.id)
            )
        ).all():
            releases_by_movie.setdefault(release.movie_id, []).append(release)
    for movie in movies:
        profile = await load_effective_profile(db, media_type=MediaType.MOVIES, entity_id=movie.id)
        for release in releases_by_movie.get(movie.id, []):
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
            read_only = _read_only_root([release])
            if read_only is not None and not replacement:
                skipped_read_only.add(read_only)
                continue
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
                    execution_eligible=True,
                    execution_block_reason=None,
                    season_pack_upgrades=profile.season_pack_upgrades,
                )
            )
    return targets, skipped_quality, skipped_read_only


async def _show_targets(
    db: AsyncSession,
    *,
    today: date,
    replacement: bool = False,
    show_ids: set[UUID] | None = None,
) -> tuple[list[UpgradeTarget], int, int, set[str]]:
    if show_ids is not None and not show_ids:
        return [], 0, 0, set()
    statement = select(Show).where(Show.monitored.is_(True) if not replacement else True)
    if show_ids is not None:
        statement = statement.where(Show.id.in_(show_ids))
    shows = (await db.scalars(statement.order_by(Show.title, Show.id))).all()
    targets: list[UpgradeTarget] = []
    skipped_airing = 0
    skipped_quality = 0
    skipped_read_only: set[str] = set()
    for show in shows:
        profile = await load_effective_profile(db, media_type=MediaType.SHOWS, entity_id=show.id)
        seasons = (
            await db.scalars(
                select(Season)
                .where(Season.show_id == show.id, Season.monitored.is_(True) if not replacement else True)
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
                        selectinload(ShowRelease.episode_maps)
                        .selectinload(EpisodeMediaMap.media_file)
                        .selectinload(MediaFile.media_directory)
                        .selectinload(MediaDirectory.storage_root),
                        selectinload(ShowRelease.torrents),
                    )
                    .where(
                        ShowRelease.show_id == show.id,
                        ShowRelease.season_id == season.id,
                        ShowRelease.release_state.in_([ReleaseState.CURRENT, ReleaseState.MISSING] if replacement else [ReleaseState.CURRENT]),
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
            # A season-pack replacement is destructive across the whole season.
            # If even one current release cannot be scored under the active
            # profile, do not silently omit it from the replacement set.
            if len(evidence) != len(releases):
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
            read_only = _read_only_root(list(releases))
            if read_only is not None and not replacement:
                skipped_read_only.add(read_only)
                continue
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
                    execution_eligible=True,
                    execution_block_reason=None,
                    season_pack_upgrades=profile.season_pack_upgrades,
                )
            )
    return targets, skipped_airing, skipped_quality, skipped_read_only


async def _evaluation_policy(
    db: AsyncSession,
    *,
    media_type: MediaType,
    entity_id: UUID,
) -> tuple[EffectiveProfile, list[EvaluationFormat]]:
    profile = await load_effective_profile(db, media_type=media_type, entity_id=entity_id)
    rows = (
        await db.scalars(
            select(CustomFormatModel)
            .where(CustomFormatModel.enabled.is_(True))
            .order_by(CustomFormatModel.name)
        )
    ).all()
    from app.services.builtin_formats import format_group

    for row in rows:
        _FORMAT_GROUPS[str(row.id)] = format_group(row.builtin_key, row.condition_definition)
    formats = [
        EvaluationFormat.from_dict(
            {
                "id": str(row.id),
                "name": row.name,
                "description": row.description,
                "enabled": row.enabled,
                "condition_definition": dict(row.condition_definition or {}),
            }
        )
        for row in rows
    ]
    return profile, formats


def _is_the_searched_title(parsed: Any, result: TorznabSearchResult, target: SearchTarget) -> bool:
    """Whether a search result is really the title that was searched for.

    A text search is only as good as the tracker's matching: "Ray 2004" finds
    "50 First Dates 2004 ... Blu-ray". So a result counts only when its own
    TMDB id or its parsed title says so. A year that disagrees rules it out
    either way, since indexers do attach wrong ids.
    """

    identity = parsed.identity
    if target.media_type == MediaType.MOVIES and target.year and identity.year and identity.year != target.year:
        return False
    if result.tmdb_id is not None and result.tmdb_id == target.tmdb_id:
        return True
    return any(identity_titles_equivalent(title, target.title) for title in (identity.title_candidate, *identity.alternate_titles))


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
    if not target.replacement and not _is_the_searched_title(parsed, result, target.search_target):
        return None
    if target.replacement:
        if not identity_titles_equivalent(parsed.identity.title_candidate, target.search_target.title):
            return None
        if target.media_type == MediaType.MOVIES and target.search_target.year and parsed.identity.year != target.search_target.year:
            return None
        from app.integrations.qbittorrent import magnet_info_hash
        result_hash = magnet_info_hash(result.download_url) if result.download_url.lower().startswith("magnet:") else None
        if result_hash and result_hash.lower() in target.excluded_hashes:
            return None
        if result.seeders is not None and result.seeders < max(1, indexer.minimum_seeders):
            return None
    if target.media_type == MediaType.MOVIES and not same_edition(target.reference.edition, parsed.edition):
        return None
    if target.media_type == MediaType.SHOWS:
        seasons = tuple(parsed.identity.seasons)
        if parsed.identity.episodes or len(seasons) != 1 or seasons[0] != target.search_target.season:
            return None
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
    if any(item.matched and item.custom_format_name == BANNED_RELEASE_GROUPS_FORMAT for item in evaluation.formats):
        return None
    same_release_group = _same_release_group(target.reference, parsed)
    revision_context_matches = _revision_context_matches(target, parsed)
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
            and _revision_dependent_match(item)
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
    # Better Custom Formats alone must gain enough, without trading away audio
    # or dynamic range to get there.
    format_gain, worse_groups = _format_gain(target.reference.score_snapshot, format_snapshots)
    if format_gain >= MINIMUM_FORMAT_GAIN and not worse_groups:
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
    if target.replacement:
        # A lost source is replaced by the same quality from any group.
        without_groups = score - _release_group_points(format_snapshots)
        if without_groups < max(item.custom_format_score - _release_group_points(_matched_formats(item.score_snapshot).values()) for item in target.releases):
            return None
        if profile.minimum_custom_format_score is not None and score < profile.minimum_custom_format_score:
            return None
        if minimum_quality_status(quality, profile.minimum_quality_name, profile.quality_order) is False:
            return None
        reasons.append("replacement")
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
            "edition": target.reference.edition,
            "quality": target.reference.quality,
            "quality_preference": target.reference.quality_preference,
            "custom_format_score": target.reference.custom_format_score,
            "score_snapshot": target.reference.score_snapshot,
            "releases": [
                {
                    "release_id": str(item.release_id),
                    "release_name": item.release_name,
                    "quality": item.quality,
                    "edition": item.edition,
                    "custom_format_score": item.custom_format_score,
                    "release_scope": item.release_scope.value if item.release_scope is not None else None,
                }
                for item in target.releases
            ],
        },
        "candidate": {
            "quality": quality,
            "quality_preference": preference,
            "custom_format_score": score,
            "minimum_quality_met": floor_status,
            "minimum_custom_format_score_met": minimum_score_met,
            "parse": parsed.to_dict(),
            "edition": parsed.edition,
            "release_group": parsed.release_group,
            "same_release_group": same_release_group,
            "revision_context_eligible": revision_context_matches,
            "formats": format_snapshots,
        },
        "upgrade_types": reasons,
        "replacement": target.replacement,
        "excluded_hashes": list(target.excluded_hashes),
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


def _rejection_reasons(
    target: UpgradeTarget,
    profile: EffectiveProfile,
    result: TorznabSearchResult,
) -> list[str]:
    if not result.title.strip():
        return ["The indexer returned an empty release title."]
    if not result.download_url:
        return ["The indexer did not provide a downloadable torrent."]
    if BANNED_RELEASE_GROUP.search(result.title):
        return ["This release group is banned."]
    parsed = parse_release(result.title)
    if not target.replacement and not _is_the_searched_title(parsed, result, target.search_target):
        return [f"A different title: {parsed.identity.title_candidate or 'unknown'}."]
    if target.replacement:
        from app.integrations.qbittorrent import magnet_info_hash
        result_hash = magnet_info_hash(result.download_url) if result.download_url.lower().startswith("magnet:") else None
        if result_hash and result_hash.lower() in target.excluded_hashes:
            return ["This is an existing torrent, not a new recovery source."]
        if result.seeders is not None and result.seeders < 1:
            return ["No seeders were reported for this replacement."]
    if target.media_type == MediaType.MOVIES and not same_edition(target.reference.edition, parsed.edition):
        return [f"Different movie edition: {parsed.edition or 'Theatrical Cut' }."]
    if target.media_type == MediaType.SHOWS:
        seasons = tuple(parsed.identity.seasons)
        if parsed.identity.episodes:
            return ["Individual episode releases are not eligible; Medialogue requires a season pack."]
        if len(seasons) != 1 or seasons[0] != target.search_target.season:
            return ["The release is not a pack for exactly this season."]
    quality = parsed.quality.canonical
    preference = _quality_preference(quality, profile.quality_order)
    if quality is None or preference is None:
        return ["The release quality is not recognized by this Quality Profile."]
    if preference > target.reference.quality_preference:
        return ["The release is lower quality than the current upgrade floor."]
    return ["The release does not meet the replacement identity, quality, or equal-or-higher CF score requirements."] if target.replacement else ["The release does not improve quality, Custom Format score, or revision status."]


def _stored_search_result(
    *,
    target: UpgradeTarget,
    indexer: ConfiguredIndexer,
    result: TorznabSearchResult,
    scored: ScoredCandidate | None,
    profile: EffectiveProfile,
) -> dict[str, Any]:
    parsed = scored.parsed if scored is not None else parse_release(result.title)
    guid = (result.guid or result.download_url or result.title).strip()
    result_id = uuid.uuid5(uuid.NAMESPACE_URL, f"medialogue:{target.target_key}:{indexer.id}:{guid}")
    published = _published_at(result.published)
    return {
        "id": str(result_id),
        "indexer_id": str(indexer.id),
        "indexer_name": indexer.name,
        "indexer_priority": int(indexer.priority),
        "guid": guid,
        "download_url": result.download_url,
        "title": result.title,
        "quality": scored.quality if scored is not None else parsed.quality.canonical,
        "edition": parsed.edition,
        "release_group": parsed.release_group,
        "custom_format_score": scored.custom_format_score if scored is not None else None,
        "size": result.size,
        "seeders": result.seeders,
        "leechers": result.leechers,
        "published_at": published.isoformat() if published else None,
        "eligible": scored is not None,
        "rejection_reasons": [] if scored is not None else _rejection_reasons(target, profile, result),
        "upgrade_types": list(scored.upgrade_types) if scored is not None else [],
        "added_custom_formats": [dict(item) for item in scored.added_custom_formats] if scored is not None else [],
        "snapshot": scored.snapshot if scored is not None else {},
        "parser": parsed.to_dict(),
        "revision_rank": scored.revision_rank if scored is not None else 0,
    }


async def _search_target(
    db: AsyncSession,
    *,
    target: UpgradeTarget,
    indexers: list[ConfiguredIndexer],
    client_factory: TorznabClientFactory,
    errors: list[str] | None = None,
) -> tuple[ScoredCandidate | None, list[dict[str, Any]], int, bool]:
    eligible_scopes = (
        {MediaScope.MOVIES, MediaScope.BOTH}
        if target.media_type == MediaType.MOVIES
        else {MediaScope.SHOWS, MediaScope.BOTH}
    )
    scoped = [
        indexer
        for indexer in indexers
        # Replace Media keeps every tracker: a lost source needs the widest search.
        if indexer.enabled and indexer.enable_interactive_search and indexer.scope in eligible_scopes
        and (target.replacement or indexer.use_for_upgrades)
    ]
    if not scoped:
        return None, [], 0, False
    outcomes = await asyncio.gather(
        *[_query_indexer(indexer, target.search_target, client_factory=client_factory) for indexer in scoped]
    )
    failures = 0
    successful_indexers = 0
    by_id = {indexer.id: indexer for indexer in scoped}
    answered: list[tuple[ConfiguredIndexer, list[TorznabSearchResult]]] = []
    for indexer_id, outcome in outcomes:
        if outcome.get("status") != "completed":
            failures += 1
            if errors is not None:
                errors.append(f"{outcome.get('name') or 'Indexer'}: {outcome.get('error') or outcome.get('status')}")
            continue
        successful_indexers += 1
        answered.append((by_id[indexer_id], list(outcome.get("results") or [])))
    candidates, stored_results, _ = await evaluate_results(db, target=target, answered=answered)
    return (candidates[0] if candidates else None), stored_results, failures, successful_indexers > 0


async def evaluate_results(
    db: AsyncSession,
    *,
    target: UpgradeTarget,
    answered: list[tuple[ConfiguredIndexer, list[TorznabSearchResult]]],
) -> tuple[list[ScoredCandidate], list[dict[str, Any]], EffectiveProfile]:
    """Score indexer results against a title: qualifying ones best first, and all to store."""

    from app.services.blocklist import blocklist_entries_for_target, entry_matches

    profile, formats = await _evaluation_policy(db, media_type=target.media_type, entity_id=target.profile_entity_id)
    candidates: list[ScoredCandidate] = []
    stored_results: list[dict[str, Any]] = []
    blocklist = await blocklist_entries_for_target(
        db,
        media_type=target.media_type,
        target_entity_type="movie" if target.media_type == MediaType.MOVIES else "season",
        target_entity_id=target.movie_id if target.media_type == MediaType.MOVIES else target.season_id,
    )
    for indexer, results in answered:
        for result in results:
            scored = _score_candidate(target=target, profile=profile, formats=formats, indexer=indexer, result=result)
            blocked = next(
                (
                    entry
                    for entry in blocklist
                    if entry_matches(entry, source_title=result.title, indexer_id=indexer.id, indexer_name=indexer.name)
                ),
                None,
            )
            if scored is not None and blocked is None:
                candidates.append(scored)
            stored = _stored_search_result(target=target, indexer=indexer, result=result, scored=scored, profile=profile)
            if blocked is not None:
                # A release that previously failed for this media is never
                # proposed again until its Blocklist entry is removed.
                stored["eligible"] = False
                stored["rejection_reasons"] = [*stored.get("rejection_reasons", []), f"Blocklisted: {blocked.reason}"]
                stored["blocklisted"] = True
            stored_results.append(stored)
    if not candidates:
        return [], stored_results, profile
    candidates.sort(key=_candidate_sort_key)
    stored_results.sort(
        key=lambda item: (
            not bool(item["eligible"]),
            next(
                (_candidate_sort_key(candidate) for candidate in candidates if str(candidate.indexer_id) == item["indexer_id"] and candidate.result.title == item["title"]),
                (9999, 0, 0, 0, 0, 9999, str(item["title"]).casefold(), str(item["id"])),
            ),
        )
    )
    return candidates, stored_results, profile


def apply_search_result(row: UpgradeCandidate, value: dict[str, Any]) -> None:
    """Make one of a candidate's stored search results the chosen release."""

    snapshot = dict(value.get("snapshot") or {})
    current = snapshot.get("current") or {}
    row.indexer_id = uuid.UUID(str(value["indexer_id"])) if value.get("indexer_id") else None
    row.indexer_name = str(value.get("indexer_name") or row.indexer_name)
    row.guid = str(value.get("guid") or value.get("title") or row.guid)
    row.download_url = value.get("download_url")
    row.title = str(value.get("title") or row.title)
    row.quality = value.get("quality")
    row.custom_format_score = int(value.get("custom_format_score") or 0)
    row.score_delta = row.custom_format_score - int(current.get("custom_format_score") or row.current_custom_format_score)
    row.size = value.get("size")
    row.seeders = value.get("seeders")
    row.leechers = value.get("leechers")
    published = value.get("published_at")
    row.published_at = datetime.fromisoformat(published) if isinstance(published, str) else published
    row.upgrade_types = [str(reason) for reason in value.get("upgrade_types") or []]
    row.added_custom_formats = [dict(item) for item in value.get("added_custom_formats") or []]
    row.comparison_snapshot = {**snapshot, **{key: True for key in ("same_file", "link_failed") if value.get(key)}}


async def _persist_candidate(
    db: AsyncSession,
    *,
    target: UpgradeTarget,
    candidate: ScoredCandidate | None,
    search_results: list[dict[str, Any]],
    searched_at: datetime,
) -> tuple[bool, bool]:
    row = await db.scalar(select(UpgradeCandidate).where(UpgradeCandidate.target_key == target.target_key))
    if row is not None and row.execution_state in {"submitted", "linking", "downloading", "validating", "handoff_attention", "cleanup_required"}:
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
    row.search_results = search_results
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
    movie_id: UUID | None = None,
    show_id: UUID | None = None,
) -> None:
    await update_job(db, job, status=JobStatus.RUNNING, progress={"completed": 0, "total": 0, "percent": 0})
    await db.commit()

    movie_targets, movie_skipped_quality, movie_read_only = await _movie_targets(db)
    show_targets, skipped_airing, show_skipped_quality, show_read_only = await _show_targets(db, today=today)
    read_only_roots = sorted(movie_read_only | show_read_only)
    targets = [*movie_targets, *show_targets]
    if movie_id is not None:
        targets = [target for target in targets if target.movie_id == movie_id]
    elif show_id is not None:
        targets = [target for target in targets if target.media_type == MediaType.SHOWS and target.profile_entity_id == show_id]
    valid_keys = {target.target_key for target in targets}
    inactive = (
        UpgradeCandidate.execution_state.is_(None)
        | UpgradeCandidate.execution_state.in_(["completed", "failed"])
    )
    scope = None
    if movie_id is not None:
        scope = UpgradeCandidate.movie_id == movie_id
    elif show_id is not None:
        season_ids = select(Season.id).where(Season.show_id == show_id)
        scope = UpgradeCandidate.season_id.in_(season_ids)
    if valid_keys:
        stale = delete(UpgradeCandidate).where(~UpgradeCandidate.target_key.startswith("replace:"), UpgradeCandidate.target_key.not_in(valid_keys), inactive)
        if scope is not None:
            stale = stale.where(scope)
        await db.execute(stale)
    else:
        stale = delete(UpgradeCandidate).where(~UpgradeCandidate.target_key.startswith("replace:"), inactive)
        if scope is not None:
            stale = stale.where(scope)
        await db.execute(stale)
    await db.commit()

    # Longest since searched first, so a run that is cancelled or crashes is
    # carried on by the next one rather than repeating the same titles.
    if movie_id is None and show_id is None:
        await db.execute(delete(UpgradeSearchMark).where(UpgradeSearchMark.target_key.not_in(valid_keys)))
        await db.commit()
    marks = dict((await db.execute(select(UpgradeSearchMark.target_key, UpgradeSearchMark.searched_at))).tuples().all())
    targets.sort(key=lambda target: marks[target.target_key].replace(tzinfo=None) if target.target_key in marks else datetime.min)

    indexers = await list_configured_indexers(db)
    found = removed = failures = searched = 0
    errors: list[str] = []
    total = len(targets)
    for position, target in enumerate(targets, start=1):
        best, search_results, target_failures, completed_search = await _search_target(
            db,
            target=target,
            indexers=indexers,
            client_factory=client_factory,
            errors=errors,
        )
        failures += target_failures
        if completed_search:
            searched += 1
            has_candidate, removed_candidate = await _persist_candidate(
                db,
                target=target,
                candidate=best,
                search_results=search_results,
                searched_at=utcnow(),
            )
            found += int(has_candidate)
            removed += int(removed_candidate)
            searched_at = utcnow()
            await db.execute(
                sqlite_insert(UpgradeSearchMark)
                .values(target_key=target.target_key, searched_at=searched_at)
                .on_conflict_do_update(index_elements=["target_key"], set_={"searched_at": searched_at})
            )
        summary = {
            "targets_total": total,
            "targets_searched": searched,
            "candidates_found": found,
            "candidates_removed": removed,
            "indexer_failures": failures,
            "skipped_airing_or_incomplete": skipped_airing,
            "skipped_unknown_current_quality": movie_skipped_quality + show_skipped_quality,
            "skipped_read_only_roots": read_only_roots,
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
        "skipped_read_only_roots": read_only_roots,
        "automatic_grabs": False,
        # What the indexers said, so a run that found nothing says why.
        "indexer_errors": list(dict.fromkeys(errors))[:10],
    }
    if total and not searched and failures:
        # Every search failed: "0 found" would read as "nothing better exists".
        raise JobFailure(
            "UPGRADE_SEARCH_INDEXERS_FAILED",
            f"No indexer search succeeded ({failures} failed). " + (errors[0] if errors else ""),
            details=summary,
            progress={"completed": total, "total": total, "percent": 100},
        )
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
    movie_id: UUID | None = None,
    show_id: UUID | None = None,
) -> None:
    """Evaluate the library and retain its recommendation plus every search result."""

    run_date = today or utcnow().date()

    async def worker(db: AsyncSession, job: Job) -> None:
        await _worker(
            db,
            job,
            client_factory=client_factory,
            today=run_date,
            movie_id=movie_id,
            show_id=show_id,
        )

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
