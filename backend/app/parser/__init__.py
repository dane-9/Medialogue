"""Shared release parser public API."""

from .release_parser import (
    EDITION_OPTIONS,
    QUALITY_PRECEDENCE,
    normalize_release_name,
    parse_release,
    parse_quality,
    detect_edition,
    detect_release_group,
    parse_season_folder,
    extract_episode_numbers,
)
from .types import (
    AudioInfo,
    HDRInfo,
    IdentityInfo,
    QualityInfo,
    ReleaseAttributes,
    ReleaseParseResult,
    VideoInfo,
)
from .quality import QUALITY_BY_NAME, QUALITY_DEFINITIONS, QualityDefinition, list_quality_definitions

__all__ = [
    "QUALITY_PRECEDENCE",
    "AudioInfo",
    "HDRInfo",
    "IdentityInfo",
    "QualityInfo",
    "ReleaseAttributes",
    "ReleaseParseResult",
    "VideoInfo",
    "QUALITY_BY_NAME",
    "EDITION_OPTIONS",
    "QUALITY_DEFINITIONS",
    "QualityDefinition",
    "list_quality_definitions",
    "normalize_release_name",
    "parse_release",
    "parse_season_folder",
    "extract_episode_numbers",
    "parse_quality",
    "detect_edition",
    "detect_release_group",
]
