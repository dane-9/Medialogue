from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
import httpx

from app.parser.iso_languages import resolve_languages

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
MAX_TORRENT_BYTES: Final = 10 * 1024 * 1024
MAX_XML_BYTES: Final = 5 * 1024 * 1024
MAX_REDIRECTS: Final = 3


class TorznabError(RuntimeError):
    pass


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    scheme = url.scheme.lower()
    host = (url.host or "").lower()
    if scheme not in {"http", "https"} or not host:
        raise TorznabError("Configured Torznab URL must use HTTP or HTTPS.")
    return scheme, host, url.port or (443 if scheme == "https" else 80)


@dataclass(frozen=True, slots=True)
class SearchResult:
    guid: str
    title: str
    download_url: str | None
    size: int | None
    seeders: int | None
    published: str | None
    leechers: int | None = None
    #: Languages the indexer declared. Authoritative over anything parsed from
    #: the title, because the tracker knows what it is serving.
    languages: tuple[str, ...] = ()


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
        try:
            self._base_url = httpx.URL(url.rstrip("/"))
            self.trusted_origin = _origin(self._base_url)
        except (TypeError, ValueError) as exc:
            raise TorznabError("Configured Torznab URL is invalid.") from exc
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    async def _read_bounded(response: httpx.Response, limit: int, message: str) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    raise TorznabError(message)
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise TorznabError(message)
            chunks.append(chunk)
        return b"".join(chunks)

    def resolve_download_url(self, download_url: str, *, base_url: httpx.URL | None = None) -> httpx.URL:
        """Resolve a result/redirect URL and require the configured origin."""

        base = base_url or self._base_url
        try:
            resolved = base.join(download_url)
            candidate_origin = _origin(resolved)
        except (TypeError, ValueError, TorznabError) as exc:
            raise TorznabError("Indexer download URL points to an untrusted origin.") from exc
        if candidate_origin != self.trusted_origin:
            raise TorznabError("Indexer download URL points to an untrusted origin.")
        return resolved

    async def health(self) -> dict[str, str | None]:
        try:
            async with self._client.stream("GET", "", params={"apikey": self.api_key, "t": "caps"}) as response:
                response.raise_for_status()
                payload = await self._read_bounded(
                    response,
                    MAX_XML_BYTES,
                    "Torznab capabilities response exceeded the maximum size.",
                )
        except httpx.HTTPError as exc:
            raise TorznabError(f"Torznab request failed: {_describe_http_error(exc)}") from exc
        try:
            root = ElementTree.fromstring(payload)
        except (ElementTree.ParseError, DefusedXmlException) as exc:
            raise TorznabError("Torznab capabilities response was not valid XML.") from exc
        _raise_for_error_document(root)
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
        categories: tuple[int, ...] = (),
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
        # Torznab categories are positive; a stored 0 (once saved for a blank
        # field) filtered every search down to nothing.
        categories = tuple(value for value in categories if value > 0)
        if categories:
            params["cat"] = ",".join(str(value) for value in categories)
        try:
            async with self._client.stream("GET", "", params=params) as response:
                response.raise_for_status()
                payload = await self._read_bounded(
                    response,
                    MAX_XML_BYTES,
                    "Torznab search response exceeded the maximum size.",
                )
        except httpx.HTTPError as exc:
            raise TorznabError(f"Torznab search failed: {_describe_http_error(exc)}") from exc
        try:
            root = ElementTree.fromstring(payload)
        except (ElementTree.ParseError, DefusedXmlException) as exc:
            raise TorznabError("Torznab search response was not valid XML.") from exc
        _raise_for_error_document(root)
        if root.find("channel") is None:
            raise TorznabError(f"Torznab search returned an unexpected <{root.tag}> document instead of results.")
        return [self._parse_item(item) for item in root.findall("./channel/item")[:2000]]

    async def fetch_torrent(self, download_url: str) -> bytes:
        current_url = self.resolve_download_url(download_url)
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                async with self._client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise TorznabError("Indexer returned an invalid torrent redirect.")
                        if redirect_count >= MAX_REDIRECTS:
                            raise TorznabError("Indexer returned too many torrent redirects.")
                        current_url = self.resolve_download_url(location, base_url=current_url)
                        continue
                    response.raise_for_status()
                    payload = await self._read_bounded(
                        response,
                        MAX_TORRENT_BYTES,
                        "Indexer torrent response exceeded the maximum size.",
                    )
            except TorznabError:
                raise
            except httpx.HTTPError as exc:
                raise TorznabError("Torrent download failed.") from exc
            if not payload:
                raise TorznabError("Indexer returned an empty torrent payload")
            return payload
        raise TorznabError("Indexer returned too many torrent redirects.")

    @staticmethod
    def _parse_item(item: ElementTree.Element) -> SearchResult:
        attrs = {
            node.attrib.get("name"): node.attrib.get("value")
            for node in item.findall(f"{{{TORZNAB_NS}}}attr")
        }
        language_values = [
            node.attrib.get("value") or ""
            for node in item.findall(f"{{{TORZNAB_NS}}}attr")
            if (node.attrib.get("name") or "").lower() == "language"
        ]
        if not language_values:
            language_values = [element.text or "" for element in item.findall("language")]
        languages: list[str] = []
        for value in language_values:
            for name in resolve_languages(value):
                if name not in languages:
                    languages.append(name)
        enclosure = item.find("enclosure")
        size_raw = attrs.get("size") or (enclosure.attrib.get("length") if enclosure is not None else None)
        seeders_raw = attrs.get("seeders")
        seeders = _safe_int(seeders_raw)
        leechers = _safe_int(attrs.get("leechers"))
        if leechers is None:
            peers = _safe_int(attrs.get("peers"))
            if peers is not None and seeders is not None:
                leechers = max(peers - seeders, 0)
        return SearchResult(
            guid=item.findtext("guid") or item.findtext("link") or item.findtext("title") or "",
            title=item.findtext("title") or "",
            download_url=(enclosure.attrib.get("url") if enclosure is not None else item.findtext("link")),
            size=_safe_int(size_raw),
            seeders=seeders,
            leechers=leechers,
            languages=tuple(languages),
            published=item.findtext("pubDate"),
        )


def _raise_for_error_document(root: ElementTree.Element) -> None:
    """Torznab reports failures as a 200 response holding <error code description/>.

    Read as a feed it has no items, so a rejected API key or a tracker error
    looked exactly like "searched, nothing found".
    """

    if root.tag == "error":
        code = root.attrib.get("code") or "?"
        description = root.attrib.get("description") or "no description"
        raise TorznabError(f"Indexer returned error {code}: {description}")


def _describe_http_error(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
