from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx


@dataclass(frozen=True, slots=True)
class PlexExternalRating:
    """IMDb metadata returned by Plex Discover for a TMDB identity."""

    imdb_id: str | None = None
    rating: float | None = None
    votes: int | None = None


class PlexDiscoverClient:
    """Read IMDb metadata from Plex's provider-backed Discover service.

    Plex's normal library endpoint is scoped to items already in a user's
    library. Discover can resolve a provider GUID such as ``tmdb://550`` for a
    title that is only being searched, which is exactly what the global search
    needs.
    """

    _BASE_URL = "https://discover.provider.plex.tv"
    _CLIENT_IDENTIFIER = "medialogue"

    def __init__(self, token: str, *, timeout: float = 8.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._BASE_URL,
            headers={
                "X-Plex-Token": token,
                "X-Plex-Client-Identifier": self._CLIENT_IDENTIFIER,
                "Accept": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_rating(self, tmdb_id: int, kind: Literal["movie", "show"]) -> PlexExternalRating | None:
        response = await self._client.get(
            "/library/metadata/matches",
            params={"type": 1 if kind == "movie" else 2, "guid": f"tmdb://{tmdb_id}"},
        )
        response.raise_for_status()
        return _parse_rating(response.json())


def _parse_rating(payload: object) -> PlexExternalRating | None:
    if not isinstance(payload, dict):
        return None
    container = payload.get("MediaContainer")
    if not isinstance(container, dict):
        return None
    metadata_values = container.get("Metadata")
    if not isinstance(metadata_values, list) or not metadata_values:
        return None
    metadata = metadata_values[0]
    if not isinstance(metadata, dict):
        return None

    rating: float | None = None
    ratings = metadata.get("Rating")
    if isinstance(ratings, list):
        for value in ratings:
            if not isinstance(value, dict):
                continue
            image = value.get("image")
            candidate = value.get("value")
            if isinstance(image, str) and image.casefold().startswith("imdb://"):
                try:
                    rating = float(candidate)
                except (TypeError, ValueError):
                    pass
                break

    votes = _int_value(metadata.get("imdbRatingCount"))
    if votes is None:
        votes = _int_value(container.get("imdbRatingCount"))

    imdb_id: str | None = None
    guids = metadata.get("Guid")
    if isinstance(guids, list):
        for value in guids:
            if not isinstance(value, dict):
                continue
            candidate = value.get("id")
            if not isinstance(candidate, str) or not candidate.casefold().startswith("imdb"):
                continue
            if "://" in candidate:
                imdb_id = candidate.split("://", 1)[1]
            elif ":" in candidate:
                imdb_id = candidate.split(":", 1)[1]
            else:
                imdb_id = candidate
            imdb_id = imdb_id.lstrip("/")
            if imdb_id and not imdb_id.casefold().startswith("tt"):
                imdb_id = f"tt{imdb_id}"
            break

    if rating is None and imdb_id is None:
        return None
    return PlexExternalRating(imdb_id=imdb_id, rating=rating, votes=votes)


def _int_value(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
