from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import DiscoveryReviewState, MediaDirectory, MediaDiscovery, MediaType
from app.parser import PARSER_VERSION, ReleaseParseResult, parse_release
from app.services.tmdb import (
    TMDBMovieIdentityResolution,
    TMDBShowIdentityResolution,
    TMDBUnavailable,
    resolve_movie_identity_detailed,
    resolve_show_identity_detailed,
)


#: How many alternatives are kept for one folder. Enough that a common title
#: like "Friends" shows its whole result list without a second request.
DISCOVERY_CANDIDATE_LIMIT = 20


async def retire_missing_review_discovery(db: AsyncSession, directory_id: UUID) -> bool:
    """Delete pending Review work whose directory is confirmed absent.

    Approved media follows the normal missing-media lifecycle. A pending
    discovery has never entered the library, so retaining it after the grace
    threshold only leaves an impossible action and, after a rename, a duplicate
    row beside the newly discovered path.
    """

    discovery = await db.scalar(
        select(MediaDiscovery).where(
            MediaDiscovery.media_directory_id == directory_id,
            MediaDiscovery.review_state == DiscoveryReviewState.PENDING,
        )
    )
    if discovery is None:
        return False
    await db.delete(discovery)
    return True


def refresh_discovery_parse(discovery: MediaDiscovery) -> bool:
    """Reparse queued review evidence after the parser grammar changes.

    Review rows intentionally survive reloads, which also means their parsed
    title and TMDB candidates can outlive the parser version that created them.
    Refreshing lazily keeps existing queues correct without a one-off database
    migration for every parser improvement.
    """

    snapshot = discovery.parse_snapshot if isinstance(discovery.parse_snapshot, dict) else {}
    raw_name = snapshot.get("raw_name") or snapshot.get("raw")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raw_name = Path(discovery.media_directory.resolved_path).name
    current = parse_release(raw_name)
    version_changed = snapshot.get("parser_version") != PARSER_VERSION
    identity_changed = (
        discovery.parsed_title != current.identity.title_candidate
        or discovery.parsed_year != current.identity.year
    )
    if not version_changed and not identity_changed:
        return False

    if identity_changed:
        manually_selected = (
            discovery.selected_tmdb_id is not None
            and discovery.selected_tmdb_id != discovery.suggested_tmdb_id
        )
        selected = next(
            (
                item
                for item in discovery.candidates
                if isinstance(item, dict) and item.get("tmdb_id") == discovery.selected_tmdb_id
            ),
            None,
        )
        discovery.candidates = [selected] if manually_selected and selected is not None else []
        discovery.suggested_tmdb_id = None
        if not manually_selected:
            discovery.selected_tmdb_id = None

    discovery.parsed_title = current.identity.title_candidate
    discovery.parsed_year = current.identity.year
    discovery.parse_snapshot = current.to_dict()
    return True


def discovery_candidate(item: object) -> dict[str, object]:
    return {
        "tmdb_id": int(getattr(item, "tmdb_id")),
        "title": str(getattr(item, "title")),
        "original_title": getattr(item, "original_title", None),
        "year": getattr(item, "year", None),
        "overview": getattr(item, "overview", None),
        "poster_ref": getattr(item, "poster_path", None),
    }


async def stage_media_discovery(
    db: AsyncSession,
    directory: MediaDirectory,
    folder_parse: ReleaseParseResult,
    *,
    media_type: MediaType,
    title: str | None,
    year: int | None,
    confidence: float | None = None,
    identity_resolution: TMDBMovieIdentityResolution | TMDBShowIdentityResolution | None = None,
) -> MediaDiscovery:
    """Persist an observed title without admitting it to the logical library."""

    candidates: list[dict[str, object]] = []
    suggested_tmdb_id: int | None = None
    if title:
        resolver = resolve_movie_identity_detailed if media_type == MediaType.MOVIES else resolve_show_identity_detailed
        resolution = identity_resolution or await resolver(db, title, year)
        if resolution.reason in {"not_configured", "unavailable"}:
            label = "movie" if media_type == MediaType.MOVIES else "show"
            raise TMDBUnavailable(
                f"TMDB is {resolution.reason.replace('_', ' ')}; {label} identity cannot be established. "
                "Check Settings -> TMDB, then run the scan again."
            )
        candidates = [discovery_candidate(item) for item in resolution.candidates[:DISCOVERY_CANDIDATE_LIMIT]]
        if resolution.match is not None:
            suggested_tmdb_id = resolution.match.tmdb_id
            if not any(int(item["tmdb_id"]) == suggested_tmdb_id for item in candidates):
                candidates.insert(0, discovery_candidate(resolution.match))
                candidates = candidates[:DISCOVERY_CANDIDATE_LIMIT]

    discovery = await db.scalar(
        select(MediaDiscovery).where(MediaDiscovery.media_directory_id == directory.id)
    )
    if discovery is None:
        discovery = MediaDiscovery(
            media_directory_id=directory.id,
            media_type=media_type,
            review_state=DiscoveryReviewState.PENDING,
        )
        db.add(discovery)
        await db.flush()

    old_suggestion = discovery.suggested_tmdb_id
    old_candidates = [item for item in discovery.candidates if isinstance(item, dict)]
    manually_selected = (
        discovery.selected_tmdb_id is not None
        and discovery.selected_tmdb_id != old_suggestion
    )
    if manually_selected and not any(
        int(item.get("tmdb_id", -1)) == discovery.selected_tmdb_id for item in candidates
    ):
        selected_candidate = next(
            (
                item
                for item in old_candidates
                if int(item.get("tmdb_id", -1)) == discovery.selected_tmdb_id
            ),
            None,
        )
        if selected_candidate is not None:
            candidates.insert(0, selected_candidate)
            candidates = candidates[:DISCOVERY_CANDIDATE_LIMIT]

    discovery.media_type = media_type
    discovery.parsed_title = title
    discovery.parsed_year = year
    discovery.confidence = confidence
    discovery.parse_snapshot = folder_parse.to_dict()
    discovery.candidates = candidates
    discovery.suggested_tmdb_id = suggested_tmdb_id
    if discovery.selected_tmdb_id is None or discovery.selected_tmdb_id == old_suggestion:
        discovery.selected_tmdb_id = suggested_tmdb_id
    # With no confident match, the best candidate is still a better starting
    # point than nothing: a reviewer confirms or corrects one guess faster than
    # they research a blank row, and the confidence shown next to it says how
    # much to trust it. "No match" then means what it says, that TMDB returned
    # nothing at all.
    if discovery.selected_tmdb_id is None and candidates:
        discovery.selected_tmdb_id = int(candidates[0]["tmdb_id"])
    return discovery
