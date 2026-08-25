from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import DiscoveryReviewState, MediaDirectory, MediaDiscovery, MediaType
from app.parser import ReleaseParseResult
from app.services.tmdb import TMDBUnavailable, resolve_movie_identity_detailed, resolve_show_identity_detailed


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
) -> MediaDiscovery:
    """Persist an observed title without admitting it to the logical library."""

    candidates: list[dict[str, object]] = []
    suggested_tmdb_id: int | None = None
    if title:
        resolver = resolve_movie_identity_detailed if media_type == MediaType.MOVIES else resolve_show_identity_detailed
        resolution = await resolver(db, title, year)
        if resolution.reason in {"not_configured", "unavailable"}:
            label = "movie" if media_type == MediaType.MOVIES else "show"
            raise TMDBUnavailable(
                f"TMDB is {resolution.reason.replace('_', ' ')}; {label} identity cannot be established. "
                "Check Settings -> TMDB, then run the scan again."
            )
        candidates = [discovery_candidate(item) for item in resolution.candidates[:6]]
        if resolution.match is not None:
            suggested_tmdb_id = resolution.match.tmdb_id
            if not any(int(item["tmdb_id"]) == suggested_tmdb_id for item in candidates):
                candidates.insert(0, discovery_candidate(resolution.match))
                candidates = candidates[:6]

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
            candidates = candidates[:6]

    discovery.media_type = media_type
    discovery.parsed_title = title
    discovery.parsed_year = year
    discovery.confidence = confidence
    discovery.parse_snapshot = folder_parse.to_dict()
    discovery.candidates = candidates
    discovery.suggested_tmdb_id = suggested_tmdb_id
    if discovery.selected_tmdb_id is None or discovery.selected_tmdb_id == old_suggestion:
        discovery.selected_tmdb_id = suggested_tmdb_id
    return discovery
