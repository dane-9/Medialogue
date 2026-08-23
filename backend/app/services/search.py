from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.integrations.torznab import SearchResult as TorznabSearchResult
from app.integrations.torznab import TorznabClient
from app.models.domain import (
    CustomFormat as CustomFormatModel,
    InteractiveSearchResult,
    Job,
    JobStatus,
    MediaScope,
    MediaType,
)
from app.parser import parse_release_name
from app.services.custom_formats import CustomFormat as EvaluationFormat, evaluate_custom_formats
from app.services.events import publish_live_event
from app.services.integration_state import ConfiguredIndexer, get_configured_indexer, list_configured_indexers
from app.services.jobs import update_job
from app.services.quality_profiles import load_effective_profile, load_quality_profile_for_search, minimum_quality_status

TorznabClientFactory = Callable[..., TorznabClient]
SEARCH_RESULT_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class SearchTarget:
    media_type: MediaType
    entity_type: str
    entity_id: UUID | None
    title: str
    # Quality Profile assignments live on the logical Movie/Show. For Movie
    # searches this equals entity_id; episode/season searches point here to
    # their parent Show while retaining the episode/season target separately.
    profile_entity_id: UUID | None = None
    quality_profile_id: UUID | None = None
    year: int | None = None
    tmdb_id: int | None = None
    overview: str | None = None
    poster_ref: str | None = None
    season: int | None = None
    episode: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_type": self.media_type.value,
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "profile_entity_id": str(self.profile_entity_id) if self.profile_entity_id else None,
            "quality_profile_id": str(self.quality_profile_id) if self.quality_profile_id else None,
            "title": self.title,
            "year": self.year,
            "tmdb_id": self.tmdb_id,
            "overview": self.overview,
            "poster_ref": self.poster_ref,
            "season": self.season,
            "episode": self.episode,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SearchTarget":
        return cls(
            media_type=MediaType(value["media_type"]),
            entity_type=str(value["entity_type"]),
            entity_id=UUID(str(value["entity_id"])) if value.get("entity_id") else None,
            title=str(value["title"]),
            profile_entity_id=UUID(str(value["profile_entity_id"])) if value.get("profile_entity_id") else None,
            quality_profile_id=UUID(str(value["quality_profile_id"])) if value.get("quality_profile_id") else None,
            year=int(value["year"]) if value.get("year") is not None else None,
            tmdb_id=int(value["tmdb_id"]) if value.get("tmdb_id") is not None else None,
            overview=str(value["overview"]) if value.get("overview") is not None else None,
            poster_ref=str(value["poster_ref"]) if value.get("poster_ref") is not None else None,
            season=int(value["season"]) if value.get("season") is not None else None,
            episode=int(value["episode"]) if value.get("episode") is not None else None,
        )


async def cleanup_expired_search_results(db: AsyncSession) -> int:
    result = await db.execute(
        delete(InteractiveSearchResult).where(
            InteractiveSearchResult.selected_at.is_(None),
            InteractiveSearchResult.expires_at < utcnow(),
        )
    )
    return int(result.rowcount or 0)


