"""Shared release parser public API."""

from .release_parser import (
    QUALITY_PRECEDENCE,
    normalize_release_name,
    parse_release_name,
    parse_quality,
    parse_release,
    detect_edition,
    detect_release_group,
    parse_season_folder,
    extract_episode_numbers,
)
from .types import (
    PARSER_VERSION,
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
    "PARSER_VERSION",
    "QUALITY_PRECEDENCE",
    "AudioInfo",
    "HDRInfo",
    "IdentityInfo",
    "QualityInfo",
    "ReleaseAttributes",
    "ReleaseParseResult",
    "VideoInfo",
    "QUALITY_BY_NAME",
    "QUALITY_DEFINITIONS",
    "QualityDefinition",
    "list_quality_definitions",
    "normalize_release_name",
    "parse_release_name",
    "parse_season_folder",
    "extract_episode_numbers",
    "parse_quality",
    "parse_release",
    "detect_edition",
    "detect_release_group",
]
