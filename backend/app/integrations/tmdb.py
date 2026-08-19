from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx


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


@dataclass(frozen=True, slots=True)
class TMDBShowMatch:
    tmdb_id: int
    title: str
    original_title: str | None
    year: int | None
    overview: str | None
    poster_path: str | None


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
        payload = (await self._get("/search/movie", params=params)).json()
        matches: list[TMDBMovieMatch] = []
        for item in payload.get("results", []):
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
                )
            )
        return matches

    async def get_movie(self, tmdb_id: int) -> TMDBMovieMatch:
        payload = (await self._get(f"/movie/{tmdb_id}")).json()
        title = payload.get("title")
        if not isinstance(title, str):
            raise TMDBError("TMDB movie response did not include a title")
        return TMDBMovieMatch(
            tmdb_id=int(payload.get("id") or tmdb_id),
            title=title,
            original_title=payload.get("original_title") if isinstance(payload.get("original_title"), str) else None,
            year=_year(payload.get("release_date")),
            overview=payload.get("overview") if isinstance(payload.get("overview"), str) else None,
            poster_path=payload.get("poster_path") if isinstance(payload.get("poster_path"), str) else None,
        )

    async def search_show(self, title: str, year: int | None = None) -> list[TMDBShowMatch]:
        params: dict[str, object] = {"query": title, "include_adult": "false"}
        if year is not None:
            params["first_air_date_year"] = year
        payload = (await self._get("/search/tv", params=params)).json()
        matches: list[TMDBShowMatch] = []
        for item in payload.get("results", []):
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
                )
            )
        return matches

    async def get_show(self, tmdb_id: int) -> TMDBShowDetails:
        payload = (await self._get(f"/tv/{tmdb_id}", params={"append_to_response": "external_ids"})).json()
        title = payload.get("name")
        if not isinstance(title, str):
            raise TMDBError("TMDB show response did not include a title")
        external = payload.get("external_ids") if isinstance(payload.get("external_ids"), dict) else {}
        seasons: list[TMDBSeasonMetadata] = []
        for item in payload.get("seasons", []):
            if not isinstance(item, dict) or not isinstance(item.get("season_number"), int):
                continue
            seasons.append(
                TMDBSeasonMetadata(
                    season_number=int(item["season_number"]),
                    title=item.get("name") if isinstance(item.get("name"), str) else None,
                    episode_count=int(item.get("episode_count") or 0),
                    air_date=_date(item.get("air_date")),
                    poster_path=item.get("poster_path") if isinstance(item.get("poster_path"), str) else None,
                )
            )
        return TMDBShowDetails(
            tmdb_id=int(payload.get("id") or tmdb_id),
            title=title,
            original_title=payload.get("original_name") if isinstance(payload.get("original_name"), str) else None,
            year=_year(payload.get("first_air_date")),
            overview=payload.get("overview") if isinstance(payload.get("overview"), str) else None,
            poster_path=payload.get("poster_path") if isinstance(payload.get("poster_path"), str) else None,
            tvdb_id=int(external["tvdb_id"]) if isinstance(external.get("tvdb_id"), int) else None,
            seasons=tuple(sorted(seasons, key=lambda item: item.season_number)),
        )

    async def get_season(self, tmdb_id: int, season_number: int) -> list[TMDBEpisodeMetadata]:
        payload = (await self._get(f"/tv/{tmdb_id}/season/{season_number}")).json()
        episodes: list[TMDBEpisodeMetadata] = []
        for item in payload.get("episodes", []):
            if not isinstance(item, dict) or not isinstance(item.get("episode_number"), int):
                continue
            episodes.append(
                TMDBEpisodeMetadata(
                    tmdb_id=int(item["id"]) if isinstance(item.get("id"), int) else None,
                    season_number=int(item.get("season_number") or season_number),
                    episode_number=int(item["episode_number"]),
                    title=item.get("name") if isinstance(item.get("name"), str) else None,
                    air_date=_date(item.get("air_date")),
                    overview=item.get("overview") if isinstance(item.get("overview"), str) else None,
                )
            )
        return sorted(episodes, key=lambda item: item.episode_number)

    async def _get(self, path: str, **kwargs: object) -> httpx.Response:
        response = await self._client.get(path, **kwargs)
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TMDBError(f"TMDB request failed: {response.status_code}") from exc
        return response


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