async def run_search_job(
    job_id: UUID,
    *,
    client_factory: TorznabClientFactory = TorznabClient,
) -> None:
    """Run one interactive search with independent indexer failure domains."""

    async with db_session.async_session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None or job.status != JobStatus.QUEUED:
            # A queued job may have been cancelled before the background task
            # actually started. Never resurrect cancelled/interrupted work.
            return
        target = SearchTarget.from_dict(dict(job.summary.get("target") or {}))
        scope_values = (
            {MediaScope.MOVIES, MediaScope.BOTH}
            if target.media_type == MediaType.MOVIES
            else {MediaScope.SHOWS, MediaScope.BOTH}
        )
        indexers = sorted(
            [
                indexer
                for indexer in await list_configured_indexers(db)
                if indexer.enabled and indexer.enable_interactive_search and indexer.scope in scope_values
            ],
            key=lambda item: (item.priority, item.name.casefold()),
        )
        # Freeze both the profile scores/overrides and Custom Format
        # definitions at search start. Every indexer result in this job is
        # therefore evaluated under the same rules even if settings are edited
        # while a slow indexer is still responding.
        if target.quality_profile_id is not None:
            effective_profile = await load_quality_profile_for_search(
                db, media_type=target.media_type, profile_id=target.quality_profile_id
            )
        else:
            profile_entity_id = target.profile_entity_id or target.entity_id
            if profile_entity_id is None:
                raise ValueError("Interactive search target has no Quality Profile context")
            effective_profile = await load_effective_profile(
                db, media_type=target.media_type, entity_id=profile_entity_id
            )
        eligible_scopes = (
            (MediaScope.MOVIES, MediaScope.BOTH)
            if target.media_type == MediaType.MOVIES
            else (MediaScope.SHOWS, MediaScope.BOTH)
        )
        custom_format_rows = (
            await db.scalars(
                select(CustomFormatModel)
                .where(CustomFormatModel.enabled.is_(True), CustomFormatModel.media_scope.in_(eligible_scopes))
                .order_by(CustomFormatModel.name)
            )
        ).all()
        custom_format_definitions = [
            {
                "id": str(row.id),
                "name": row.name,
                "description": row.description,
                "media_scope": row.media_scope.value,
                "enabled": row.enabled,
                "condition_definition": dict(row.condition_definition or {}),
                "revision": row.revision,
            }
            for row in custom_format_rows
        ]
        await cleanup_expired_search_results(db)
        summary = dict(job.summary or {})
        summary["target"] = target.to_dict()
        summary["quality_profile"] = effective_profile.snapshot()
        summary["custom_formats"] = custom_format_definitions
        summary["indexers"] = {
            str(indexer.id): {
                "id": str(indexer.id),
                "name": indexer.name,
                "status": "queued",
                "results": 0,
                "elapsed_ms": None,
                "error": None,
            }
            for indexer in indexers
        }
        summary["result_count"] = 0
        await update_job(
            db,
            job,
            status=JobStatus.RUNNING,
            progress={"completed_indexers": 0, "total_indexers": len(indexers), "percent": 0},
            summary=summary,
        )
        await db.commit()
        publish_live_event(
            "search.started",
            entity_type="job",
            entity_id=job.id,
            data={"job_id": str(job.id), "target": target.to_dict(), "indexer_count": len(indexers)},
        )

    if not indexers:
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is not None:
                summary = dict(job.summary or {})
                summary["message"] = "No enabled indexers are configured for this media type."
                await update_job(
                    db,
                    job,
                    status=JobStatus.COMPLETED,
                    progress={"completed_indexers": 0, "total_indexers": 0, "percent": 100},
                    summary=summary,
                )
                await db.commit()
                publish_live_event(
                    "search.completed",
                    entity_type="job",
                    entity_id=job.id,
                    data={"job_id": str(job.id), "result_count": 0},
                )
        return

    tasks = {
        asyncio.create_task(_query_indexer(indexer, target, client_factory=client_factory)): indexer
        for indexer in indexers
    }
    completed_count = 0
    try:
        for future in asyncio.as_completed(tasks):
            # as_completed yields wrapper futures, so the indexer comes back in
            # the function result rather than by indexing the task dictionary.
            indexer_id, outcome = await future
            completed_count += 1
            async with db_session.async_session_factory() as db:
                job = await db.get(Job, job_id)
                if job is None:
                    return
                if job.status == JobStatus.CANCELLED:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await db.commit()
                    publish_live_event(
                        "search.cancelled",
                        entity_type="job",
                        entity_id=job.id,
                        data={"job_id": str(job.id)},
                    )
                    return
                indexer = await get_configured_indexer(db, indexer_id)
                summary = dict(job.summary or {})
                indexer_states = dict(summary.get("indexers") or {})
                current = dict(indexer_states.get(str(indexer_id)) or {})
                current.update(
                    {
                        "id": str(indexer_id),
                        "name": outcome["name"],
                        "status": outcome["status"],
                        "results": len(outcome["results"]),
                        "elapsed_ms": outcome.get("elapsed_ms"),
                        "error": outcome.get("error"),
                    }
                )
                indexer_states[str(indexer_id)] = current
                summary["indexers"] = indexer_states

                if indexer is not None:
                    indexer.last_checked_at = utcnow()
                    indexer.latency_ms = outcome.get("elapsed_ms")
                    if outcome["status"] == "completed":
                        indexer.health = "healthy"
                        indexer.last_success_at = utcnow()
                        indexer.last_error = None
                    else:
                        indexer.health = "unavailable"
                        indexer.last_error = outcome.get("error")

                stored = await _store_results(
                    db,
                    job_id=job.id,
                    indexer_id=indexer_id,
                    indexer_name=outcome["name"],
                    target=target,
                    results=outcome["results"],
                    profile_snapshot=dict(summary.get("quality_profile") or {}),
                    custom_format_definitions=list(summary.get("custom_formats") or []),
                    indexer_priority=int(outcome.get("priority") or 25),
                )
                summary["result_count"] = int(summary.get("result_count") or 0) + len(stored)
                percent = round((completed_count / len(indexers)) * 100, 1)
                await update_job(
                    db,
                    job,
                    progress={
                        "completed_indexers": completed_count,
                        "total_indexers": len(indexers),
                        "percent": percent,
                    },
                    summary=summary,
                )
                await db.commit()

                publish_live_event(
                    "search.indexer_status",
                    entity_type="job",
                    entity_id=job.id,
                    data={"job_id": str(job.id), **current},
                )
                for row in stored:
                    publish_live_event(
                        "search.result",
                        entity_type="job",
                        entity_id=job.id,
                        data={
                            "job_id": str(job.id),
                            "result_id": str(row.id),
                            "indexer_id": str(indexer_id),
                            "indexer_name": row.indexer_name,
                            "title": row.title,
                            "quality": row.quality,
                            "edition": row.edition,
                            "release_group": row.release_group,
                            "size": row.size,
                            "seeders": row.seeders,
                            "custom_format_score": row.custom_format_score,
                            "quality_profile_name": (row.custom_format_snapshot or {}).get("profile_name"),
                            "minimum_quality_met": (row.custom_format_snapshot or {}).get("minimum_quality_met"),
                        },
                    )
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise
    except Exception as exc:
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is not None and job.status not in {JobStatus.CANCELLED, JobStatus.COMPLETED}:
                await update_job(
                    db,
                    job,
                    status=JobStatus.FAILED,
                    error={"code": "SEARCH_JOB_FAILED", "message": str(exc)},
                )
                await db.commit()
                publish_live_event(
                    "search.failed",
                    entity_type="job",
                    entity_id=job.id,
                    data={"job_id": str(job.id), "message": str(exc)},
                )
        return

    async with db_session.async_session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None or job.status == JobStatus.CANCELLED:
            return
        summary = dict(job.summary or {})
        await update_job(
            db,
            job,
            status=JobStatus.COMPLETED,
            progress={"completed_indexers": len(indexers), "total_indexers": len(indexers), "percent": 100},
            summary=summary,
        )
        await db.commit()
        publish_live_event(
            "search.completed",
            entity_type="job",
            entity_id=job.id,
            data={"job_id": str(job.id), "result_count": int(summary.get("result_count") or 0)},
        )


