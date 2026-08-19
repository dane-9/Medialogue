from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree

import httpx

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"


class TorznabError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SearchResult:
    guid: str
    title: str
    download_url: str | None
    size: int | None
    seeders: int | None
    published: str | None


class TorznabClient:
    """Minimal async Torznab adapter for individually configured Prowlarr endpoints."""

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, str | None]:
        response = await self._client.get("", params={"apikey": self.api_key, "t": "caps"})
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TorznabError(f"Torznab request failed: HTTP {response.status_code}") from exc
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise TorznabError("Torznab capabilities response was not valid XML") from exc
        server = root.find("server")
        return {
            "status": "healthy",
            "title": server.attrib.get("title") if server is not None else None,
        }

    async def search(
        self,
        query: str,
        *,
        media_type: str,
        tmdb_id: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        params: dict[str, str | int] = {
            "apikey": self.api_key,
            "t": "movie" if media_type == "movies" else "tvsearch",
            "q": query,
        }
        if tmdb_id is not None:
            params["tmdbid"] = tmdb_id
        if season is not None:
            params["season"] = season
        if episode is not None:
            params["ep"] = episode
        response = await self._client.get("", params=params)
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TorznabError(f"Torznab search failed: HTTP {response.status_code}") from exc
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise TorznabError("Torznab search response was not valid XML") from exc
        return [self._parse_item(item) for item in root.findall("./channel/item")]

    async def fetch_torrent(self, download_url: str) -> bytes:
        response = await self._client.get(download_url)
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TorznabError(f"Torrent download failed: HTTP {response.status_code}") from exc
        payload = response.content
        if not payload:
            raise TorznabError("Indexer returned an empty torrent payload")
        return payload

    @staticmethod
    def _parse_item(item: ElementTree.Element) -> SearchResult:
        attrs = {
            node.attrib.get("name"): node.attrib.get("value")
            for node in item.findall(f"{{{TORZNAB_NS}}}attr")
        }
        enclosure = item.find("enclosure")
        size_raw = attrs.get("size") or (enclosure.attrib.get("length") if enclosure is not None else None)
        seeders_raw = attrs.get("seeders")
        return SearchResult(
            guid=item.findtext("guid") or item.findtext("link") or item.findtext("title") or "",
            title=item.findtext("title") or "",
            download_url=(enclosure.attrib.get("url") if enclosure is not None else item.findtext("link")),
            size=_safe_int(size_raw),
            seeders=_safe_int(seeders_raw),
            published=item.findtext("pubDate"),
        )


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
