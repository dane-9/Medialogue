from __future__ import annotations

from dataclasses import dataclass, field
import posixpath
from pathlib import PurePosixPath
from xml.etree import ElementTree

import httpx

from app.core.identity import normalize_identity_title


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


@dataclass(frozen=True, slots=True)
class PlexLibrarySnapshot:
    """Read-only in-memory view of Plex library paths for one sync cycle.

    The lookup indexes are built once so verifying a large library remains
    effectively O(local media) instead of repeatedly scanning every Plex Part
    for every Movie and Episode.
    """

    items: tuple[PlexMediaMatch, ...]
    _by_path: dict[str, PlexMediaMatch] = field(init=False, repr=False)
    _by_basename: dict[str, tuple[PlexMediaMatch, ...]] = field(init=False, repr=False)
    _movies_by_title: dict[str, tuple[PlexTitleMatch, ...]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        by_path: dict[str, PlexMediaMatch] = {}
        by_basename: dict[str, list[PlexMediaMatch]] = {}
        movies_by_title: dict[str, list[PlexTitleMatch]] = {}
        for item in self.items:
            canonical = _canonical_path(item.file_path)
            by_path.setdefault(canonical, item)
            by_basename.setdefault(PurePosixPath(canonical).name.casefold(), []).append(item)
            # Episodes use grandparentTitle for the Show and must not be
            # treated as Movie title matches.
            if item.show_title is None:
                movies_by_title.setdefault(normalize_identity_title(item.title), []).append(
                    PlexTitleMatch(
                        rating_key=item.rating_key,
                        title=item.title,
                        year=item.year,
                        edition=item.edition,
                    )
                )
        object.__setattr__(self, "_by_path", by_path)
        object.__setattr__(self, "_by_basename", {key: tuple(values) for key, values in by_basename.items()})
        object.__setattr__(
            self,
            "_movies_by_title",
            {key: tuple(values) for key, values in movies_by_title.items()},
        )

    def find_exact_path(
        self,
        file_path: str,
        *,
        local_root: str | None = None,
        media_type: str | None = None,
    ) -> PlexMediaMatch | None:
        """Find the same physical media even when Docker mount prefixes differ.

        Plex reports the path visible inside the Plex container, while Medialogue
        stores the path visible inside its own container.  Exact absolute paths
        are preferred.  If those differ and the caller supplies the configured
        Medialogue storage root, compare the root-relative path instead.  A
        relative match is accepted only when it is unique in Plex, so two Plex
        libraries with the same tail path remain unresolved instead of being
        guessed.
        """

        canonical = _canonical_path(file_path)
        exact = self._by_path.get(canonical)
        if exact is not None or not local_root:
            return exact

        try:
            relative = PurePosixPath(canonical).relative_to(PurePosixPath(_canonical_path(local_root)))
        except ValueError:
            return None
        if not relative.parts:
            return None

        expected_parts = tuple(part.casefold() for part in relative.parts)
        candidates: list[PlexMediaMatch] = []
        for item in self._by_basename.get(relative.name.casefold(), ()):
            if media_type == "movies" and item.show_title is not None:
                continue
            if media_type == "shows" and item.show_title is None:
                continue
            remote_parts = tuple(part.casefold() for part in PurePosixPath(_canonical_path(item.file_path)).parts)
            if len(remote_parts) >= len(expected_parts) and remote_parts[-len(expected_parts):] == expected_parts:
                candidates.append(item)

        unique = {_canonical_path(item.file_path): item for item in candidates}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def search_title_year(self, title: str, year: int | None) -> list[PlexTitleMatch]:
        matches = self._movies_by_title.get(normalize_identity_title(title), ())
        if year is None:
            return list(matches)
        return [item for item in matches if item.year == year]


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

    async def _section_media(self, key: str, section_type: str | None) -> httpx.Response | None:
        """Fetch physical Movie or Episode rows for a Plex library section.

        A Plex Show section's plain ``/all`` endpoint enumerates Show
        directories, not Episode ``Video`` rows with ``Media/Part`` paths.
        Requesting metadata type 4 is therefore required for exact-path TV
        verification. Movie sections use type 1. Other library types are not
        relevant to Medialogue and are skipped.
        """

        normalized = (section_type or "").casefold()
        if normalized == "show":
            return await self._get(f"/library/sections/{key}/all", params={"type": 4})
        if normalized == "movie":
            return await self._get(f"/library/sections/{key}/all", params={"type": 1})
        # Some test/minimal Plex responses omit section type. Keeping the
        # default request makes those servers usable while real Plex responses
        # use the explicit Movie/Episode filters above.
        if not normalized:
            return await self._get(f"/library/sections/{key}/all")
        return None

    async def library_snapshot(self) -> PlexLibrarySnapshot:
        """Fetch each Plex library section once and index every physical Part.

        This is intentionally read-only and does not request a Plex library
        scan. A Medialogue-wide verification cycle can therefore compare
        thousands of local paths without re-downloading every Plex section for
        every individual title.
        """

        sections = ElementTree.fromstring((await self._get("/library/sections")).content)
        items: list[PlexMediaMatch] = []
        for directory in sections.findall("Directory"):
            key = directory.attrib.get("key")
            if not key:
                continue
            response = await self._section_media(key, directory.attrib.get("type"))
            if response is None:
                continue
            for video in ElementTree.fromstring(response.content).findall(".//Video"):
                year = video.attrib.get("year")
                parsed_year = int(year) if year and year.isdigit() else None
                for part in video.findall("./Media/Part"):
                    path = part.attrib.get("file")
                    if not path:
                        continue
                    items.append(
                        PlexMediaMatch(
                            rating_key=video.attrib.get("ratingKey", ""),
                            title=video.attrib.get("title", ""),
                            year=parsed_year,
                            edition=video.attrib.get("editionTitle"),
                            file_path=path,
                            show_title=video.attrib.get("grandparentTitle"),
                            season_number=_safe_int(video.attrib.get("parentIndex")),
                            episode_number=_safe_int(video.attrib.get("index")),
                        )
                    )
        return PlexLibrarySnapshot(items=tuple(items))

    async def find_exact_path(self, file_path: str) -> PlexMediaMatch | None:
        requested = _canonical_path(file_path)
        sections = ElementTree.fromstring((await self._get("/library/sections")).content)
        for directory in sections.findall("Directory"):
            key = directory.attrib.get("key")
            if not key:
                continue
            response = await self._section_media(key, directory.attrib.get("type"))
            if response is None:
                continue
            for video in ElementTree.fromstring(response.content).findall(".//Video"):
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
            if normalize_identity_title(video.attrib.get("title", "")) != normalize_identity_title(title):
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