async def _query_indexer(
    indexer: ConfiguredIndexer,
    target: SearchTarget,
    *,
    client_factory: TorznabClientFactory,
) -> tuple[UUID, dict[str, Any]]:
    started = perf_counter()
    client = client_factory(
        indexer.torznab_url,
        indexer.api_key or "",
        timeout=float(indexer.timeout_seconds),
    )
    try:
        query = target.title if target.year is None else f"{target.title} {target.year}"
        try:
            results = await asyncio.wait_for(
                client.search(
                    query,
                    media_type=target.media_type.value,
                    tmdb_id=target.tmdb_id,
                    season=target.season,
                    episode=target.episode,
                    categories=tuple(indexer.categories),
                ),
                timeout=float(indexer.timeout_seconds),
            )
        except TimeoutError:
            return indexer.id, {
                "name": indexer.name,
                "status": "timeout",
                "results": [],
                "elapsed_ms": round((perf_counter() - started) * 1000),
                "error": f"Timed out after {indexer.timeout_seconds} seconds.",
            }
        except Exception as exc:
            return indexer.id, {
                "name": indexer.name,
                "status": "failed",
                "results": [],
                "elapsed_ms": round((perf_counter() - started) * 1000),
                "error": str(exc),
            }
        eligible = [
            result for result in results
            if result.seeders is None or result.seeders >= indexer.minimum_seeders
        ]
        return indexer.id, {
            "name": indexer.name,
            "status": "completed",
            "results": eligible,
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "error": None,
            "priority": indexer.priority,
        }
    finally:
        await client.close()


