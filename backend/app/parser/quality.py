"""Canonical quality taxonomy used by the release parser.

The taxonomy is intentionally data-shaped so API/bootstrap code can expose it
without importing the parser's implementation details.
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


# `rank` provides deterministic display order and the explicit minimum-quality
# warning floor used by Quality Profiles. It is not an automatic upgrade score,
# preferred-quality ladder, or download rejection rule. Parser matching itself
# uses the explicit precedence in release_parser.py.
QUALITY_DEFINITIONS: tuple[QualityDefinition, ...] = (
    QualityDefinition("2160p BluRay REMUX", "2160p", "BluRay", "REMUX", rank=100),
    QualityDefinition("1080p BluRay REMUX", "1080p", "BluRay", "REMUX", rank=90),
    QualityDefinition("2160p Encode", "2160p", "BluRay", "Encode", rank=85),
    QualityDefinition("1080p Encode", "1080p", "BluRay", "Encode", rank=80),
    QualityDefinition("2160p WEB-DL", "2160p", "WEB-DL", rank=75),
    QualityDefinition("1080p WEB-DL", "1080p", "WEB-DL", rank=70),
    QualityDefinition("720p WEB-DL", "720p", "WEB-DL", rank=65),
    QualityDefinition("2160p WEBRip", "2160p", "WEBRip", rank=60),
    QualityDefinition("1080p WEBRip", "1080p", "WEBRip", rank=55),
    QualityDefinition("720p WEBRip", "720p", "WEBRip", rank=50),
    QualityDefinition("2160p HDTV", "2160p", "HDTV", rank=45),
    QualityDefinition("1080p HDTV", "1080p", "HDTV", rank=40),
    QualityDefinition("720p HDTV", "720p", "HDTV", rank=35),
    QualityDefinition("576i PAL DVD REMUX", "576i", "DVD", "REMUX", "interlaced", 30),
    QualityDefinition("576p PAL DVD REMUX", "576p", "DVD", "REMUX", "progressive", 29),
    QualityDefinition("480i NTSC DVD REMUX", "480i", "DVD", "REMUX", "interlaced", 25),
    QualityDefinition("480p NTSC DVD REMUX", "480p", "DVD", "REMUX", "progressive", 24),
    QualityDefinition("Full Disc DVD5", None, "DVD", scan_type="full_disc", rank=20),
    QualityDefinition("Full Disc DVD9", None, "DVD", scan_type="full_disc", rank=21),
    QualityDefinition("Full Disc DVD5/DVD9", None, "DVD", scan_type="full_disc", rank=22),
)

QUALITY_BY_NAME = {item.name: item for item in QUALITY_DEFINITIONS}


def list_quality_definitions() -> tuple[QualityDefinition, ...]:
    return QUALITY_DEFINITIONS


__all__ = ["QUALITY_BY_NAME", "QUALITY_DEFINITIONS", "QualityDefinition", "list_quality_definitions"]

