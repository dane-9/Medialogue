"""Structured, versioned release parser data types.

The parser deliberately keeps the values that are useful to the application
separate: a quality is not an edition, a provider is not a quality, and
attributes such as ``Hybrid`` are not editions.  The classes in this module
are intentionally plain dataclasses so they can be used by the scanner,
search adapters, API schemas, and tests without coupling the parser to the
database layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterator


PARSER_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class IdentityInfo:
    """Identity boundary information extracted from a release name."""

    title_candidate: str | None = None
    year: int | None = None
    season: int | None = None
    episodes: tuple[int, ...] = ()
    episode_title: str | None = None

    # Friendly aliases used by callers that do not need the word candidate.
    @property
    def title(self) -> str | None:
        return self.title_candidate

    @property
    def episode(self) -> int | None:
        return self.episodes[0] if self.episodes else None

    @property
    def episode_numbers(self) -> tuple[int, ...]:
        return self.episodes

    @property
    def season_number(self) -> int | None:
        return self.season

    @property
    def is_tv(self) -> bool:
        return self.season is not None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["episodes"] = list(self.episodes)
        value["title"] = self.title_candidate
        return value


@dataclass(frozen=True, slots=True)
class QualityInfo:
    """Canonical quality and the evidence used to classify it."""

    canonical: str | None = None
    resolution: str | None = None
    source: str | None = None
    modifier: str | None = None
    disc_type: str | None = None
    tv_standard: str | None = None
    raw_tokens: tuple[str, ...] = ()

    @property
    def name(self) -> str | None:
        return self.canonical

    def __str__(self) -> str:
        return self.canonical or ""

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["raw_tokens"] = list(self.raw_tokens)
        value["name"] = self.canonical
        return value


@dataclass(frozen=True, slots=True)
class ReleaseAttributes:
    """Known name-derived release attributes.

    ``values`` retains canonical display names and the boolean fields make
    common conditions cheap and obvious.  It also behaves like a small
    collection for compatibility with callers that use ``"Hybrid" in``.
    """

    values: tuple[str, ...] = ()
    hybrid: bool = False
    repack: bool = False
    proper: bool = False
    real: bool = False
    internal: bool = False
    limited: bool = False

    def __contains__(self, value: object) -> bool:
        return any(str(value).casefold() == item.casefold() for item in self.values)

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __getitem__(self, key: str) -> Any:
        if key == "values":
            return list(self.values)
        return bool(getattr(self, key))

    def get(self, value: str, default: bool = False) -> bool:
        return value.casefold() in {item.casefold() for item in self.values} or default

    def as_list(self) -> list[str]:
        return list(self.values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": list(self.values),
            "hybrid": self.hybrid,
            "repack": self.repack,
            "proper": self.proper,
            "real": self.real,
            "internal": self.internal,
            "limited": self.limited,
        }


@dataclass(frozen=True, slots=True)
class HDRInfo:
    dolby_vision: bool = False
    hdr: bool = False
    hdr10: bool = False
    hdr10_plus: bool = False
    hlg: bool = False

    @property
    def values(self) -> tuple[str, ...]:
        result: list[str] = []
        if self.dolby_vision:
            result.append("Dolby Vision")
        if self.hdr:
            result.append("HDR")
        if self.hdr10:
            result.append("HDR10")
        if self.hdr10_plus:
            result.append("HDR10+")
        if self.hlg:
            result.append("HLG")
        return tuple(result)

    def __contains__(self, value: object) -> bool:
        return str(value).casefold() in {item.casefold() for item in self.values}

    def __getitem__(self, key: str) -> Any:
        if key == "values":
            return list(self.values)
        return bool(getattr(self, key))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["values"] = list(self.values)
        return value


@dataclass(frozen=True, slots=True)
class VideoInfo:
    codec: str | None = None
    raw_codec_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AudioInfo:
    codec: str | None = None
    channels: str | None = None
    atmos: bool = False
    dts_x: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReleaseParseResult:
    """Complete parser output.

    ``to_dict`` is stable JSON-friendly output intended for parser snapshots.
    The parser version is part of every result so historical snapshots remain
    interpretable when the grammar evolves.
    """

    raw_name: str
    normalized_name: str
    identity: IdentityInfo = field(default_factory=IdentityInfo)
    quality: QualityInfo = field(default_factory=QualityInfo)
    edition: str | None = None
    provider: str | None = None
    release_group: str = "NoGroup"
    attributes: ReleaseAttributes = field(default_factory=ReleaseAttributes)
    hdr: HDRInfo = field(default_factory=HDRInfo)
    video: VideoInfo = field(default_factory=VideoInfo)
    audio: AudioInfo = field(default_factory=AudioInfo)
    languages: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    unknown_tokens: tuple[str, ...] = ()
    parser_version: str = PARSER_VERSION
    warnings: tuple[str, ...] = ()

    # Flat convenience properties are useful in small integrations and make
    # the result pleasant to inspect in a debugger.
    @property
    def title(self) -> str | None:
        return self.identity.title_candidate

    @property
    def year(self) -> int | None:
        return self.identity.year

    @property
    def season(self) -> int | None:
        return self.identity.season

    @property
    def episodes(self) -> tuple[int, ...]:
        return self.identity.episodes

    @property
    def resolution(self) -> str | None:
        return self.quality.resolution

    @property
    def quality_name(self) -> str | None:
        return self.quality.canonical

    @property
    def video_codec(self) -> str | None:
        return self.video.codec

    @property
    def audio_codec(self) -> str | None:
        return self.audio.codec

    @property
    def channels(self) -> str | None:
        return self.audio.channels

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_name": self.raw_name,
            "raw": self.raw_name,
            "normalized_name": self.normalized_name,
            "identity": self.identity.to_dict(),
            "quality": self.quality.to_dict(),
            "edition": self.edition,
            "provider": self.provider,
            "web_provider": self.provider,
            "release_group": self.release_group,
            "attributes": self.attributes.to_dict(),
            "hdr": self.hdr.to_dict(),
            "video": self.video.to_dict(),
            "audio": self.audio.to_dict(),
            "languages": list(self.languages),
            "regions": list(self.regions),
            "unknown_tokens": list(self.unknown_tokens),
            "parser_version": self.parser_version,
            "warnings": list(self.warnings),
            # These aliases keep API consumers from needing to know the
            # internal nesting while preserving the canonical nested model.
            "title": self.title,
            "year": self.year,
            "season": self.season,
            "episodes": list(self.episodes),
            "quality_name": self.quality_name,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "channels": self.channels,
        }

    def dict(self) -> dict[str, Any]:
        """Pydantic-like convenience used by API code and tests."""

        return self.to_dict()

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.to_dict()

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        import json

        return json.dumps(self.to_dict(), **kwargs)

    def json(self, *args: Any, **kwargs: Any) -> str:
        return self.model_dump_json(*args, **kwargs)

    def jsonable(self) -> dict[str, Any]:
        return self.to_dict()