async def _store_results(
    db: AsyncSession,
    *,
    job_id: UUID,
    indexer_id: UUID,
    indexer_name: str,
    target: SearchTarget,
    results: list[TorznabSearchResult],
    profile_snapshot: dict[str, Any],
    custom_format_definitions: list[dict[str, Any]],
    indexer_priority: int = 25,
) -> list[InteractiveSearchResult]:
    stored: list[InteractiveSearchResult] = []
    seen: set[str] = set()
    now = utcnow()
    custom_formats = [EvaluationFormat.from_dict(value) for value in custom_format_definitions]
    profile_scores = {str(key): int(value) for key, value in (profile_snapshot.get("profile_scores") or {}).items()}
    score_overrides = {str(key): int(value) for key, value in (profile_snapshot.get("score_overrides") or {}).items()}
    minimum_quality = profile_snapshot.get("minimum_quality")
    quality_order = [str(value) for value in (profile_snapshot.get("quality_order") or [])]
    quality_preferences = {name: len(quality_order) - index for index, name in enumerate(quality_order)}

    for result in results:
        guid = (result.guid or result.download_url or result.title).strip()
        if not guid or guid in seen or not result.title.strip():
            continue
        seen.add(guid)
        parsed = parse_release_name(result.title)
        warnings = list(parsed.warnings)
        candidate_quality = parsed.quality.canonical
        quality_allowed = not quality_order or candidate_quality in quality_preferences
        quality_preference = quality_preferences.get(candidate_quality, -1 if quality_order else 0)
        if quality_order and not quality_allowed:
            warnings.append(
                f"Quality not enabled in this profile: {candidate_quality or 'Unknown quality'}."
            )
        if not result.download_url:
            warnings.append("No downloadable torrent URL was supplied by the indexer.")

        evaluation = evaluate_custom_formats(
            custom_formats,
            parsed,
            profile_scores=profile_scores,
            score_overrides=score_overrides,
            context={"indexer": indexer_name},
        )
        floor_status = minimum_quality_status(parsed.quality.canonical, str(minimum_quality) if minimum_quality else None)
        if minimum_quality and floor_status is False:
            warnings.append(
                f"Below minimum quality: {parsed.quality.canonical or 'Unknown'} is below {minimum_quality}."
            )
        elif minimum_quality and floor_status is None:
            warnings.append(
                f"Minimum quality {minimum_quality} could not be confirmed because this result has no comparable canonical quality."
            )

        format_snapshots: list[dict[str, Any]] = []
        for item in evaluation.formats:
            key = str(item.custom_format_id)
            configured = int(profile_scores.get(key, 0))
            override = score_overrides.get(key)
            effective = int(override if override is not None else configured)
            evidence = item.to_dict()
            evidence["profile_score"] = configured
            evidence["override_score"] = int(override) if override is not None else None
            evidence["effective_score"] = effective
            evidence["score_offset"] = int(item.score_offset)
            evidence["contribution"] = int(item.score_contribution)
            # Keep the base score explicitly tied to its profile/override; the
            # Custom Format contributes only its matched rule offset.
            evidence.pop("score", None)
            evidence.pop("configured_score", None)
            format_snapshots.append(evidence)

        custom_format_snapshot = {
            "schema_version": 2,
            "evaluated_at": now.isoformat(),
            "profile_id": profile_snapshot.get("profile_id"),
            "profile_name": profile_snapshot.get("profile_name"),
            "profile_revision": profile_snapshot.get("profile_revision"),
            "assignment_revision": profile_snapshot.get("assignment_revision"),
            "minimum_quality_definition_id": profile_snapshot.get("minimum_quality_definition_id"),
            "minimum_quality": minimum_quality,
            "candidate_quality": parsed.quality.canonical,
            "quality_allowed": quality_allowed,
            "quality_preference": quality_preference,
            "indexer_priority": indexer_priority,
            "minimum_quality_met": floor_status,
            "score_evaluated": True,
            "total_score": int(evaluation.total_score),
            "formats": format_snapshots,
            "matched_format_ids": [item.custom_format_id for item in evaluation.formats if item.matched],
            "matched_format_names": [item.custom_format_name for item in evaluation.formats if item.matched],
        }
        row = InteractiveSearchResult(
            job_id=job_id,
            indexer_id=indexer_id,
            indexer_name=indexer_name,
            media_type=target.media_type,
            target_entity_type=target.entity_type,
            target_entity_id=target.entity_id,
            guid=guid,
            title=result.title,
            download_url=result.download_url,
            size=result.size,
            seeders=result.seeders,
            published_at=_published_at(result.published),
            parser_version=parsed.parser_version,
            parse_snapshot=parsed.to_dict(),
            quality=parsed.quality.canonical,
            edition=parsed.edition,
            release_group=parsed.release_group,
            custom_format_score=int(evaluation.total_score),
            custom_format_snapshot=custom_format_snapshot,
            warnings=warnings,
            expires_at=now + SEARCH_RESULT_TTL,
        )
        db.add(row)
        stored.append(row)
    if stored:
        await db.flush()
    return stored


def _published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
