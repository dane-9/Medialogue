from __future__ import annotations

import asyncio

import httpx

from app.integrations.plex_discover import PlexDiscoverClient


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
