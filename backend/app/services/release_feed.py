"""Finding upgrades from each tracker's list of new releases.

A full upgrade search asks every tracker about every title, which takes hours.
What changes from day to day is only what was uploaded, so this reads each
tracker's newest releases, matches them to the library by title and year, and
scores just the titles that turned up. The full search remains for releases
that existed before they could be seen here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import comparable_release_title
from app.integrations.torznab import SearchResult, TorznabClient
from app.models.domain import Job, JobStatus, MediaScope, MediaType, Movie, Show, UpgradeCandidate
from app.parser import parse_release
from app.services.integration_state import ConfiguredIndexer, list_configured_indexers
from app.services.jobs import JobFailure, run_job, update_job
from app.services.upgrade_candidates import (
    _movie_targets,
    _persist_candidate,
    _quality_preference,
    _show_targets,
    evaluate_results,
    utcnow,
)

logger = logging.getLogger(__name__)

JOB_TYPE = "check_new_releases"
#: Torznab's movie and TV categories, for trackers with none configured: without
#: a query, an unfiltered feed is mostly things that are neither.
_DEFAULT_CATEGORIES = {"movies": (2000,), "shows": (5000,)}
_SCOPES = {"movies": {MediaScope.MOVIES, MediaScope.BOTH}, "shows": {MediaScope.SHOWS, MediaScope.BOTH}}

Feed = tuple[ConfiguredIndexer, str, list[SearchResult] | None, str | None]


async def _read_feed(indexer: ConfiguredIndexer, media_type: str, client_factory: Any) -> Feed:
    client = client_factory(indexer.torznab_url, indexer.api_key or "", timeout=float(indexer.timeout_seconds))
    categories = tuple(value for value in indexer.categories if value > 0) or _DEFAULT_CATEGORIES[media_type]
    try:
        results = await asyncio.wait_for(client.recent(media_type=media_type, categories=categories), timeout=float(indexer.timeout_seconds))
        return indexer, media_type, results, None
    except TimeoutError:
        return indexer, media_type, None, f"{indexer.name}: timed out"
    except Exception as exc:
        logger.warning(f"New release check failed for {indexer.name}: {exc}", extra={"indexer": indexer.name})
        return indexer, media_type, None, f"{indexer.name}: {exc}"
    finally:
        await client.close()


async def _titles_by_name(db: AsyncSession, model: type[Movie] | type[Show]) -> dict[str, list[tuple[UUID, int | None]]]:
    names: dict[str, list[tuple[UUID, int | None]]] = {}
    for title_id, title, year in (await db.execute(select(model.id, model.title, model.year).where(model.monitored.is_(True)))).all():
        names.setdefault(comparable_release_title(title), []).append((title_id, year))
    return names


def _matching_title(names: dict[str, list[tuple[UUID, int | None]]], result: SearchResult, *, year_required: bool) -> UUID | None:
    """The one library title a release names. Nothing is better than a guess."""

    identity = parse_release(result.title).identity
    same_name = names.get(comparable_release_title(identity.title_candidate), [])
    if identity.year is None:
        return same_name[0][0] if not year_required and len(same_name) == 1 else None
    matches = [title_id for title_id, year in same_name if year == identity.year or (not year_required and year is None)]
    return matches[0] if len(matches) == 1 else None


async def _offer(db: AsyncSession, target: Any, answered: list[tuple[ConfiguredIndexer, list[SearchResult]]]) -> bool:
    """Offer the best new release if it beats what the title is already offered."""

    candidates, stored, profile = await evaluate_results(db, target=target, answered=answered)
    if not candidates:
        return False
    row = await db.scalar(select(UpgradeCandidate).where(UpgradeCandidate.target_key == target.target_key))
    if row is not None and row.execution_state not in {None, "completed", "failed"}:
        return False
    qualifying = [value for value in stored if value["eligible"]]
    new_ids = {value["id"] for value in qualifying}
    earlier = [value for value in ((row.search_results or []) if row is not None else []) if value.get("id") not in new_ids]
    best = candidates[0]
    if row is not None and row.execution_eligible:
        offered = _quality_preference(row.quality, profile.quality_order)
        if offered is not None and (best.quality_preference, -best.custom_format_score) >= (offered, -row.custom_format_score):
            # Not better than the current offer, but worth listing beside it.
            row.search_results = [*earlier, *qualifying]
            return False
    await _persist_candidate(db, target=target, candidate=best, search_results=[*qualifying, *earlier], searched_at=utcnow())
    return True


async def run_new_release_check(job_id: UUID, *, client_factory: Any = TorznabClient, today: date | None = None) -> None:
    """Read every tracker's newest releases and offer the upgrades among them."""

    async def worker(db: AsyncSession, job: Job) -> None:
        await update_job(db, job, status=JobStatus.RUNNING, progress={"percent": 0})
        await db.commit()
        indexers = [indexer for indexer in await list_configured_indexers(db) if indexer.enabled and indexer.enable_rss and indexer.use_for_upgrades]
        wanted = [(indexer, kind) for indexer in indexers for kind in ("movies", "shows") if indexer.scope in _SCOPES[kind]]
        feeds = await asyncio.gather(*[_read_feed(indexer, kind, client_factory) for indexer, kind in wanted])
        errors = [error for *_, error in feeds if error]
        if wanted and len(errors) == len(wanted):
            raise JobFailure("NEW_RELEASE_CHECK_FAILED", f"No tracker answered. {errors[0]}", progress={"percent": 100})

        names = {"movies": await _titles_by_name(db, Movie), "shows": await _titles_by_name(db, Show)}
        # title -> indexer name -> (indexer, its results for that title)
        found: dict[str, dict[UUID, dict[str, tuple[ConfiguredIndexer, list[SearchResult]]]]] = {"movies": {}, "shows": {}}
        seen = 0
        for indexer, kind, results, _error in feeds:
            for result in results or []:
                seen += 1
                title_id = _matching_title(names[kind], result, year_required=kind == "movies")
                if title_id is not None:
                    found[kind].setdefault(title_id, {}).setdefault(indexer.name, (indexer, []))[1].append(result)

        targets: list[Any] = []
        if found["movies"]:
            targets.extend((await _movie_targets(db, movie_ids=set(found["movies"])))[0])
        if found["shows"]:
            targets.extend((await _show_targets(db, today=today or utcnow().date(), show_ids=set(found["shows"])))[0])
        offered = 0
        for position, target in enumerate(targets, start=1):
            kind, title_id = ("movies", target.movie_id) if target.media_type == MediaType.MOVIES else ("shows", target.profile_entity_id)
            offered += int(await _offer(db, target, list(found[kind][title_id].values())))
            await update_job(db, job, progress={"percent": round(position * 100 / len(targets), 1)})
            await db.commit()
        await update_job(
            db, job, status=JobStatus.COMPLETED, progress={"percent": 100},
            summary={**dict(job.summary or {}), "releases_seen": seen, "titles_matched": len(targets),
                     "upgrades_offered": offered, "indexer_errors": errors[:10],
                     "message": f"Read {seen} new releases and offered {offered} upgrade(s). Nothing was downloaded."},
        )

    await run_job(
        job_id,
        worker,
        failure_code="NEW_RELEASE_CHECK_FAILED",
        failure_message="The new release check failed.",
        failure_progress={"percent": 100},
    )
