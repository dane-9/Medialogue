from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx
from pydantic import TypeAdapter, ValidationError


class TMDBError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TMDBMovieMatch:
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str | None
    poster_path: str | None
    popularity: float | None = None
    vote_count: int | None = None
    vote_average: float | None = None
    original_language: str | None = None


@dataclass(frozen=True, slots=True)
class TMDBShowMatch:
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str | None
    poster_path: str | None
    popularity: float | None = None
    vote_count: int | None = None
    vote_average: float | None = None
    original_language: str | None = None


@dataclass(frozen=True, slots=True)
class TMDBSeasonMetadata:
    season_number: int
    title: str | None
    episode_count: int
    air_date: date | None = None
    poster_path: str | None = None


@dataclass(frozen=True, slots=True)
class TMDBEpisodeMetadata:
    tmdb_id: int | None
    season_number: int
    episode_number: int
    title: str | None
    air_date: date | None
    overview: str | None = None


EPISODE_GROUP_TYPES: dict[int, str] = {
    1: "Original air date",
    2: "Absolute",
    3: "DVD",
    4: "Digital",
    5: "Story arc",
    6: "Production",
    7: "TV",
}


@dataclass(frozen=True, slots=True)
class TMDBEpisodeGroupSummary:
    """One available ordering, without its episodes."""

    id: str
    name: str
    type: int
    group_count: int
    episode_count: int
    description: str | None = None
    network: str | None = None

    @property
    def type_label(self) -> str:
        return EPISODE_GROUP_TYPES.get(self.type, "Other")


@dataclass(frozen=True, slots=True)
class TMDBEpisodeGroup:
    """A full ordering: seasons in order, each holding episodes in order.

    The episodes are the same TMDB episodes as the default structure, the same
    ``tmdb_id`` values, just arranged differently. That is what makes switching
    an ordering a renumbering rather than a rebuild.
    """

    id: str
    name: str
    type: int
    seasons: tuple[tuple[int, str | None, tuple[TMDBEpisodeMetadata, ...]], ...]


@dataclass(frozen=True, slots=True)
class TMDBShowDetails:
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str | None
    poster_path: str | None
    tvdb_id: int | None
    seasons: tuple[TMDBSeasonMetadata, ...]
    original_language: str | None = None


