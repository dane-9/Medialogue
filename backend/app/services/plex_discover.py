from __future__ import annotations

import asyncio
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.plex_discover import PlexDiscoverClient, PlexExternalRating
from app.services.integration_state import get_configured_plex


async def lookup_tmdb_ratings(
    db: AsyncSession,
    tmdb_ids: list[int],
    kind: Literal["movie", "show"],
) -> dict[int, PlexExternalRating]:
    """Resolve IMDb ratings for TMDB results when Plex Discover is available."""

    configuration = await get_configured_plex(db)
    if configuration is None or not configuration.enabled or not configuration.token:
        return {}
    if kind not in {"movie", "show"}:
        return {}

    client = PlexDiscoverClient(configuration.token, timeout=5.0)
    semaphore = asyncio.Semaphore(5)

    async def lookup(tmdb_id: int) -> tuple[int, PlexExternalRating | None]:
        async with semaphore:
            try:
                return tmdb_id, await client.get_rating(tmdb_id, kind)
            except Exception:
                # An unavailable Discover lookup must not make TMDB search
                # unusable. Missing ratings are represented as absent fields.
                return tmdb_id, None

    try:
        tasks = [asyncio.create_task(lookup(tmdb_id)) for tmdb_id in dict.fromkeys(tmdb_ids)]
        ratings: dict[int, PlexExternalRating] = {}
        try:
            for completed in asyncio.as_completed(tasks, timeout=6.0):
                try:
                    tmdb_id, rating = await completed
                except Exception:
                    continue
                if rating is not None:
                    ratings[tmdb_id] = rating
        except TimeoutError:
            # Search should remain responsive if Discover is slow or
            # unreachable. Any completed lookups are still retained.
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return ratings
    finally:
        await client.close()
