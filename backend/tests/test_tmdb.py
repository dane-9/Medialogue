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


def test_tmdb_candidate_credits_include_principal_people() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/movie/1091/credits":
            return httpx.Response(200, json={"crew": [{"job": "Director", "name": "John Carpenter"}], "cast": [{"name": "Kurt Russell"}, {"name": "Keith David"}, {"name": "Wilford Brimley"}, {"name": "Fourth Billing"}]})
        if request.url.path == "/3/tv/94997":
            assert request.url.params["append_to_response"] == "credits"
            return httpx.Response(200, json={"created_by": [{"name": "Example Creator"}], "credits": {"cast": [{"name": "Lead One"}, {"name": "Lead Two"}]}})
        raise AssertionError(f"unexpected TMDB request: {request.url}")

    async def run():
        client = TMDBClient("test-key", transport=httpx.MockTransport(handler))
        try:
            return await client.get_movie_credits(1091), await client.get_show_credits(94997)
        finally:
            await client.close()

    movie, show = asyncio.run(run())
    assert movie == ("John Carpenter", ("Kurt Russell", "Keith David", "Wilford Brimley"))
    assert show == ("Example Creator", ("Lead One", "Lead Two"))


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


def test_identity_title_normalization_treats_common_punctuation_variants_as_equal() -> None:
    from app.core.identity import normalize_identity_title

    assert normalize_identity_title("Oliver & Company") == normalize_identity_title("Oliver and Company")
    assert normalize_identity_title("Wallace & Gromit: The Curse of the Were-Rabbit") == normalize_identity_title(
        "Wallace and Gromit The Curse of the Were-Rabbit"
    )
    assert normalize_identity_title("The Adventures of Ichabod and Mr. Toad") == normalize_identity_title(
        "The Adventures of Ichabod and Mr Toad"
    )


def test_movie_identity_selection_accepts_ampersand_and_punctuation_variants() -> None:
    from app.integrations.tmdb import TMDBMovieMatch
    from app.services.tmdb import _select_movie_identity

    match, reason, evidence = _select_movie_identity(
        "Oliver and Company",
        1988,
        [TMDBMovieMatch(12233, "Oliver & Company", "Oliver & Company", 1988, None, None)],
    )

    assert reason == "matched"
    assert match is not None and match.tmdb_id == 12233
    assert len(evidence) == 1


def test_show_identity_prefers_a_unique_display_title_over_original_name_aliases() -> None:
    from app.integrations.tmdb import TMDBShowMatch
    from app.services.tmdb import _select_show_identity

    intended = TMDBShowMatch(63404, "Taskmaster", "Taskmaster", 2015, "The original series.", "/uk.jpg")
    localized = TMDBShowMatch(195531, "Taskmaster Portugal", "Taskmaster", 2022, "Portuguese edition.", "/pt.jpg")

    match, reason, evidence = _select_show_identity("Taskmaster", None, [intended, localized])

    assert reason == "matched"
    assert match is intended
    assert evidence == (intended,)


def test_show_identity_keeps_real_same_title_editions_ambiguous_without_a_year() -> None:
    from app.integrations.tmdb import TMDBShowMatch
    from app.services.tmdb import _select_show_identity

    us = TMDBShowMatch(2316, "The Office", "The Office", 2005, "Scranton.", "/us.jpg")
    uk = TMDBShowMatch(2996, "The Office", "The Office", 2001, "Slough.", "/uk.jpg")

    match, reason, evidence = _select_show_identity("The Office", None, [us, uk])

    assert match is None
    assert reason == "ambiguous"
    assert evidence == (us, uk)

    match, reason, _ = _select_show_identity("The Office", 2005, [us, uk])
    assert reason == "matched"
    assert match is us


def test_show_identity_ignores_an_empty_shadow_row_behind_a_complete_exact_result() -> None:
    from app.integrations.tmdb import TMDBShowMatch
    from app.services.tmdb import _select_show_identity

    series = TMDBShowMatch(1417, "Glee", "Glee", 2009, "A musical comedy.", "/glee.jpg")
    shadow = TMDBShowMatch(272153, "Glee", "Glee", None, None, None)

    match, reason, evidence = _select_show_identity("Glee", None, [series, shadow])

    assert reason == "matched"
    assert match is series
    assert evidence == (series, shadow)


def test_movie_alternative_title_can_resolve_home_video_alias() -> None:
    from app.integrations.tmdb import TMDBMovieMatch
    from app.services.tmdb import _select_movie_by_alternative_title

    class FakeClient:
        async def get_movie_alternative_titles(self, tmdb_id: int):
            assert tmdb_id == 36972
            return ("Scooby-Doo Goes Hollywood",)

    candidate = TMDBMovieMatch(36972, "Scooby Goes Hollywood", "Scooby Goes Hollywood", 1979, None, None)
    match, reason = asyncio.run(
        _select_movie_by_alternative_title(FakeClient(), "Scooby Doo Goes Hollywood", 1979, [candidate])
    )
    assert reason == "matched"
    assert match is candidate