class TMDBClient:
    """Read-only TMDB adapter used for identity and metadata verification."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.themoviedb.org/3",
            params={"api_key": api_key},
            headers={"Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, str]:
        await self._get("/configuration")
        return {"status": "healthy"}

    async def search_movie(self, title: str, year: int | None = None) -> list[TMDBMovieMatch]:
        params: dict[str, object] = {"query": title, "include_adult": "false"}
        if year is not None:
            params["year"] = year
        payload = _json_object(await self._get("/search/movie", params=params), "movie search")
        matches: list[TMDBMovieMatch] = []
        for item in _object_list(payload, "results", "movie search results"):
            tmdb_id = item.get("id")
            candidate_title = item.get("title")
            if not isinstance(tmdb_id, int) or not isinstance(candidate_title, str):
                continue
            matches.append(
                TMDBMovieMatch(
                    tmdb_id=tmdb_id,
                    title=candidate_title,
                    original_title=item.get("original_title") if isinstance(item.get("original_title"), str) else None,
                    year=_year(item.get("release_date")),
                    overview=item.get("overview") if isinstance(item.get("overview"), str) else None,
                    poster_path=item.get("poster_path") if isinstance(item.get("poster_path"), str) else None,
                    original_language=(
                        item.get("original_language")
                        if isinstance(item.get("original_language"), str)
                        else None
                    ),
                    popularity=_number(item.get("popularity")),
                    vote_count=_int(item.get("vote_count")),
                    vote_average=_number(item.get("vote_average")),
                )
            )
        return matches

    async def get_movie(self, tmdb_id: int) -> TMDBMovieMatch:
        payload = _json_object(await self._get(f"/movie/{tmdb_id}"), "movie details")
        title = payload.get("title")
        if not isinstance(title, str):
            raise TMDBError("TMDB movie response did not include a title")
        return TMDBMovieMatch(
            tmdb_id=_int(payload.get("id")) or tmdb_id,
            title=title,
            original_title=payload.get("original_title") if isinstance(payload.get("original_title"), str) else None,
            year=_year(payload.get("release_date")),
            overview=payload.get("overview") if isinstance(payload.get("overview"), str) else None,
            poster_path=payload.get("poster_path") if isinstance(payload.get("poster_path"), str) else None,
            original_language=(
                payload.get("original_language")
                if isinstance(payload.get("original_language"), str)
                else None
            ),
        )

    async def get_movie_credits(self, tmdb_id: int) -> tuple[str | None, tuple[str, ...]]:
        payload = _json_object(await self._get(f"/movie/{tmdb_id}/credits"), "movie credits")
        director = next(
            (
                item.get("name")
                for item in _object_list(payload, "crew", "movie crew")
                if isinstance(item, dict) and item.get("job") == "Director" and isinstance(item.get("name"), str)
            ),
            None,
        )
        cast = tuple(
            item["name"]
            for item in _object_list(payload, "cast", "movie cast")[:3]
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
        return director, cast

    async def get_movie_alternative_titles(self, tmdb_id: int) -> tuple[str, ...]:
        """Return TMDB's known alternate titles for a Movie.

        This is used only as a conservative identity fallback after the normal
        search result title/original-title comparison fails. It is especially
        useful for releases whose home-video name differs from TMDB's primary
        display title.
        """

        payload = _json_object(
            await self._get(f"/movie/{tmdb_id}/alternative_titles"),
            "movie alternative titles",
        )
        titles: list[str] = []
        for item in _object_list(payload, "titles", "movie alternative titles"):
            title = item.get("title")
            if isinstance(title, str) and title.strip() and title not in titles:
                titles.append(title.strip())
        return tuple(titles)

    async def search_show(self, title: str, year: int | None = None) -> list[TMDBShowMatch]:
        params: dict[str, object] = {"query": title, "include_adult": "false"}
        if year is not None:
            params["first_air_date_year"] = year
        payload = _json_object(await self._get("/search/tv", params=params), "show search")
        matches: list[TMDBShowMatch] = []
        for item in _object_list(payload, "results", "show search results"):
            tmdb_id = item.get("id")
            candidate_title = item.get("name")
            if not isinstance(tmdb_id, int) or not isinstance(candidate_title, str):
                continue
            matches.append(
                TMDBShowMatch(
                    tmdb_id=tmdb_id,
                    title=candidate_title,
                    original_title=item.get("original_name") if isinstance(item.get("original_name"), str) else None,
                    year=_year(item.get("first_air_date")),
                    overview=item.get("overview") if isinstance(item.get("overview"), str) else None,
                    poster_path=item.get("poster_path") if isinstance(item.get("poster_path"), str) else None,
                    original_language=(
                        item.get("original_language")
                        if isinstance(item.get("original_language"), str)
                        else None
                    ),
                    popularity=_number(item.get("popularity")),
                    vote_count=_int(item.get("vote_count")),
                    vote_average=_number(item.get("vote_average")),
                )
            )
        return matches

    async def get_show(self, tmdb_id: int) -> TMDBShowDetails:
        payload = _json_object(
            await self._get(f"/tv/{tmdb_id}", params={"append_to_response": "external_ids"}),
            "show details",
        )
        title = payload.get("name")
        if not isinstance(title, str):
            raise TMDBError("TMDB show response did not include a title")
        external = payload.get("external_ids") if isinstance(payload.get("external_ids"), dict) else {}
        seasons: list[TMDBSeasonMetadata] = []
        for item in _object_list(payload, "seasons", "show seasons"):
            if not isinstance(item.get("season_number"), int):
                continue
            seasons.append(
                TMDBSeasonMetadata(
                    season_number=int(item["season_number"]),
                    title=item.get("name") if isinstance(item.get("name"), str) else None,
                    episode_count=_int(item.get("episode_count")) or 0,
                    air_date=_date(item.get("air_date")),
                    poster_path=item.get("poster_path") if isinstance(item.get("poster_path"), str) else None,
                )
            )
        return TMDBShowDetails(
            tmdb_id=_int(payload.get("id")) or tmdb_id,
            title=title,
            original_title=payload.get("original_name") if isinstance(payload.get("original_name"), str) else None,
            year=_year(payload.get("first_air_date")),
            overview=payload.get("overview") if isinstance(payload.get("overview"), str) else None,
            poster_path=payload.get("poster_path") if isinstance(payload.get("poster_path"), str) else None,
            tvdb_id=int(external["tvdb_id"]) if isinstance(external.get("tvdb_id"), int) else None,
            seasons=tuple(sorted(seasons, key=lambda item: item.season_number)),
            original_language=(
                payload.get("original_language")
                if isinstance(payload.get("original_language"), str)
                else None
            ),
        )

    async def get_show_credits(self, tmdb_id: int) -> tuple[str | None, tuple[str, ...]]:
        payload = _json_object(
            await self._get(f"/tv/{tmdb_id}", params={"append_to_response": "credits"}),
            "show credits",
        )
        creator = next(
            (
                item.get("name")
                for item in _object_list(payload, "created_by", "show creators")
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ),
            None,
        )
        credits = _optional_object(payload.get("credits"), "show credits")
        cast = tuple(
            item["name"]
            for item in _object_list(credits, "cast", "show cast")[:3]
            if isinstance(item.get("name"), str)
        )
        return creator, cast

    async def get_season(self, tmdb_id: int, season_number: int) -> list[TMDBEpisodeMetadata]:
        payload = _json_object(await self._get(f"/tv/{tmdb_id}/season/{season_number}"), "season details")
        episodes: list[TMDBEpisodeMetadata] = []
        for item in _object_list(payload, "episodes", "season episodes"):
            if not isinstance(item.get("episode_number"), int):
                continue
            episodes.append(
                TMDBEpisodeMetadata(
                    tmdb_id=int(item["id"]) if isinstance(item.get("id"), int) else None,
                    season_number=_int(item.get("season_number")) or season_number,
                    episode_number=int(item["episode_number"]),
                    title=item.get("name") if isinstance(item.get("name"), str) else None,
                    air_date=_date(item.get("air_date")),
                    overview=item.get("overview") if isinstance(item.get("overview"), str) else None,
                )
            )
        return sorted(episodes, key=lambda item: item.episode_number)

    async def list_episode_groups(self, tmdb_id: int) -> list[TMDBEpisodeGroupSummary]:
        payload = _json_object(await self._get(f"/tv/{tmdb_id}/episode_groups"), "episode groups")
        groups: list[TMDBEpisodeGroupSummary] = []
        for item in _object_list(payload, "results", "episode group summaries"):
            if not isinstance(item.get("id"), str):
                continue
            network = item.get("network")
            groups.append(
                TMDBEpisodeGroupSummary(
                    id=item["id"],
                    name=item.get("name") if isinstance(item.get("name"), str) else "Unnamed group",
                    type=_int(item.get("type")) or 0,
                    group_count=_int(item.get("group_count")) or 0,
                    episode_count=_int(item.get("episode_count")) or 0,
                    description=item.get("description") if isinstance(item.get("description"), str) else None,
                    network=(
                        network.get("name")
                        if isinstance(network, dict) and isinstance(network.get("name"), str)
                        else None
                    ),
                )
            )
        return groups

    async def get_episode_group(self, group_id: str) -> TMDBEpisodeGroup:
        payload = _json_object(await self._get(f"/tv/episode_group/{group_id}"), "episode group")
        seasons: list[tuple[int, str | None, tuple[TMDBEpisodeMetadata, ...]]] = []
        raw_groups = _object_list(payload, "groups", "episode group seasons")
        raw_groups.sort(key=lambda item: item.get("order") if isinstance(item.get("order"), int) else 0)
        for index, group in enumerate(raw_groups):
            episodes: list[TMDBEpisodeMetadata] = []
            raw_episodes = _object_list(group, "episodes", "episode group episodes")
            raw_episodes.sort(key=lambda item: item.get("order") if isinstance(item.get("order"), int) else 0)
            season_number = group.get("order") if isinstance(group.get("order"), int) else index + 1
            for position, item in enumerate(raw_episodes, start=1):
                episodes.append(
                    TMDBEpisodeMetadata(
                        tmdb_id=int(item["id"]) if isinstance(item.get("id"), int) else None,
                        season_number=season_number,
                        episode_number=position,
                        title=item.get("name") if isinstance(item.get("name"), str) else None,
                        air_date=_date(item.get("air_date")),
                        overview=item.get("overview") if isinstance(item.get("overview"), str) else None,
                    )
                )
            seasons.append((
                season_number,
                group.get("name") if isinstance(group.get("name"), str) else None,
                tuple(episodes),
            ))
        return TMDBEpisodeGroup(
            id=str(payload.get("id") or group_id),
            name=payload.get("name") if isinstance(payload.get("name"), str) else "Unnamed group",
            type=_int(payload.get("type")) or 0,
            seasons=tuple(seasons),
        )

    async def _get(self, path: str, **kwargs: object) -> httpx.Response:
        response = await self._client.get(path, **kwargs)
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TMDBError(f"TMDB request failed: {response.status_code}") from exc
        return response


_JSON_OBJECT = TypeAdapter(dict[str, object])
_JSON_OBJECT_LIST = TypeAdapter(list[dict[str, object]])


def _json_object(response: httpx.Response, context: str) -> dict[str, object]:
    try:
        return _JSON_OBJECT.validate_python(response.json())
    except (ValueError, ValidationError) as exc:
        raise TMDBError(f"TMDB returned malformed {context} data") from exc


def _optional_object(value: object, context: str) -> dict[str, object]:
    if value is None:
        return {}
    try:
        return _JSON_OBJECT.validate_python(value)
    except ValidationError as exc:
        raise TMDBError(f"TMDB returned malformed {context} data") from exc


def _object_list(payload: dict[str, object], field: str, context: str) -> list[dict[str, object]]:
    try:
        return _JSON_OBJECT_LIST.validate_python(payload.get(field, []))
    except ValidationError as exc:
        raise TMDBError(f"TMDB returned malformed {context} data") from exc


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _year(value: object) -> int | None:
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
