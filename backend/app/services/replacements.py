"""A repair queue driven by confirmed, recent tracker evidence, with shared upgrade execution."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.integrations.torznab import TorznabClient, SearchResult
from app.models.domain import (MediaDirectory, ReleaseState, JobStatus, MediaType, MovieRelease, ShowRelease, Torrent,
                               TorrentClientObservation, TorrentTrackerObservation, UpgradeCandidate)
from app.services.integration_state import list_configured_indexers, get_configured_indexer
from app.services.jobs import run_job, update_job
from app.services.upgrade_candidates import (_movie_targets, _show_targets, _search_target,
    _persist_candidate, _evaluation_policy, _score_candidate)


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


async def lost_sources(db: AsyncSession) -> dict[UUID, Torrent]:
    """Archived-only and stale observations never establish a current source failure."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    rows = (await db.scalars(select(Torrent).options(selectinload(Torrent.observations),
        selectinload(Torrent.tracker_observations)))).all()
    lost = {}
    for row in rows:
        replacement_id = (row.metadata_json or {}).get("replacement_candidate_id")
        if replacement_id:
            candidate = await db.get(UpgradeCandidate, UUID(replacement_id))
            if candidate and candidate.validated_at and candidate.execution_state in {"completed", "cleanup_required"}:
                continue
        live = [item for item in row.observations if item.is_present and _aware(item.last_seen_at) >= cutoff]
        if not live:
            continue
        # Require every currently observed client to have exclusively confirmed
        # terminal endpoints. An uninspected second client is uncertainty.
        confirmed = True
        for client in live:
            trackers = [item for item in row.tracker_observations if item.download_client_id == client.download_client_id
                        and item.active and item.health_state != "disabled"]
            if not trackers or any(not item.terminal_confirmed or item.health_state != "error"
                                   or not item.last_checked_at or _aware(item.last_checked_at) < cutoff for item in trackers):
                confirmed = False
                break
        if confirmed:
            lost[row.id] = row
    return lost


def releases_without_any_healthy_source(releases, lost: dict[UUID, Torrent]) -> list[Torrent]:
    """Return failed torrents only for payloads whose every cross-seed failed.

    A release is still available while any torrent attached to it has a usable
    tracker source. One tracker/indexer losing its copy must not place the
    shared media payload in Replace Media.
    """

    affected: list[Torrent] = []
    for release in releases:
        torrents = list(release.torrents)
        if torrents and all(torrent.id in lost for torrent in torrents):
            affected.extend(torrents)
    return affected


async def replacement_targets(db: AsyncSession):
    lost = await lost_sources(db)
    movies, _, _ = await _movie_targets(db, replacement=True)
    shows, _, _, _ = await _show_targets(db, today=datetime.now(timezone.utc).date(), replacement=True)
    targets = []
    for target in [*movies, *shows]:
        model = MovieRelease if target.media_type == MediaType.MOVIES else ShowRelease
        releases = (await db.scalars(select(model).options(selectinload(model.torrents)).where(
            model.id.in_([item.release_id for item in target.releases])))).all()
        affected = releases_without_any_healthy_source(releases, lost)
        if not affected:
            continue
        excluded = tuple(sorted({torrent.info_hash.lower() for release in releases for torrent in release.torrents}))
        targets.append((replace(target, target_key="replace:" + target.target_key, replacement=True,
                                excluded_hashes=excluded), affected))
    return targets


