"""Canonical, structured quality taxonomy used by the release parser.

Definitions are generated from resolution/source dimensions. Existing names
stay stable so database IDs and saved profiles survive catalog upgrades.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class QualityDefinition:
    name: str
    resolution: str | None
    source: str | None
    modifier: str | None = None
    scan_type: str | None = None
    rank: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


WEB_DL_RESOLUTIONS = ("2160p", "1440p", "1080p", "720p", "576p", "540p", "480p", "360p")
WEBRIP_RESOLUTIONS = ("2160p", "1440p", "1080p", "720p", "576p", "540p", "480p", "360p")
HDTV_RESOLUTIONS = ("2160p", "1080p", "1080i", "720p", "576p", "576i", "480p", "480i", "360p")
BLURAY_REMUX_RESOLUTIONS = ("2160p", "1080p", "1080i", "720p")
BLURAY_ENCODE_RESOLUTIONS = ("2160p", "1080p", "1080i", "720p")


def _family(
    resolutions: tuple[str, ...],
    *,
    source: str,
    modifier: str | None,
    rank_base: int,
) -> tuple[QualityDefinition, ...]:
    definitions: list[QualityDefinition] = []
    for index, resolution in enumerate(resolutions):
        name = (
            f"{resolution} Encode"
            if source == "BluRay" and modifier == "Encode"
            else f"{resolution} {source}{f' {modifier}' if modifier else ''}"
        )
        scan_type = "interlaced" if resolution.endswith("i") else "progressive"
        definitions.append(QualityDefinition(name, resolution, source, modifier, scan_type, rank_base - index))
    return tuple(definitions)


def _dvd_family(modifier: str, rank_base: int) -> tuple[QualityDefinition, ...]:
    """DVD qualities by resolution alone.

    PAL and NTSC are a broadcast standard, not a quality: 576p PAL and 576p
    NTSC are the same thing to rank, so splitting the catalog by them only
    produced pairs nobody could meaningfully score differently. The standard is
    still parsed out of a release name and kept as evidence.
    """
    variants = (
        ("576p", "progressive"),
        ("480p", "progressive"),
        ("576i", "interlaced"),
        ("480i", "interlaced"),
    )
    return tuple(
        QualityDefinition(
            f"{resolution} DVD {modifier}",
            resolution,
            "DVD",
            modifier,
            scan_type,
            rank_base - index,
        )
        for index, (resolution, scan_type) in enumerate(variants)
    )


# Source families retain the previous default ordering. A profile's explicit
# order is authoritative; rank only seeds fresh profiles.
QUALITY_DEFINITIONS: tuple[QualityDefinition, ...] = (
    *_family(BLURAY_REMUX_RESOLUTIONS, source="BluRay", modifier="REMUX", rank_base=1000),
    *_family(BLURAY_ENCODE_RESOLUTIONS, source="BluRay", modifier="Encode", rank_base=900),
    *_family(WEB_DL_RESOLUTIONS, source="WEB-DL", modifier=None, rank_base=800),
    *_family(WEBRIP_RESOLUTIONS, source="WEBRip", modifier=None, rank_base=700),
    *_family(HDTV_RESOLUTIONS, source="HDTV", modifier=None, rank_base=600),
    *_dvd_family("REMUX", 500),
    *_dvd_family("Encode", 450),
    QualityDefinition("Full Disc UHD BluRay", "2160p", "BluRay", modifier="Full Disc", scan_type="full_disc", rank=405),
    QualityDefinition("Full Disc BluRay", None, "BluRay", modifier="Full Disc", scan_type="full_disc", rank=404),
    QualityDefinition("Full Disc DVD", None, "DVD", modifier="Full Disc", scan_type="full_disc", rank=403),
    QualityDefinition("Full Disc DVD5/DVD9", None, "DVD", scan_type="full_disc", rank=402),
    QualityDefinition("Full Disc DVD9", None, "DVD", scan_type="full_disc", rank=401),
    QualityDefinition("Full Disc DVD5", None, "DVD", scan_type="full_disc", rank=400),
)

QUALITY_BY_NAME = {item.name: item for item in QUALITY_DEFINITIONS}


def list_quality_definitions() -> tuple[QualityDefinition, ...]:
    return QUALITY_DEFINITIONS


__all__ = [
    "BLURAY_ENCODE_RESOLUTIONS",
    "BLURAY_REMUX_RESOLUTIONS",
    "HDTV_RESOLUTIONS",
    "QUALITY_BY_NAME",
    "QUALITY_DEFINITIONS",
    "WEBRIP_RESOLUTIONS",
    "WEB_DL_RESOLUTIONS",
    "QualityDefinition",
    "list_quality_definitions",
]
