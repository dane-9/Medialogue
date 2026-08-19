from __future__ import annotations

import asyncio

import httpx

from app.integrations.tmdb import TMDBClient, TMDBError


def test_tmdb_movie_search_maps_identity_and_metadata() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/3/search/movie"
        assert request.url.params["query"] == "Inception"
        assert request.url.params["year"] == "2010"
        assert request.url.params["api_key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 27205,
                        "title": "Inception",
                        "original_title": "Inception",
                        "release_date": "2010-07-15",
                        "overview": "A test overview.",
                        "poster_path": "/poster.jpg",
                    }
                ]
            },
        )

    async def run():
        client = TMDBClient("test-key", transport=httpx.MockTransport(handler))
        try:
            matches = await client.search_movie("Inception", 2010)
        finally:
            await client.close()
        return matches

    matches = asyncio.run(run())
    assert len(matches) == 1
    assert matches[0].tmdb_id == 27205
    assert matches[0].title == "Inception"
    assert matches[0].year == 2010
    assert matches[0].poster_path == "/poster.jpg"


def test_tmdb_health_surfaces_http_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"status_message": "Invalid API key"})

    async def run():
        client = TMDBClient("bad-key", transport=httpx.MockTransport(handler))
        try:
            try:
                await client.health()
            except TMDBError as exc:
                return str(exc)
        finally:
            await client.close()
        raise AssertionError("TMDB health unexpectedly succeeded")

    assert "401" in asyncio.run(run())


def test_tmdb_show_search_details_and_episode_metadata() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/tv":
            assert request.url.params["query"] == "Dollface"
            assert request.url.params["first_air_date_year"] == "2019"
            return httpx.Response(200, json={"results": [{"id": 194764, "name": "Dollface", "original_name": "Dollface", "first_air_date": "2019-11-15", "overview": "Show overview", "poster_path": "/show.jpg"}]})
        if request.url.path == "/3/tv/194764":
            assert request.url.params["append_to_response"] == "external_ids"
            return httpx.Response(200, json={"id": 194764, "name": "Dollface", "original_name": "Dollface", "first_air_date": "2019-11-15", "overview": "Show overview", "poster_path": "/show.jpg", "external_ids": {"tvdb_id": 361563}, "seasons": [{"season_number": 1, "name": "Season 1", "episode_count": 2, "air_date": "2019-11-15", "poster_path": "/s1.jpg"}]})
        if request.url.path == "/3/tv/194764/season/1":
            return httpx.Response(200, json={"episodes": [{"id": 1001, "season_number": 1, "episode_number": 1, "name": "Guy's Girl", "air_date": "2019-11-15", "overview": "Episode overview"}]})
        raise AssertionError(f"unexpected TMDB request: {request.url}")

    async def run():
        client = TMDBClient("test-key", transport=httpx.MockTransport(handler))
        try:
            matches = await client.search_show("Dollface", 2019)
            details = await client.get_show(194764)
            episodes = await client.get_season(194764, 1)
            return matches, details, episodes
        finally:
            await client.close()

    matches, details, episodes = asyncio.run(run())
    assert matches[0].tmdb_id == 194764
    assert matches[0].year == 2019
    assert details.tvdb_id == 361563
    assert details.seasons[0].episode_count == 2
    assert episodes[0].episode_number == 1
    assert episodes[0].title == "Guy's Girl"
