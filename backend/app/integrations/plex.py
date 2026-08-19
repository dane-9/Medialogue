from __future__ import annotations

from dataclasses import dataclass
import posixpath
from xml.etree import ElementTree

import httpx


class PlexError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PlexMediaMatch:
    rating_key: str
    title: str
    year: int | None
    edition: str | None
    file_path: str
    show_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None


@dataclass(frozen=True, slots=True)
class PlexTitleMatch:
    rating_key: str
    title: str
    year: int | None
    edition: str | None


class PlexClient:
    """Read-only Plex adapter; no mutation or scan methods are exposed."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-Plex-Token": token, "Accept": "application/xml"},
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, str]:
        response = await self._get("/identity")
        root = ElementTree.fromstring(response.content)
        return {"status": "healthy", "machine_identifier": root.attrib.get("machineIdentifier", "")}

    async def find_exact_path(self, file_path: str) -> PlexMediaMatch | None:
        requested = _canonical_path(file_path)
        sections = ElementTree.fromstring((await self._get("/library/sections")).content)
        for directory in sections.findall("Directory"):
            key = directory.attrib.get("key")
            if not key:
                continue
            response = await self._get(f"/library/sections/{key}/all")
            for video in ElementTree.fromstring(response.content).findall("Video"):
                for part in video.findall("./Media/Part"):
                    path = part.attrib.get("file")
                    if path and _canonical_path(path) == requested:
                        year = video.attrib.get("year")
                        return PlexMediaMatch(
                            rating_key=video.attrib.get("ratingKey", ""),
                            title=video.attrib.get("title", ""),
                            year=int(year) if year and year.isdigit() else None,
                            edition=video.attrib.get("editionTitle"),
                            file_path=path,
                            show_title=video.attrib.get("grandparentTitle"),
                            season_number=_safe_int(video.attrib.get("parentIndex")),
                            episode_number=_safe_int(video.attrib.get("index")),
                        )
        return None

    async def search_title_year(self, title: str, year: int | None) -> list[PlexTitleMatch]:
        response = await self._get("/hubs/search", params={"query": title, "limit": 50})
        matches: list[PlexTitleMatch] = []
        for video in ElementTree.fromstring(response.content).findall(".//Video"):
            plex_year = video.attrib.get("year")
            parsed_year = int(plex_year) if plex_year and plex_year.isdigit() else None
            if video.attrib.get("title", "").casefold() != title.casefold():
                continue
            if year is not None and parsed_year != year:
                continue
            matches.append(
                PlexTitleMatch(
                    rating_key=video.attrib.get("ratingKey", ""),
                    title=video.attrib.get("title", ""),
                    year=parsed_year,
                    edition=video.attrib.get("editionTitle"),
                )
            )
        return matches

    async def _get(self, path: str, **kwargs: object) -> httpx.Response:
        response = await self._client.get(path, **kwargs)
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PlexError(f"Plex request failed: {response.status_code}") from exc
        return response


def _canonical_path(path: str) -> str:
    """Compare Plex and local paths independent of slash representation.

    Plex normally reports POSIX paths, while local development and some
    remote-path configurations can expose Windows separators.  Keeping this
    normalization in the adapter avoids treating equivalent paths as two
    different media files without changing the paths persisted as evidence.
    """

    return posixpath.normpath(path.replace("\\", "/"))


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