async def replacement_queue(db: AsyncSession) -> list[dict]:
    lost = await lost_sources(db)
    targets = await replacement_targets(db)
    covered = set()
    items = []
    now = datetime.now(timezone.utc)
    for target, torrents in targets:
        covered.update(t.id for t in torrents)
        row = await db.scalar(select(UpgradeCandidate).where(UpgradeCandidate.target_key == target.target_key))
        model = MovieRelease if target.media_type == MediaType.MOVIES else ShowRelease
        releases = (await db.scalars(select(model).options(selectinload(model.torrents)).where(model.id.in_([r.release_id for r in target.releases])))).all()
        media_present = all(r.release_state == ReleaseState.CURRENT for r in releases)
        alternate_source = any(t.id not in lost and (t.tracker_summary or {}).get("state") in {"healthy", "degraded"}
                               and _aware((t.tracker_summary or {}).get("checked_at"))
                               and _aware((t.tracker_summary or {}).get("checked_at")) > now - timedelta(hours=2)
                               for r in releases for t in r.torrents)
        preferences = [(t.metadata_json or {}).get("replacement_review", {}) for t in torrents]
        dismissed = all(p.get("ignored") or (_aware(p.get("snoozed_until")) or now) > now for p in preferences)
        items.append({"target_key": target.target_key, "title": target.search_target.title,
            "media_type": target.media_type.value, "movie_id": str(target.movie_id) if target.movie_id else None,
            "season_id": str(target.season_id) if target.season_id else None,
            "season_number": target.search_target.season, "current_release": target.reference.release_name,
            "current_quality": target.reference.quality, "current_score": max(r.custom_format_score for r in target.releases),
            "torrent_ids": [str(t.id) for t in torrents], "reason": "Tracker reports torrent removed",
            "media_present": media_present, "alternate_source": alternate_source,
            "last_search_result": (torrents[0].metadata_json or {}).get("replacement_search_result"),
            "dismissed": dismissed, "candidate_id": str(row.id) if row else None,
            "candidate_title": row.title if row else None, "candidate_quality": row.quality if row else None,
            "candidate_score": row.custom_format_score if row else None, "seeders": row.seeders if row else None,
            "indexer": row.indexer_name if row else None, "execution_state": row.execution_state if row else None,
            "error": row.execution_error if row else None, "blocked_reason": None})
    for torrent_id, torrent in lost.items():
        if torrent_id in covered:
            continue
        review = (torrent.metadata_json or {}).get("replacement_review", {})
        items.append({"target_key": f"torrent:{torrent_id}", "title": torrent.name,
            "torrent_ids": [str(torrent_id)], "reason": "Tracker reports torrent removed",
            "dismissed": bool(review.get("ignored") or (_aware(review.get("snoozed_until")) or now) > now),
            "blocked_reason": "Resolve media identity, current quality, monitoring, and season coverage before searching for an equivalent replacement."})
    # Keep active handoffs visible after the previous torrent disappears or the
    # replacement becomes the new current release.
    known = {item.get("candidate_id") for item in items}
    active = (await db.scalars(select(UpgradeCandidate).where(UpgradeCandidate.target_key.startswith("replace:"),
        UpgradeCandidate.execution_state.in_(["submitted", "downloading", "validating", "handoff_attention", "cleanup_required"])))).all()
    for row in active:
        if str(row.id) not in known:
            items.append({"target_key": row.target_key, "title": row.current_release_name,
                "torrent_ids": [], "reason": "Replacement in progress", "dismissed": False,
                "candidate_id": str(row.id), "candidate_title": row.title, "candidate_score": row.custom_format_score,
                "candidate_quality": row.quality, "current_quality": row.current_quality,
                "current_score": row.current_custom_format_score, "media_type": row.media_type.value,
                "execution_state": row.execution_state, "error": row.execution_error, "blocked_reason": None})
    return sorted(items, key=lambda item: (item.get("dismissed", False), item.get("alternate_source", False), item.get("media_present") is True, item["title"].casefold()))


async def run_replacement_search(job_id: UUID, *, target_key: str | None = None, client_factory=TorznabClient):
    async def worker(db, job):
        await update_job(db, job, status=JobStatus.RUNNING)
        await db.commit()
        targets = await replacement_targets(db)
        if target_key:
            targets = [item for item in targets if item[0].target_key == target_key]
        else:
            visible = {item["target_key"] for item in await replacement_queue(db) if not item["dismissed"]}
            targets = [item for item in targets if item[0].target_key in visible]
        indexers = await list_configured_indexers(db)
        found = failures = 0
        for position, (target, torrents) in enumerate(targets):
            best, results, errors, success = await _search_target(db, target=target, indexers=indexers, client_factory=client_factory)
            failures += errors
            if success:
                candidate, _ = await _persist_candidate(db, target=target, candidate=best, search_results=results,
                                                      searched_at=datetime.now(timezone.utc))
                found += int(candidate)
            for torrent in torrents:
                torrent.metadata_json = {**torrent.metadata_json, "replacement_last_search": datetime.now(timezone.utc).isoformat(),
                    "replacement_search_result": "candidate" if best else "no_match" if success else "failed"}
            await update_job(db, job, progress={"percent": round((position + 1) / len(targets) * 100)})
            await db.commit()
        await update_job(db, job, status=JobStatus.COMPLETED,
            summary={"targets": len(targets), "candidates_found": found, "indexer_failures": failures,
                     "message": f"Found {found} suitable replacement(s). No downloads were started."},
            progress={"percent": 100})
    await run_job(job_id, worker, failure_code="REPLACEMENT_SEARCH_FAILED", failure_message="Replacement search failed")


async def revalidate_replacement(db: AsyncSession, row: UpgradeCandidate) -> None:
    targets = await replacement_targets(db)
    target = next((target for target, _ in targets if target.target_key == row.target_key), None)
    if target is None:
        raise AppError("REPLACEMENT_STALE", "The source problem or current media changed. Refresh Replace Media.", status_code=409)
    profile, formats = await _evaluation_policy(db, media_type=target.media_type, entity_id=target.profile_entity_id)
    indexer = await get_configured_indexer(db, row.indexer_id)
    if not indexer or not indexer.enabled:
        raise AppError("INDEXER_UNAVAILABLE", "The replacement indexer is unavailable", status_code=409)
    result = SearchResult(guid=row.guid, title=row.title, download_url=row.download_url,
                          size=row.size, seeders=row.seeders, published=None)
    scored = _score_candidate(target=target, profile=profile, formats=formats, indexer=indexer, result=result)
    if scored is None or scored.custom_format_score != row.custom_format_score or profile.snapshot() != row.comparison_snapshot.get("profile"):
        raise AppError("REPLACEMENT_STALE", "The profile or replacement eligibility changed. Search again before downloading.", status_code=409)
    row.comparison_snapshot = scored.snapshot
