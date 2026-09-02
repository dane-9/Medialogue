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
from typing import Any



@dataclass(frozen=True, slots=True)
class IdentityInfo:
    """Identity boundary information extracted from a release name."""

    title_candidate: str | None = None
    year: int | None = None
    season: int | None = None
    episodes: tuple[int, ...] = ()
    episode_title: str | None = None
    # A release may cover more than one season (for example ``S01-S04``).
    # ``season`` is the primary season; ``seasons`` is the complete set.
    seasons: tuple[int, ...] = ()
    alternate_titles: tuple[str, ...] = ()
    geographic: str | None = None

    @property
    def is_tv(self) -> bool:
        return self.season is not None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["episodes"] = list(self.episodes)
        value["seasons"] = list(self.seasons)
        value["alternate_titles"] = list(self.alternate_titles)
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

    def __str__(self) -> str:
        return self.canonical or ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["raw_tokens"] = list(self.raw_tokens)
        return value


@dataclass(frozen=True, slots=True)
class ReleaseAttributes:
    """Known name-derived release attributes.

    ``values`` retains canonical display names and the boolean fields make
    common conditions cheap and obvious.
    """

    values: tuple[str, ...] = ()
    hybrid: bool = False
    repack: bool = False
    proper: bool = False
    rerip: bool = False
    real: bool = False
    internal: bool = False
    limited: bool = False
    complete: bool = False
    three_d: bool = False
    repack_revision: int = 0
    proper_revision: int = 0
    rerip_revision: int = 0

    def as_list(self) -> list[str]:
        return list(self.values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": list(self.values),
            "hybrid": self.hybrid,
            "repack": self.repack,
            "proper": self.proper,
            "rerip": self.rerip,
            "real": self.real,
            "internal": self.internal,
            "limited": self.limited,
            "complete": self.complete,
            "three_d": self.three_d,
            "repack_revision": self.repack_revision,
            "proper_revision": self.proper_revision,
            "rerip_revision": self.rerip_revision,
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
    auro_3d: bool = False
    dual_audio: bool = False
    multi_audio: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReleaseParseResult:
    """Complete parser output.

    ``to_dict`` is the canonical JSON-friendly parser snapshot.
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
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_name": self.raw_name,
            "normalized_name": self.normalized_name,
            "identity": self.identity.to_dict(),
            "quality": self.quality.to_dict(),
            "edition": self.edition,
            "provider": self.provider,
            "release_group": self.release_group,
            "attributes": self.attributes.to_dict(),
            "hdr": self.hdr.to_dict(),
            "video": self.video.to_dict(),
            "audio": self.audio.to_dict(),
            "languages": list(self.languages),
            "regions": list(self.regions),
            "unknown_tokens": list(self.unknown_tokens),
            "warnings": list(self.warnings),
        }
