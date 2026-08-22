"""Name-based movie and TV release parser.

The parser is deliberately conservative.  It identifies a technical release
boundary from the right-hand side of a name, keeps the title evidence on the
left, and preserves unclassified technical tokens for later grammar updates.
It never opens media files and it does not make filesystem decisions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from pathlib import PurePath
from typing import Iterable

from .types import (
    AudioInfo,
    HDRInfo,
    IdentityInfo,
    PARSER_VERSION,
    QualityInfo,
    ReleaseAttributes,
    ReleaseParseResult,
    VideoInfo,
)


# The order is intentional.  It is the parser's quality precedence contract.
QUALITY_PRECEDENCE: tuple[str, ...] = (
    "full_disc_dvd5_dvd9",
    "full_disc_dvd9",
    "full_disc_dvd5",
    "2160p_bluray_remux",
    "1080p_bluray_remux",
    "2160p_encode",
    "1080p_encode",
    "2160p_web_dl",
    "1080p_web_dl",
    "720p_web_dl",
    "2160p_webrip",
    "1080p_webrip",
    "720p_webrip",
    "2160p_hdtv",
    "1080p_hdtv",
    "720p_hdtv",
    "480i_ntsc_dvd_remux",
    "576i_pal_dvd_remux",
    "480p_ntsc_dvd_remux",
    "576p_pal_dvd_remux",
)

_EDITION_ALIASES: tuple[tuple[str, str], ...] = (
    ("super sized and uncut", "Super-Sized and Uncut"),
    ("super-sized and uncut", "Super-Sized and Uncut"),
    ("special assembly cut", "Special Assembly Cut"),
    ("super duper cut", "Super Duper Cut"),
    ("ultimate edition", "Ultimate Edition"),
    ("director's cut", "Director's Cut"),
    ("directors cut", "Director's Cut"),
    ("producer's cut", "Producer's Cut"),
    ("producers cut", "Producer's Cut"),
    ("theatrical cut", "Theatrical Cut"),
    ("theatrical", "Theatrical Cut"),
    ("extended cut", "Extended Cut"),
    ("extended", "Extended Cut"),
    ("ultimate cut", "Ultimate Cut"),
    ("special assembly", "Special Assembly Cut"),
    ("unrated", "Unrated"),
    ("final cut", "Final Cut"),
    ("open matte", "Open Matte"),
    ("imax enhanced", "IMAX"),
    ("imax", "IMAX"),
    ("recut", "Recut"),
    ("rogue cut", "Rogue Cut"),
)

_PROVIDERS: dict[str, str] = {
    "amzn": "AMZN",
    "amazon": "AMZN",
    "dsnp": "DSNP",
    "disney+": "DSNP",
    "ma": "MA",
    "it": "iT",
    "nf": "NF",
    "netflix": "NF",
    "atvp": "ATVP",
    "wowp": "WOWP",
    "hmax": "HMAX",
    "max": "MAX",
    "hulu": "HULU",
    "paramount+": "Paramount+",
    "pcok": "PCOK",
}

_REGIONS = {
    "FIN",
    "BRA",
    "GBR",
    "USA",
    "UK",
    "CAN",
    "AUS",
    "DEU",
    "FRA",
    "ESP",
    "ITA",
    "JPN",
    "KOR",
}

_LANGUAGES = {
    "ENGLISH",
    "FRENCH",
    "KOREAN",
    "GERMAN",
    "SPANISH",
    "ITALIAN",
    "JAPANESE",
    "DUTCH",
    "SWEDISH",
    "NORWEGIAN",
    "DANISH",
    "FINNISH",
    "POLISH",
    "RUSSIAN",
    "LATIN",
}

_ATTRIBUTE_CANONICAL = {
    "hybrid": "Hybrid",
    "repack": "REPACK",
    "proper": "PROPER",
    "real": "REAL",
    "internal": "INTERNAL",
    "limited": "LIMITED",
    "rerip": "RERIP",
    "reencode": "RE-ENCODE",
    "remastered": "REMASTERED",
}

_KNOWN_NON_TITLE = {
    "2160p",
    "1080p",
    "720p",
    "576p",
    "480p",
    "576i",
    "480i",
    "2160",
    "1080",
    "720",
    "576",
    "480",
    "uhd",
    "uhd-bluray",
    "uhdbluray",
    "bluray",
    "blu-ray",
    "web-dl",
    "webdl",
    "webrip",
    "hdtv",
    "remux",
    "dvd",
    "dvd5",
    "dvd9",
    "ntsc",
    "pal",
    "x264",
    "x265",
    "xvid",
    "avc",
    "hevc",
    "av1",
    "vc-1",
    "vc1",
    "mpeg-2",
    "mpeg2",
    "h",
    "264",
    "265",
    "hdr",
    "hdr10",
    "hdr10+",
    "dv",
    "hlg",
    "dolby",
    "vision",
    "truehd",
    "dts-hd",
    "dts",
    "ma",
    "hra",
    "dd",
    "dd+",
    "flac",
    "aac",
    "atmos",
    "dts:x",
    "dtsx",
    "hybrid",
    "repack",
    "proper",
    "real",
    "internal",
    "limited",
    "aka",
}

_MEDIA_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".m4v",
    ".mov",
    ".wmv",
    ".ts",
    ".m2ts",
    ".iso",
}


def _clean_raw_name(raw_name: str) -> str:
    # Input may be a media filename or a directory path.  Only the basename
    # is parsed; the caller retains the original path as separate evidence.
    value = str(raw_name).strip()
    if not value:
        return ""
    value = PurePath(value.replace("\\", "/")).name
    for ext in sorted(_MEDIA_EXTENSIONS, key=len, reverse=True):
        if value.casefold().endswith(ext):
            value = value[: -len(ext)]
            break
    return unicodedata.normalize("NFKC", value).strip()


def normalize_release_name(raw_name: str) -> str:
    """Normalize common release separators while retaining raw evidence.

    Dots and underscores are release-name separators.  Hyphens are retained
    long enough for release-group suffix detection and then represented as
    spaces in the normalized display value.
    """

    value = _clean_raw_name(raw_name)
    value = value.replace("’", "'").replace("`", "'")
    value = re.sub(r"[._]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -")


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r"\s+", value.strip()) if token]


def _casefold_token(value: str) -> str:
    return value.casefold().strip()


def _normalise_punctuation_phrase(value: str) -> str:
    value = value.replace("’", "'").replace("`", "'")
    value = re.sub(r"[._-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().casefold()


def _edition_from_name(normalized: str) -> tuple[str | None, str | None]:
    folded = _normalise_punctuation_phrase(normalized)
    # Longest/specific phrase wins.  Word boundaries stop e.g. "unrated" from
    # being matched inside a future token.
    for alias, canonical in sorted(_EDITION_ALIASES, key=lambda item: len(item[0]), reverse=True):
        phrase = _normalise_punctuation_phrase(alias)
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded):
            return canonical, alias
    return None, None


_SEASON_NUMBER = r"(?:\d{1,2}|(?:19|20)\d{2})"


def _extract_tv_boundary(normalized: str) -> tuple[int | None, tuple[int, ...], int | None, int | None]:
    """Return season, episode numbers, and marker start/end.

    Supported forms include S01, S01E02, S01E01E02, S01E01-E03 and 1x02.
    A season pack marker is represented by a season and an empty episode tuple.
    """

    # The compact range is deliberately handled before the repeated E form.
    range_match = re.search(
        rf"(?<![A-Za-z0-9])S(?P<s>{_SEASON_NUMBER})E(?P<start>\d{{1,3}})\s*[-–]\s*(?:E)?(?P<end>\d{{1,3}})(?!\d)",
        normalized,
        re.IGNORECASE,
    )
    if range_match:
        season = int(range_match.group("s"))
        start, end = int(range_match.group("start")), int(range_match.group("end"))
        episodes = tuple(range(start, end + 1)) if end >= start else (start, end)
        return season, episodes, range_match.start(), range_match.end()

    compact = re.search(
        rf"(?<![A-Za-z0-9])S(?P<s>{_SEASON_NUMBER})(?P<eps>(?:E\d{{1,3}})+)(?!\d)",
        normalized,
        re.IGNORECASE,
    )
    if compact:
        season = int(compact.group("s"))
        episodes = tuple(int(value) for value in re.findall(r"E(\d{1,3})", compact.group("eps"), re.I))
        return season, episodes, compact.start(), compact.end()

    single = re.search(
        rf"(?<![A-Za-z0-9])S(?P<s>{_SEASON_NUMBER})E(?P<e>\d{{1,3}})(?!\d)",
        normalized,
        re.IGNORECASE,
    )
    if single:
        return int(single.group("s")), (int(single.group("e")),), single.start(), single.end()

    one_x = re.search(
        r"(?<![A-Za-z0-9])(?P<s>\d{1,2})x(?P<e>\d{1,3})(?!\d)", normalized, re.I
    )
    if one_x:
        return int(one_x.group("s")), (int(one_x.group("e")),), one_x.start(), one_x.end()

    season_only = re.search(rf"(?<![A-Za-z0-9])S(?P<s>{_SEASON_NUMBER})(?![A-Za-z0-9])", normalized, re.I)
    if season_only:
        return int(season_only.group("s")), (), season_only.start(), season_only.end()
    return None, (), None, None



def parse_season_folder(name: str) -> int | None:
    """Return the season number a directory name denotes, if any.

    Recognises the layouts libraries actually use: ``Season 1``, ``Season.1``,
    ``Season_1``, ``Season 01``, ``S01``, ``S1``, the UK ``Series 2`` form, and
    year seasons such as ``Season 1940`` / ``S1940``. ``Specials`` and
    ``Season 0`` both mean season zero.
    """

    text = str(name).strip()
    if not text:
        return None
    if re.fullmatch(r"specials?", text, re.IGNORECASE):
        return 0
    worded = re.fullmatch(
        rf"(?:season|series|staffel|saison)[\s._-]*({_SEASON_NUMBER})",
        text,
        re.IGNORECASE,
    )
    if worded:
        return int(worded.group(1))
    compact = re.fullmatch(rf"s[\s._-]*({_SEASON_NUMBER})", text, re.IGNORECASE)
    if compact:
        return int(compact.group(1))
    return None


def extract_episode_numbers(name: str) -> tuple[int, ...]:
    """Episode numbers from a filename whose season comes from its folder.

    Only used when a season folder has already established the season, because
    these patterns are far weaker than S01E01 and would misfire on titles that
    merely begin with a number.
    """

    text = normalize_release_name(_clean_raw_name(str(name)))

    ranged = re.match(r"\s*(\d{1,3})\s*[-–]\s*(\d{1,3})\s*[-–]\s+", text)
    if ranged:
        start, end = int(ranged.group(1)), int(ranged.group(2))
        if end >= start and end - start < 50:
            return tuple(range(start, end + 1))

    explicit = re.findall(r"(?<![A-Za-z0-9])E(\d{1,3})(?!\d)", text, re.IGNORECASE)
    if explicit:
        return tuple(dict.fromkeys(int(value) for value in explicit))

    worded = re.search(r"\bepisode[\s._-]*(\d{1,3})(?!\d)", text, re.IGNORECASE)
    if worded:
        return (int(worded.group(1)),)

    # ``01 - Title`` / ``01. Title`` / ``01 Title``: a leading number acting as
    # the episode index. A separator is required so a title like "1917" or
    # "300" is not mistaken for an episode.
    leading = re.match(r"\s*(\d{1,3})\s*(?:[-–.]\s*|\s+)(?=\S)", text)
    if leading:
        return (int(leading.group(1)),)
    return None or ()


def _year_match(normalized: str, *, before: int | None = None) -> re.Match[str] | None:
    text = normalized if before is None else normalized[:before]
    # Years are intentionally bounded to realistic release years.
    matches = list(re.finditer(r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)", text))
    return matches[-1] if matches else None


def _technical_boundary(normalized: str, *, start: int = 0) -> int | None:
    """Locate the earliest high-confidence release token after ``start``."""

    patterns = (
        r"\b(?:2160|1080|720|576|480)p\b",
        r"\b(?:576|480)i\b",
        rf"\b(?:S{_SEASON_NUMBER}(?:E\d{{1,3}})?|\d{{1,2}}x\d{{1,3}})\b",
        r"\b(?:WEB[- ]?DL|WEB[- ]?Rip|HDTV|Blu[- ]?Ray|DVD(?:5|9)?|REMUX)\b",
        r"\b(?:x26[45]|AVC|HEVC|H\.26[45]|MPEG[- ]?2|VC[- ]?1|TrueHD|DTS[- ]?HD|DD\+?|FLAC|AAC)\b",
    )
    candidates: list[int] = []
    for pattern in patterns:
        match = re.search(pattern, normalized[start:], re.IGNORECASE)
        if match:
            candidates.append(start + match.start())
    return min(candidates) if candidates else None


def _release_group(raw: str, normalized: str) -> tuple[str, str, bool]:
    """Extract a terminal ``-Group`` suffix and return text without it."""

    # A suffix must be at the end and begin after a meaningful token.  This
    # accepts numeric groups (e.g. -126811) and preserves mixed case.
    # Release groups conventionally occupy the final hyphen-delimited token.
    # Do not let the hyphen in WEB-DL/Blu-Ray become part of that token.
    match = re.search(r"-(?P<group>[A-Za-z0-9][A-Za-z0-9+._]*)\s*$", raw)
    if not match:
        # Normalized names have separators converted but still allow a final
        # hyphen; this fallback handles names that entered already normalized.
        match = re.search(r"-(?P<group>[A-Za-z0-9][A-Za-z0-9+._]*)\s*$", normalized)
        if not match:
            return "NoGroup", normalized, False
    group = match.group("group")
    cut = match.start()
    prefix = raw[:cut]
    # Hyphens embedded in technical compounds are not release-group
    # separators (WEB-DL, Blu-Ray, DTS-HD).  A terminal token such as ``DL``
    # must therefore remain part of the source token.
    if group.casefold() in {"dl", "ray", "rip", "hd"} and re.search(
        r"(?:WEB|Blu|DTS)\s*$", prefix, re.IGNORECASE
    ):
        return "NoGroup", normalized, False
    without = raw[:cut].rstrip(" -")
    return group, normalize_release_name(without), True


def _resolution(normalized: str) -> str | None:
    match = re.search(r"\b(2160|1080|720|576|480)(p|i)\b", normalized, re.I)
    return f"{match.group(1)}{match.group(2).lower()}" if match else None


def _token_values(normalized: str) -> set[str]:
    return {_casefold_token(token.strip("[](){}")) for token in _tokens(normalized)}


def _quality(normalized: str) -> QualityInfo:
    folded = normalized.casefold()
    resolution = _resolution(normalized)
    values = _token_values(normalized)
    raw_tokens = tuple(_tokens(normalized))

    has_dvd5 = bool(re.search(r"\bDVD\s*5\b|\bDVD5\b", normalized, re.I))
    has_dvd9 = bool(re.search(r"\bDVD\s*9\b|\bDVD9\b", normalized, re.I))
    has_bluray = bool(re.search(r"\b(?:UHD\s+)?Blu[- ]?Ray\b", normalized, re.I))
    has_remux = bool(re.search(r"\bREMUX\b", normalized, re.I))
    has_webdl = bool(re.search(r"\bWEB[- ]?DL\b|\bWEB-DL\b", normalized, re.I))
    has_webrip = bool(re.search(r"\bWEB[- ]?Rip\b|\bWEBRip\b", normalized, re.I))
    has_hdtv = bool(re.search(r"\bHDTV\b", normalized, re.I))
    has_dvd_remux = bool(re.search(r"\bDVD\s+REMUX\b", normalized, re.I))
    standard = None
    standard_match = re.search(r"\b(NTSC|PAL)\b", normalized, re.I)
    if standard_match:
        standard = standard_match.group(1).upper()

    # Full-disc precedence is explicit and independent of resolution.
    if has_dvd5 and has_dvd9:
        return QualityInfo("Full Disc DVD5/DVD9", resolution, "DVD", None, "DVD5/DVD9", standard, raw_tokens)
    if has_dvd9 and not has_dvd_remux:
        return QualityInfo("Full Disc DVD9", resolution, "DVD", None, "DVD9", standard, raw_tokens)
    if has_dvd5 and not has_dvd_remux:
        return QualityInfo("Full Disc DVD5", resolution, "DVD", None, "DVD5", standard, raw_tokens)

    if has_remux and has_bluray and resolution in {"2160p", "1080p"}:
        return QualityInfo(f"{resolution} BluRay REMUX", resolution, "BluRay", "REMUX", None, standard, raw_tokens)

    # DVD REMUX carries the SD standard in its canonical value.  Fall back to
    # the resolution when a release name omits NTSC/PAL.
    if has_dvd_remux:
        if standard == "NTSC" and resolution:
            canonical = f"{resolution} NTSC DVD REMUX"
        elif standard == "PAL" and resolution:
            canonical = f"{resolution} PAL DVD REMUX"
        elif resolution:
            canonical = f"{resolution} DVD REMUX"
        else:
            canonical = "DVD REMUX"
        return QualityInfo(canonical, resolution, "DVD", "REMUX", None, standard, raw_tokens)

    # BluRay without REMUX is an encode in this application's taxonomy.  The
    # codec evidence is intentionally left untouched by this classification.
    if has_bluray and resolution in {"2160p", "1080p"}:
        return QualityInfo(f"{resolution} Encode", resolution, "BluRay", "Encode", None, standard, raw_tokens)

    for source, marker, canonical_source in (
        ("WEB-DL", has_webdl, "WEB-DL"),
        ("WEBRip", has_webrip, "WEBRip"),
        ("HDTV", has_hdtv, "HDTV"),
    ):
        if marker and resolution in {"2160p", "1080p", "720p"}:
            return QualityInfo(f"{resolution} {canonical_source}", resolution, source, None, None, standard, raw_tokens)

    # A token-only DVD5/DVD9 name was already handled above; retain the
    # resolution/source even when the quality is not yet part of the taxonomy.
    return QualityInfo(None, resolution, None, None, None, standard, raw_tokens)


def _provider(normalized: str, quality: QualityInfo) -> str | None:
    tokens = _tokens(normalized)
    source_index = next(
        (idx for idx, token in enumerate(tokens) if re.fullmatch(r"WEB[- ]?DL|WEB[- ]?Rip|WEBRip", token, re.I)),
        -1,
    )
    resolution_index = next(
        (idx for idx, token in enumerate(tokens) if re.fullmatch(r"(?:2160|1080|720|576|480)[pi]", token, re.I)),
        -1,
    )
    for index, token in enumerate(tokens):
        key = token.casefold()
        if source_index >= 0 and key in _PROVIDERS and resolution_index <= index <= source_index:
            # MA after a DTS-HD token is audio, not a WEB provider.  Requiring
            # the token before WEB-DL avoids that common ambiguity.
            if key == "ma" and index >= source_index:
                continue
            return _PROVIDERS[key]
    return None


def _attributes(normalized: str) -> ReleaseAttributes:
    found: list[str] = []
    folded = _normalise_punctuation_phrase(normalized)
    for key, canonical in _ATTRIBUTE_CANONICAL.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", folded):
            found.append(canonical)
    # Stable order follows the canonical vocabulary, not source token order.
    return ReleaseAttributes(
        values=tuple(found),
        hybrid="Hybrid" in found,
        repack="REPACK" in found,
        proper="PROPER" in found,
        real="REAL" in found,
        internal="INTERNAL" in found,
        limited="LIMITED" in found,
    )


def _hdr(normalized: str) -> HDRInfo:
    folded = _normalise_punctuation_phrase(normalized)
    return HDRInfo(
        dolby_vision=bool(re.search(r"(?<![a-z0-9])(?:dv|dolby vision)(?![a-z0-9])", folded)),
        hdr=bool(re.search(r"(?<![a-z0-9])hdr(?![0-9a-z])", folded)),
        hdr10=bool(re.search(r"(?<![a-z0-9])hdr10(?!\+|[a-z0-9])", folded)),
        hdr10_plus=bool(re.search(r"(?<![a-z0-9])hdr10\+(?![a-z0-9])", folded)),
        hlg=bool(re.search(r"(?<![a-z0-9])hlg(?![a-z0-9])", folded)),
    )


def _video(raw: str, normalized: str) -> VideoInfo:
    patterns = (
        (r"(?<![A-Za-z0-9])MPEG[- .]?2(?![A-Za-z0-9])", "MPEG-2"),
        (r"(?<![A-Za-z0-9])VC[- .]?1(?![A-Za-z0-9])", "VC-1"),
        (r"(?<![A-Za-z0-9])x264(?![A-Za-z0-9])", "x264"),
        (r"(?<![A-Za-z0-9])x265(?![A-Za-z0-9])", "x265"),
        (r"(?<![A-Za-z0-9])H[. -]?264(?![A-Za-z0-9])", "H.264"),
        (r"(?<![A-Za-z0-9])H[. -]?265(?![A-Za-z0-9])", "H.265"),
        (r"(?<![A-Za-z0-9])AVC(?![A-Za-z0-9])", "AVC"),
        (r"(?<![A-Za-z0-9])HEVC(?![A-Za-z0-9])", "HEVC"),
        (r"(?<![A-Za-z0-9])AV1(?![A-Za-z0-9])", "AV1"),
    )
    for pattern, canonical in patterns:
        match = re.search(pattern, raw, re.I) or re.search(pattern, normalized, re.I)
        if match:
            return VideoInfo(canonical, match.group(0))
    return VideoInfo()


def _audio(raw: str, normalized: str) -> AudioInfo:
    # Longest/base-specific patterns precede DD, which is a substring of DD+.
    patterns = (
        (r"(?<![A-Za-z0-9])TrueHD(?=[0-9]|(?![A-Za-z0-9]))", "TrueHD"),
        (r"(?<![A-Za-z0-9])DTS[- .]?HD[- .]?MA(?=[0-9]|(?![A-Za-z0-9]))", "DTS-HD MA"),
        (r"(?<![A-Za-z0-9])DTS[- .]?HD[- .]?HRA(?=[0-9]|(?![A-Za-z0-9]))", "DTS-HD HRA"),
        (r"(?<![A-Za-z0-9])DTS[- .]?HD(?![A-Za-z0-9])", "DTS-HD"),
        (r"(?<![A-Za-z0-9])DTS[: -]?X(?![A-Za-z0-9])", "DTS:X"),
        (r"(?<![A-Za-z0-9])DTS(?=[0-9]|(?![A-Za-z0-9]))", "DTS"),
        # Dolby Digital Plus ships under four spellings; all mean the same codec.
        (r"(?<![A-Za-z0-9])E[- .]?AC[- .]?3(?![A-Za-z0-9])", "DD+"),
        (r"(?<![A-Za-z0-9])DDP(?=[0-9]|(?![A-Za-z0-9]))", "DD+"),
        (r"(?<![A-Za-z0-9])DD\+(?=[0-9]|(?![A-Za-z0-9]))", "DD+"),
        (r"(?<![A-Za-z0-9])AC[- .]?3(?![A-Za-z0-9])", "DD"),
        (r"(?<![A-Za-z0-9])DD(?=[0-9]|(?![A-Za-z0-9+]))", "DD"),
        (r"(?<![A-Za-z0-9])FLAC(?=[0-9]|(?![A-Za-z0-9]))", "FLAC"),
        (r"(?<![A-Za-z0-9])L?PCM(?![A-Za-z0-9])", "PCM"),
        (r"(?<![A-Za-z0-9])Opus(?![A-Za-z0-9])", "Opus"),
        (r"(?<![A-Za-z0-9])MP3(?![A-Za-z0-9])", "MP3"),
        (r"(?<![A-Za-z0-9])AAC(?=[0-9]|(?![A-Za-z0-9]))", "AAC"),
    )
    codec = None
    for pattern, canonical in patterns:
        match = re.search(pattern, raw, re.I) or re.search(pattern, normalized, re.I)
        if match:
            codec = canonical
            break
    # A channel layout attached directly to the codec (DDP5.1, DD+7.1) is read
    # first; only then the standalone forms.
    channels_match = re.search(
        r"(?:TrueHD|DTS(?:[- .]?HD)?(?:[- .]?MA)?|DDP|DD\+|DD|E?[- .]?AC[- .]?3|FLAC|AAC|Opus|PCM)"
        r"[- .]?([1-9](?:\.[0-9]){1,2})(?![A-Za-z0-9])",
        raw,
        re.IGNORECASE,
    )
    if channels_match is None:
        channels_match = re.search(r"(?<![A-Za-z0-9])([1-9](?:\.[0-9]){1,2})(?![A-Za-z0-9])", raw)
    if channels_match is None:
        channels_match = re.search(r"(?<![A-Za-z0-9])([1-9](?:\s+[0-9]){1,2})(?![A-Za-z0-9])", normalized)
    channels = channels_match.group(1) if channels_match else None
    if channels and " " in channels:
        channels = channels.replace(" ", ".")
    folded = _normalise_punctuation_phrase(raw)
    return AudioInfo(
        codec=codec,
        channels=channels,
        atmos=bool(re.search(r"(?<![a-z0-9])atmos(?![a-z0-9])", folded)),
        dts_x=bool(re.search(r"(?<![a-z0-9])dts x(?![a-z0-9])", folded)),
    )


def _language_and_regions(normalized: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tokens = _tokens(normalized)
    languages: list[str] = []
    regions: list[str] = []
    for token in tokens:
        clean = token.strip("[](){}.,").upper()
        if clean in _LANGUAGES and clean not in languages:
            languages.append(clean)
        if clean in _REGIONS and clean not in regions:
            regions.append(clean)
    return tuple(languages), tuple(regions)


def _unknown_tokens(
    technical_tail: str,
    *,
    release_group: str,
    edition: str | None,
    provider: str | None,
    quality: QualityInfo,
    attributes: ReleaseAttributes,
    hdr: HDRInfo,
    video: VideoInfo,
    audio: AudioInfo,
    languages: Iterable[str],
    regions: Iterable[str],
) -> tuple[str, ...]:
    """Preserve unknown technical tokens without treating title words as unknown."""

    known = set(_KNOWN_NON_TITLE)
    known.update(item.casefold() for item in languages)
    known.update(item.casefold() for item in regions)
    known.update(item.casefold() for item in attributes.values)
    known.update({item.casefold() for item in hdr.values})
    if edition:
        known.update(_normalise_punctuation_phrase(edition).split())
    if provider:
        known.add(provider.casefold())
    if quality.canonical:
        known.update(_normalise_punctuation_phrase(quality.canonical).split())
    if quality.source:
        known.update(_normalise_punctuation_phrase(quality.source).split())
    if quality.modifier:
        known.add(quality.modifier.casefold())
    if quality.tv_standard:
        known.add(quality.tv_standard.casefold())
    if video.codec:
        known.update(_normalise_punctuation_phrase(video.codec).split())
        if video.raw_codec_token:
            known.update(_normalise_punctuation_phrase(video.raw_codec_token).split())
    if audio.codec:
        known.update(_normalise_punctuation_phrase(audio.codec).split())
    if audio.channels:
        known.add(audio.channels.casefold())
    if audio.atmos:
        known.add("atmos")
    if audio.dts_x:
        known.update({"dts", "x"})
    if release_group != "NoGroup":
        known.add(release_group.casefold())

    result: list[str] = []
    for token in _tokens(technical_tail):
        stripped = token.strip("[](){}")
        if not stripped:
            continue
        folded = stripped.casefold()
        if folded in known:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
            # Channel numbers are known; standalone year/resolution numbers
            # in the technical tail are not useful unknown evidence.
            continue
        if folded in {"e", "s"}:
            continue
        if stripped not in result:
            result.append(stripped)
    return tuple(result)


def parse_release(raw_name: str, *, parser_version: str = PARSER_VERSION) -> ReleaseParseResult:
    """Parse a release/folder/filename into a structured result.

    The function is deterministic and side-effect free.  ``raw_name`` is
    retained exactly (apart from the caller's string conversion) and is safe
    to use as parser evidence.
    """

    raw = str(raw_name)
    cleaned = _clean_raw_name(raw)
    normalized_full = normalize_release_name(cleaned)
    normalized = normalized_full
    release_group, no_group_name, had_group = _release_group(cleaned, normalized)
    normalized = no_group_name

    season, episodes, marker_start, marker_end = _extract_tv_boundary(normalized)
    year_match = _year_match(normalized, before=marker_start)
    year = int(year_match.group(0)) if year_match else None

    if season is not None and marker_start is not None and marker_end is not None:
        title_end = marker_start
        title_part = normalized[:title_end].strip(" -")
        # A year before an Sxx marker is usually metadata, not part of a show
        # title.  Keep the title text before that year for consistency with
        # movie identity parsing.
        if year_match:
            title_part = normalized[: year_match.start()].strip(" -([.")
            identity_year = year
        else:
            identity_year = None
        tech_start = _technical_boundary(normalized, start=marker_end)
        episode_title = normalized[marker_end:tech_start].strip(" -") if tech_start is not None else normalized[marker_end:].strip(" -")
        if not episodes:
            # S04/S01 identifies a season pack; text after the marker belongs
            # to the release evidence, not to an episode title.
            episode_title = ""
        if episode_title:
            # Technical suffixes are sometimes separated only by a provider
            # token; remove a trailing release-group artefact if present.
            episode_title = episode_title.strip(" ._-([") or None
        identity = IdentityInfo(title_part or None, identity_year, season, episodes, episode_title or None)
        boundary_for_technical = marker_end
    else:
        boundary_for_technical = 0
        if year_match:
            # Some libraries are organized as ``YEAR Title`` rather than
            # ``Title YEAR``.  A leading four-digit year used to produce an
            # empty title candidate (e.g. ``2000 The Emperor's New Groove``).
            # Treat it as metadata only when meaningful title text follows it
            # before the technical release suffix. Numeric movie titles such
            # as ``1917 2019 ...`` still use the later release year because
            # ``_year_match`` deliberately selects the final year token.
            if year_match.start() == 0:
                leading_tech = _technical_boundary(normalized, start=year_match.end())
                title_end = leading_tech if leading_tech is not None else len(normalized)
                leading_title = normalized[year_match.end() : title_end].strip(" -")
            else:
                leading_tech = None
                leading_title = ""
            if leading_title and re.search(r"[A-Za-z]", leading_title):
                identity = IdentityInfo(leading_title, year, None, (), None)
                boundary_for_technical = leading_tech if leading_tech is not None else len(normalized)
            else:
                title_part = normalized[: year_match.start()].strip(" -")
                identity = IdentityInfo(title_part or None, year, None, (), None)
                boundary_for_technical = year_match.end()
        else:
            technical_start = _technical_boundary(normalized)
            title_part = normalized[:technical_start].strip(" -") if technical_start is not None else normalized
            identity = IdentityInfo(title_part or None, None, None, (), None)
            boundary_for_technical = technical_start or 0

    quality = _quality(normalized)
    edition, _edition_alias = _edition_from_name(normalized)
    provider = _provider(normalized, quality)
    attributes = _attributes(normalized)
    hdr = _hdr(normalized)
    video = _video(cleaned, normalized)
    audio = _audio(cleaned, normalized)
    languages, regions = _language_and_regions(normalized)

    technical_start = _technical_boundary(normalized, start=boundary_for_technical)
    # If no explicit quality was found, the suffix after the identity boundary
    # is still valuable unknown evidence.  Avoid marking the title as unknown.
    technical_tail = normalized[technical_start if technical_start is not None else boundary_for_technical :]
    unknown = _unknown_tokens(
        technical_tail,
        release_group=release_group,
        edition=edition,
        provider=provider,
        quality=quality,
        attributes=attributes,
        hdr=hdr,
        video=video,
        audio=audio,
        languages=languages,
        regions=regions,
    )

    warnings: list[str] = []
    if season is None and re.search(rf"\bS{_SEASON_NUMBER}(?:E\d{{1,3}})?\b", normalized, re.I):
        warnings.append("season_episode_boundary_ambiguous")
    if quality.canonical is None:
        warnings.append("quality_not_detected")
    if not had_group:
        warnings.append("release_group_inferred_nogroup")

    return ReleaseParseResult(
        raw_name=raw,
        normalized_name=normalized_full,
        identity=identity,
        quality=quality,
        edition=edition,
        provider=provider,
        release_group=release_group,
        attributes=attributes,
        hdr=hdr,
        video=video,
        audio=audio,
        languages=languages,
        regions=regions,
        unknown_tokens=unknown,
        parser_version=parser_version,
        warnings=tuple(warnings),
    )


def parse_quality(name: str) -> QualityInfo:
    """Return only the canonical quality portion of a release name."""

    normalized = normalize_release_name(name)
    return _quality(normalized)


def detect_edition(name: str) -> str | None:
    """Return the normalized built-in edition, if one is explicit."""

    return _edition_from_name(normalize_release_name(name))[0]


def detect_release_group(name: str) -> str:
    """Return the terminal group or the explicit ``NoGroup`` fallback."""

    normalized = normalize_release_name(name)
    return _release_group(_clean_raw_name(name), normalized)[0]


# Public aliases used by adapters and older experiments.
parse_release_name = parse_release


__all__ = [
    "QUALITY_PRECEDENCE",
    "normalize_release_name",
    "parse_release",
    "parse",
    "parse_name",
    "parse_release_name",
    "parse_quality",
    "detect_edition",
    "detect_release_group",
]
