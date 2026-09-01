from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.integrations.plex_discover import PlexDiscoverClient, PlexExternalRating
from app.services import plex_discover as plex_discover_service


def test_plex_discover_maps_imdb_rating_and_identity() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/library/metadata/matches"
        assert request.url.params["type"] == "1"
        assert request.url.params["guid"] == "tmdb://550"
        assert request.headers["X-Plex-Client-Identifier"] == "medialogue"
        assert request.headers["Accept"] == "application/json"
        return httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Metadata": [
                        {
                            "Rating": [
                                {"image": "rottentomatoes://fresh", "value": 0.98},
                                {"image": "imdb://image.rating", "value": 8.7},
                            ],
                            "imdbRatingCount": 2468000,
                            "Guid": [{"id": "tmdb://550"}, {"id": "imdb://tt0133093"}],
                        }
                    ]
                }
            },
        )

    async def run():
        client = PlexDiscoverClient("test-token", transport=httpx.MockTransport(handler))
        try:
            return await client.get_rating(550, "movie")
        finally:
            await client.close()

    rating = asyncio.run(run())
    assert rating is not None
    assert rating.imdb_id == "tt0133093"
    assert rating.rating == 8.7
    assert rating.votes == 2468000


def test_plex_discover_uses_show_match_type() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["type"] == "2"
        assert request.url.params["guid"] == "tmdb://1399"
        return httpx.Response(200, json={"MediaContainer": {"Metadata": [{"Rating": [{"image": "imdb://rating", "value": 9.1}]}]}})

    async def run():
        client = PlexDiscoverClient("test-token", transport=httpx.MockTransport(handler))
        try:
            return await client.get_rating(1399, "show")
        finally:
            await client.close()

    rating = asyncio.run(run())
    assert rating is not None
    assert rating.rating == 9.1


def test_rating_lookup_uses_stored_token_when_local_plex_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def configured_plex(_db: object) -> SimpleNamespace:
        return SimpleNamespace(enabled=False, token="stored-token")

    class FakePlexDiscoverClient:
        def __init__(self, token: str, *, timeout: float) -> None:
            assert token == "stored-token"
            assert timeout == 5.0

        async def get_rating(self, tmdb_id: int, kind: str) -> PlexExternalRating:
            assert tmdb_id == 157336
            assert kind == "movie"
            return PlexExternalRating(imdb_id="tt0816692", rating=8.7, votes=2400000)

        async def close(self) -> None:
            return None

    monkeypatch.setattr(plex_discover_service, "get_configured_plex", configured_plex)
    monkeypatch.setattr(plex_discover_service, "PlexDiscoverClient", FakePlexDiscoverClient)

    ratings = asyncio.run(
        plex_discover_service.lookup_tmdb_ratings(object(), [157336], "movie")  # type: ignore[arg-type]
    )

    assert ratings[157336] == PlexExternalRating(
        imdb_id="tt0816692",
        rating=8.7,
        votes=2400000,
    )
