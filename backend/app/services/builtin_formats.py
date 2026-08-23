"""Custom Formats that Medialogue ships and maintains.

Every condition here is written against the parser's own canonical vocabulary
(``app/parser/release_parser.py``), not against raw release text, so a format
matches whatever spelling a release group used: DDP5.1, DD+5.1, EAC3 and E-AC-3
all parse to ``DD+`` and are caught by one condition.

These rows are owned by Medialogue. Their name, description and conditions are
re-applied on every start, so a pattern fix reaches an existing install without
any action. Only ``enabled`` belongs to the operator, and it is never
overwritten. A built-in carries no score of its own — scoring is always a
Quality Profile decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BuiltinCondition:
    type: str
    value: Any = None
    pattern: str | None = None
    name: str | None = None
    negate: bool = False
    required: bool = False
    score_offset: int = 0


@dataclass(frozen=True)
class BuiltinFormat:
    key: str
    name: str
    description: str
    conditions: tuple[BuiltinCondition, ...]
    media_scope: str = "both"
    # Whether a fresh install starts with this format on. Descriptive formats
    # are on by default; opinionated or noisy ones start off so they never
    # silently change a ranking you did not ask for.
    default_enabled: bool = True
    group: str = "General"
    tags: tuple[str, ...] = field(default_factory=tuple)


def _codec(key: str, name: str, description: str, values: list[str], group: str) -> BuiltinFormat:
    return BuiltinFormat(
        key=key,
        name=name,
        description=description,
        group=group,
        conditions=(BuiltinCondition(type="video_codec" if group == "Video" else "audio_codec", value=values),),
    )


BUILTIN_FORMATS: tuple[BuiltinFormat, ...] = (
    # ---------------------------------------------------------------- video --
    _codec("video-hevc", "HEVC / x265",
           "H.265 in any of its spellings. Smaller files at equal quality, but some players and older TVs cannot decode it.",
           ["HEVC", "x265", "H.265"], "Video"),
    _codec("video-avc", "AVC / x264",
           "H.264 in any of its spellings. The most compatible video codec; plays on essentially anything.",
           ["AVC", "x264", "H.264"], "Video"),
    _codec("video-av1", "AV1",
           "Royalty-free codec with excellent efficiency. Hardware decoding is still uncommon on older devices.",
           ["AV1"], "Video"),
    _codec("video-vc1", "VC-1",
           "Legacy Microsoft codec found on early Blu-ray discs.",
           ["VC-1"], "Video"),
    _codec("video-mpeg2", "MPEG-2",
           "DVD-era codec. Very large files for the quality delivered.",
           ["MPEG-2"], "Video"),

    # ---------------------------------------------------------------- audio --
    # Ordered loosely best to worst. Atmos variants are separate from their
    # carrier codec because both match: a TrueHD Atmos release matches "Dolby
    # TrueHD" and "TrueHD Atmos", and a profile can score the pair higher.
    BuiltinFormat(
        key="audio-truehd-atmos", name="TrueHD Atmos", group="Audio",
        description="Lossless, object-based Dolby Atmos carried by TrueHD. It is primarily found on Blu-ray and UHD discs and remains compatible with TrueHD playback.",
        conditions=(BuiltinCondition(type="audio_codec", value=["TrueHD Atmos"]),)),
    BuiltinFormat(
        key="audio-truehd", name="Dolby TrueHD", group="Audio",
        description="Lossless Dolby audio with support for up to 7.1 channels, commonly found on Blu-ray. Unlike TrueHD Atmos, it carries no object-based Atmos data.",
        conditions=(BuiltinCondition(type="audio_codec", value=["TrueHD"]),)),
    BuiltinFormat(
        key="audio-dtsx", name="DTS:X", group="Audio",
        description="Lossless, object-based 3D audio carried by DTS-HD Master Audio. It is the DTS counterpart to Dolby Atmos and is mainly found on Blu-ray releases.",
        conditions=(BuiltinCondition(type="release_title", pattern=r"\b(dts[-_. ]?x)\b(?!\d)"),)),
    BuiltinFormat(
        key="audio-dtshd-ma", name="DTS-HD MA", group="Audio",
        description="Lossless surround audio commonly used on Blu-ray, with a DTS core for older equipment. It supports up to 7.1 channels and can also carry DTS:X.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DTS-HD MA"]),)),
    BuiltinFormat(
        key="audio-pcm", name="PCM / LPCM", group="Audio",
        description="Uncompressed, lossless audio that preserves the original signal exactly. It offers excellent quality but uses considerably more space than compressed lossless formats.",
        conditions=(BuiltinCondition(type="audio_codec", value=["PCM"]),)),
    BuiltinFormat(
        key="audio-flac", name="FLAC", group="Audio",
        description="Free, open lossless audio compression that preserves the original signal while reducing its size. It is widely used for archiving and high-quality playback.",
        conditions=(BuiltinCondition(type="audio_codec", value=["FLAC"]),)),
    BuiltinFormat(
        key="audio-ddplus-atmos", name="DD+ Atmos", group="Audio",
        description="Lossy, object-based Dolby Atmos carried by DD+. It is the Atmos format used by most streaming services, with less detail and dynamic range than TrueHD Atmos.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DD+ Atmos"]),)),
    BuiltinFormat(
        key="audio-ddplus", name="DD+", group="Audio",
        description="Lossy Dolby Digital Plus, also called DDP or E-AC-3. It supports up to 7.1 channels and is commonly used by streaming services for standard surround sound.",
        conditions=(BuiltinCondition(type="release_title", pattern=r"\bDD[P+](?!A)|\b(e[-_. ]?ac[-_. ]?3)\b"),)),
    BuiltinFormat(
        key="audio-dts-es", name="DTS-ES", group="Audio",
        description="A lossy 6.1 extension of DTS that adds a center-back surround channel. It appears in Discrete and Matrix variants and is most commonly found on DVDs.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DTS-ES"]),)),
    BuiltinFormat(
        key="audio-dtshd-hra", name="DTS-HD HRA", group="Audio",
        description="DTS-HD High Resolution Audio. Lossy, but at a far higher bitrate than core DTS or Dolby Digital, and the usual fallback when a disc does not carry DTS-HD Master Audio.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DTS-HD HRA"]),)),
    BuiltinFormat(
        key="audio-dts", name="DTS", group="Audio",
        description="Lossy multichannel DTS surround, typically supporting 5.1 channels at a higher bitrate than Dolby Digital. DTS-HD High Resolution has its own format. This format also includes the generic DTS-HD tag when no variant is named. Original note: Resolution audio.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DTS", "DTS-HD"]),)),
    BuiltinFormat(
        key="audio-dd", name="Dolby Digital", group="Audio",
        description="Lossy Dolby Digital, also called DD or AC-3. It supports up to 5.1 channels and is broadly compatible with broadcasts, DVDs, Blu-rays, and playback devices.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DD"]),)),
    BuiltinFormat(
        key="audio-aac", name="AAC", group="Audio",
        description="Efficient lossy audio designed as a successor to MP3. It is widely used by streaming platforms and generally delivers better quality than MP3 at the same bitrate.",
        conditions=(BuiltinCondition(type="audio_codec", value=["AAC"]),)),
    BuiltinFormat(
        key="audio-opus", name="Opus", group="Audio",
        description="Free, open lossy audio optimized for high quality at low bitrates and low latency. It is common in internet applications but has limited support on older playback hardware.",
        conditions=(BuiltinCondition(type="audio_codec", value=["Opus"]),)),
    # ------------------------------------------------------------------ HDR --
    BuiltinFormat(
        key="hdr-dolby-vision", name="Dolby Vision", group="Dynamic Range",
        description="Dynamic HDR that can adjust color and brightness scene by scene. It requires compatible equipment; a Dolby Vision-only release may not display correctly without an HDR fallback.",
        conditions=(BuiltinCondition(type="release_title", pattern=r"\b(dv|dovi|dolby[ .]?vision)\b"),)),
    BuiltinFormat(
        key="hdr-dv-hdr10", name="DV HDR10", group="Dynamic Range",
        description="Dolby Vision paired with an HDR10 fallback, giving compatible displays dynamic HDR while retaining reliable playback on standard HDR10 equipment.",
        conditions=(BuiltinCondition(type="release_title", pattern=r"^(?=.*\b(DV|DoVi|Dolby[ .]?Vision)\b)(?=.*\b(HDR(10)?(?!\+))\b)"),)),
    BuiltinFormat(
        key="hdr-dv-hdr10-plus", name="DV HDR10+", group="Dynamic Range",
        description="Dolby Vision paired with an HDR10+ base layer: dynamic metadata for Dolby Vision displays and dynamic metadata again for everything else. The best dynamic range a release can carry.",
        conditions=(BuiltinCondition(type="release_title", pattern=r"^(?=.*\b(DV|DoVi|Dolby[ .]?Vision)\b)(?=.*\bHDR10\+)"),)),
    BuiltinFormat(
        key="hdr-hdr10-plus", name="HDR10+", group="Dynamic Range",
        description="Dynamic HDR metadata that can tune color, brightness, and contrast for each frame. It falls back to standard HDR10 on unsupported displays.",
        conditions=(BuiltinCondition(type="hdr_type", value=["HDR10+"]),)),
    BuiltinFormat(
        key="hdr-hdr10", name="HDR10", group="Dynamic Range",
        description="The baseline HDR format, commonly tagged HDR or HDR10. It uses static metadata and is supported by essentially all HDR-capable displays.",
        conditions=(BuiltinCondition(type="release_title", pattern=r"\bHDR(10)?(?!\+)\b"),)),
    BuiltinFormat(
        key="hdr-hlg", name="HLG", group="Dynamic Range",
        description="Broadcast-focused HDR developed by the BBC and NHK. It is mainly found in cable, satellite, and over-the-air releases and may look dark on incompatible displays.",
        conditions=(BuiltinCondition(type="hdr_type", value=["HLG"]),)),
    BuiltinFormat(
        key="hdr-sdr", name="SDR", group="Dynamic Range",
        description="Identifies 2160p or 4K releases without an HDR format, allowing Quality Profiles to avoid UHD releases that are limited to standard dynamic range.",
        conditions=(
            BuiltinCondition(type="resolution", name="2160p", value=["2160p"], required=True),
            BuiltinCondition(type="release_title", name="HDR Formats", pattern=r"\bHDR(\b|\d)|\b(dv|dovi|dolby[ .]?v(ision)?)\b|\b(FraMeSToR|HQMUX|SICFoI)\b|\b(PQ)\b|\bHLG(\b|\d)", negate=True),
            BuiltinCondition(type="release_title", name="SDR", pattern=r"\bSDR\b"),
        )),

    # ------------------------------------------------------- release traits --
    BuiltinFormat(
        # Keep the original key so existing Quality Profile scores continue to
        # apply to Repack after Proper and Rerip become independent formats.
        key="attr-repack-proper", name="Repack", group="Release",
        description="A release group's corrected replacement for its own flawed release, such as one with corrupt data, audio-sync issues, or missing content. Later REPACK revisions receive higher offsets.",
        conditions=(
            BuiltinCondition(type="release_title", name="REPACK", pattern=r"\bREPACK\b", score_offset=1),
            BuiltinCondition(type="release_title", name="REPACK2", pattern=r"\bREPACK2\b", score_offset=2),
            BuiltinCondition(type="release_title", name="REPACK3", pattern=r"\bREPACK3\b", score_offset=3),
        )),
    BuiltinFormat(
        key="attr-proper", name="Proper", group="Release",
        description="A different release group's correction or improvement of another group's flawed release. Later PROPER revisions receive higher offsets.",
        conditions=(
            BuiltinCondition(type="release_title", name="PROPER", pattern=r"\bPROPER\b", score_offset=1),
            BuiltinCondition(type="release_title", name="PROPER2", pattern=r"\bPROPER2\b", score_offset=2),
            BuiltinCondition(type="release_title", name="PROPER3", pattern=r"\bPROPER3\b", score_offset=3),
        )),
    BuiltinFormat(
        key="attr-rerip", name="Rerip", group="Release",
        description="A corrected replacement for a flawed disc rip. Later RERIP revisions receive higher offsets so the newest correction can outrank earlier attempts.",
        conditions=(
            BuiltinCondition(type="release_title", name="RERIP", pattern=r"\bRERIP\b", score_offset=1),
            BuiltinCondition(type="release_title", name="RERIP2", pattern=r"\bRERIP2\b", score_offset=2),
            BuiltinCondition(type="release_title", name="RERIP3", pattern=r"\bRERIP3\b", score_offset=3),
        )),
    BuiltinFormat(
        key="attr-hybrid", name="Hybrid Release", group="Release",
        description="Built from more than one source, usually to combine the best video with the best audio or subtitles.",
        conditions=(BuiltinCondition(type="release_attribute", value=["Hybrid"]),)),
    BuiltinFormat(
        key="attr-re-encode", name="Re-encode", group="Release",
        description="Re-encoded from an existing encode rather than the source. Generation loss on top of generation loss; usually scored negative.",
        default_enabled=False,
        conditions=(BuiltinCondition(type="release_attribute", value=["RE-ENCODE"]),)),
    BuiltinFormat(
        key="attr-internal-limited", name="Internal / Limited", group="Release",
        description="Scene designations that do not describe quality on their own. Off by default; enable it if your trackers make these meaningful.",
        default_enabled=False,
        conditions=(BuiltinCondition(type="release_attribute", value=["INTERNAL", "LIMITED"]),)),

    # ------------------------------------------------------------ providers --
    BuiltinFormat(
        key="web-amzn", name="Amazon (AMZN)", group="Streaming",
        description="A WEB release sourced from Amazon Prime Video. Provider formats identify the storefront or service; your Quality Profile decides whether to prefer it.",
        conditions=(BuiltinCondition(type="web_provider", value=["AMZN"]),)),
    BuiltinFormat(
        key="web-dsnp", name="Disney+ (DSNP)", group="Streaming",
        description="A WEB release sourced from Disney+. Provider formats identify the storefront or service; your Quality Profile decides whether to prefer it.",
        conditions=(BuiltinCondition(type="web_provider", value=["DSNP"]),)),
    BuiltinFormat(
        key="web-nf", name="Netflix (NF)", group="Streaming",
        description="A WEB release sourced from Netflix. Provider formats identify the storefront or service; your Quality Profile decides whether to prefer it.",
        conditions=(BuiltinCondition(type="web_provider", value=["NF"]),)),
    BuiltinFormat(
        key="web-atvp", name="Apple TV+ (ATVP)", group="Streaming",
        description="A WEB release sourced from the Apple TV+ streaming service. This is separate from iTunes, Apple's digital store.",
        conditions=(BuiltinCondition(type="web_provider", value=["ATVP"]),)),
    BuiltinFormat(
        key="web-bcore", name="Bravia Core (BCORE)", group="Streaming",
        description="A WEB release sourced from Sony's Bravia Core service, commonly identified by the BCORE provider tag.",
        conditions=(BuiltinCondition(type="web_provider", value=["BCORE"]),)),
    BuiltinFormat(
        key="web-max", name="Max / HBO Max", group="Streaming",
        description="A WEB release sourced from Max or its former HBO Max branding, covering both MAX and HMAX provider tags.",
        conditions=(BuiltinCondition(type="web_provider", value=["MAX", "HMAX"]),)),
    BuiltinFormat(
        key="web-hulu", name="Hulu", group="Streaming",
        description="A WEB release sourced from Hulu. Provider formats identify the service; your Quality Profile decides whether to prefer it.",
        conditions=(BuiltinCondition(type="web_provider", value=["HULU"]),)),
    BuiltinFormat(
        key="web-paramount", name="Paramount+ (PMTP)", group="Streaming",
        description="A WEB release sourced from Paramount+, commonly tagged PMTP by release groups.",
        conditions=(BuiltinCondition(type="web_provider", value=["PMTP"]),)),
    BuiltinFormat(
        key="web-sho", name="Showtime (SHO)", group="Streaming",
        description="A WEB release sourced from Showtime, commonly identified by the SHO provider tag.",
        conditions=(BuiltinCondition(type="web_provider", value=["SHO"]),)),
    BuiltinFormat(
        key="web-peacock", name="Peacock (PCOK)", group="Streaming",
        description="A WEB release sourced from Peacock, commonly identified by the PCOK provider tag.",
        conditions=(BuiltinCondition(type="web_provider", value=["PCOK"]),)),
    BuiltinFormat(
        key="web-itunes", name="iTunes", group="Streaming",
        description="A movie or episode sourced from Apple's iTunes digital store. This is distinct from the Apple TV+ streaming service.",
        conditions=(BuiltinCondition(type="web_provider", value=["iT"]),)),
    BuiltinFormat(
        key="web-movies-anywhere", name="Movies Anywhere (MA)", group="Streaming",
        description="A movie sourced from Movies Anywhere, the digital locker that connects purchases from participating stores and studios.",
        conditions=(BuiltinCondition(type="web_provider", value=["MA"]),)),
)


BUILTIN_KEYS = frozenset(item.key for item in BUILTIN_FORMATS)

# This is the starting policy for the editable profile created on a fresh
# install. These values are copied into the profile once; they are not part of
# Custom Format evaluation and are never re-applied to an existing profile.
DEFAULT_QUALITY_PROFILE_NAME = "Default"
DEFAULT_QUALITY_PROFILE_MARKER = "medialogue-default-quality-profile-v1"
# Full-disc DVD rips are whole ISO/VIDEO_TS structures rather than a playable
# file, so the shipped profile leaves them off. The definitions still exist and
# can be re-enabled on any profile; they are simply not part of the default.
DEFAULT_EXCLUDED_QUALITY_DEFINITIONS: frozenset[str] = frozenset({
    "Full Disc DVD5",
    "Full Disc DVD9",
    "Full Disc DVD5/DVD9",
})


DEFAULT_FORMAT_SCORES: dict[str, int] = {
    # Video: efficient modern codecs first, legacy codecs last.
    "video-av1": 40,
    "video-hevc": 35,
    "video-avc": 25,
    "video-vc1": -20,
    "video-mpeg2": -40,
    # Audio: lossless/object-based formats first; compatibility codecs stay
    # neutral or receive a small penalty rather than being rejected.
    "audio-truehd-atmos": 60,
    "audio-dtsx": 50,
    "audio-truehd": 55,
    "audio-dtshd-ma": 45,
    "audio-pcm": 35,
    "audio-flac": 40,
    "audio-ddplus-atmos": 25,
    "audio-dtshd-hra": 20,
    "audio-ddplus": 15,
    "audio-dts-es": 10,
    "audio-dts": 5,
    "audio-dd": 0,
    "audio-aac": -5,
    "audio-opus": -10,
    # Dynamic range: Dolby Vision with a dynamic base layer is the ceiling,
    # then Dolby Vision, then the static formats. SDR is preferred over HLG,
    # which can look washed out on displays that do not handle it.
    "hdr-dv-hdr10-plus": 60,
    "hdr-dv-hdr10": 50,
    "hdr-hdr10-plus": 40,
    "hdr-dolby-vision": 45,
    "hdr-hdr10": 20,
    "hdr-hlg": 0,
    "hdr-sdr": 10,
    # Release traits: corrected releases are preferred; re-encodes are
    # strongly discouraged. Internal/provider-neutral traits stay neutral.
    "attr-repack-proper": 20,
    "attr-proper": 18,
    "attr-rerip": 16,
    "attr-hybrid": 10,
    "attr-internal-limited": 0,
    "attr-re-encode": -40,
    # Provider labels are evidence, not quality, so they stay neutral — with
    # one exception: Bravia Core ships at bitrates no other service matches.
    "web-amzn": 0,
    "web-dsnp": 0,
    "web-nf": 0,
    "web-atvp": 0,
    "web-bcore": 25,
    "web-max": 0,
    "web-hulu": 0,
    "web-paramount": 0,
    "web-sho": 0,
    "web-peacock": 0,
    "web-itunes": 0,
    "web-movies-anywhere": 0,
}

if set(DEFAULT_FORMAT_SCORES) != BUILTIN_KEYS:
    raise RuntimeError("Default Quality Profile scores must cover every built-in Custom Format exactly once")


def condition_definition(builtin: BuiltinFormat) -> dict[str, Any]:
    """Render a built-in into the stored condition-definition shape."""

    from app.schemas.custom_formats import CUSTOM_FORMAT_SCHEMA_VERSION

    def regex_pattern(condition: BuiltinCondition) -> str:
        if condition.pattern is not None:
            return condition.pattern
        values = condition.value if isinstance(condition.value, list) else [condition.value]
        return rf"^(?:{'|'.join(re.escape(str(value)) for value in values if value is not None)})$"

    return {
        "schema_version": CUSTOM_FORMAT_SCHEMA_VERSION,
        "conditions": [
            {
                "id": f"{builtin.key}-{index}",
                "type": condition.type,
                "value": None,
                "name": condition.name or builtin.name,
                "pattern": regex_pattern(condition),
                "required": condition.required,
                "negate": condition.negate,
                "case_sensitive": False,
                "score_offset": condition.score_offset,
                "group": None,
            }
            for index, condition in enumerate(builtin.conditions)
        ],
    }
