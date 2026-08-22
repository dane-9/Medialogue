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

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BuiltinCondition:
    type: str
    value: Any
    name: str | None = None
    negate: bool = False
    required: bool = False


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
        description="Dolby Atmos carried on lossless TrueHD. The best audio a disc release can carry.",
        conditions=(BuiltinCondition(type="audio_codec", value=["TrueHD Atmos"]),)),
    BuiltinFormat(
        key="audio-truehd", name="Dolby TrueHD", group="Audio",
        description="Lossless Dolby audio. Disc-quality and large. Also matches TrueHD Atmos, which has its own format on top.",
        conditions=(BuiltinCondition(type="audio_codec", value=["TrueHD"]),)),
    BuiltinFormat(
        key="audio-dtsx", name="DTS:X", group="Audio",
        description="DTS object-based audio, the DTS counterpart to Atmos.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DTS:X"]),)),
    BuiltinFormat(
        key="audio-dtshd-ma", name="DTS-HD MA", group="Audio",
        description="Lossless DTS Master Audio. The DTS equivalent of TrueHD.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DTS-HD MA"]),)),
    BuiltinFormat(
        key="audio-pcm", name="PCM / LPCM", group="Audio",
        description="Uncompressed audio. Lossless, but far larger than FLAC or TrueHD for identical content.",
        conditions=(BuiltinCondition(type="audio_codec", value=["PCM"]),)),
    BuiltinFormat(
        key="audio-flac", name="FLAC", group="Audio",
        description="Lossless compressed audio, most often seen on remuxes and anime releases.",
        conditions=(BuiltinCondition(type="audio_codec", value=["FLAC"]),)),
    BuiltinFormat(
        key="audio-ddplus-atmos", name="DD+ Atmos", group="Audio",
        description="Dolby Atmos carried on lossy DD+. What streaming services ship when they advertise Atmos.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DD+ Atmos"]),)),
    BuiltinFormat(
        key="audio-atmos", name="Dolby Atmos (any carrier)", group="Audio",
        description="Any Atmos track regardless of the codec beneath it. Use the carrier-specific formats above when you care whether it is lossless.",
        conditions=(BuiltinCondition(type="audio_codec", value=["Atmos"]),)),
    BuiltinFormat(
        key="audio-ddplus", name="Dolby Digital Plus", group="Audio",
        description="DD+ / DDP / E-AC-3 — one lossy codec under four spellings, and what almost every WEB-DL ships. The parser normalises them, so one condition catches all of them.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DD+"]),)),
    BuiltinFormat(
        key="audio-dts-es", name="DTS-ES", group="Audio",
        description="DTS Extended Surround, in both its Discrete and Matrix variants. A 6.1 extension of core DTS.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DTS-ES"]),)),
    BuiltinFormat(
        key="audio-dts", name="DTS", group="Audio",
        description="Core DTS, plus DTS-HD High Resolution. Everything DTS that is not the lossless MA variant or an ES/X extension.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DTS", "DTS-HD", "DTS-HD HRA"]),)),
    BuiltinFormat(
        key="audio-dd", name="Dolby Digital", group="Audio",
        description="DD / AC-3. The long-standing lossy standard; plays on essentially anything.",
        conditions=(BuiltinCondition(type="audio_codec", value=["DD"]),)),
    BuiltinFormat(
        key="audio-aac", name="AAC", group="Audio",
        description="Efficient lossy audio, common on web and anime releases.",
        conditions=(BuiltinCondition(type="audio_codec", value=["AAC"]),)),
    BuiltinFormat(
        key="audio-opus", name="Opus", group="Audio",
        description="Modern lossy codec with excellent quality per bit, but poor support on TVs and set-top players.",
        conditions=(BuiltinCondition(type="audio_codec", value=["Opus"]),)),
    BuiltinFormat(
        key="audio-surround", name="Surround (5.1 or better)", group="Audio",
        description="Any multichannel layout. Useful as a floor so a stereo-only release never outranks a surround one.",
        conditions=(BuiltinCondition(type="audio_channels", value=["5.1", "6.1", "7.1", "9.1"]),)),
    BuiltinFormat(
        key="audio-stereo", name="Stereo only", group="Audio",
        description="Two-channel audio. Usually scored negative for films, and left alone for stand-up or older TV.",
        default_enabled=False,
        conditions=(BuiltinCondition(type="audio_channels", value=["2.0", "1.0"]),)),

    # ------------------------------------------------------------------ HDR --
    BuiltinFormat(
        key="hdr-dolby-vision", name="Dolby Vision", group="HDR",
        description="Dynamic HDR metadata. Needs a Dolby Vision capable display; on other screens a DV-only release can look washed out.",
        conditions=(BuiltinCondition(type="hdr_type", value=["Dolby Vision"]),)),
    BuiltinFormat(
        key="hdr-hdr10-plus", name="HDR10+", group="HDR",
        description="Samsung's dynamic HDR metadata. Falls back cleanly to HDR10 on displays that do not support it.",
        conditions=(BuiltinCondition(type="hdr_type", value=["HDR10+"]),)),
    BuiltinFormat(
        key="hdr-hdr10", name="HDR10", group="HDR",
        description="Static HDR metadata. The baseline every HDR display understands.",
        conditions=(BuiltinCondition(type="hdr_type", value=["HDR10"]),)),
    BuiltinFormat(
        key="hdr-hlg", name="HLG", group="HDR",
        description="Broadcast HDR, seen mostly on HDTV captures.",
        conditions=(BuiltinCondition(type="hdr_type", value=["HLG"]),)),
    BuiltinFormat(
        key="hdr-any", name="HDR (any)", group="HDR",
        description="Matches a release advertising HDR without naming which flavour. Score this low and let the specific HDR formats above carry the real preference.",
        conditions=(BuiltinCondition(type="hdr_type", value=["HDR"]),)),

    # ------------------------------------------------------- release traits --
    BuiltinFormat(
        key="attr-repack-proper", name="Repack / Proper", group="Release",
        description="A corrected re-release. Usually worth a small positive score: it exists because the first attempt was broken.",
        conditions=(BuiltinCondition(type="release_attribute", value=["REPACK", "PROPER", "REAL", "RERIP"]),)),
    BuiltinFormat(
        key="attr-hybrid", name="Hybrid Release", group="Release",
        description="Built from more than one source, usually to combine the best video with the best audio or subtitles.",
        conditions=(BuiltinCondition(type="release_attribute", value=["Hybrid"]),)),
    BuiltinFormat(
        key="attr-remastered", name="Remastered", group="Release",
        description="Sourced from a newer master. Often a genuine improvement over the original disc.",
        conditions=(BuiltinCondition(type="release_attribute", value=["REMASTERED"]),)),
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
        description="Amazon web source. Generally high bitrate, and one of the better WEB-DL sources.",
        conditions=(BuiltinCondition(type="web_provider", value=["AMZN"]),)),
    BuiltinFormat(
        key="web-dsnp", name="Disney+ (DSNP)", group="Streaming",
        description="Disney+ web source.",
        conditions=(BuiltinCondition(type="web_provider", value=["DSNP"]),)),
    BuiltinFormat(
        key="web-nf", name="Netflix (NF)", group="Streaming",
        description="Netflix web source.",
        conditions=(BuiltinCondition(type="web_provider", value=["NF"]),)),
    BuiltinFormat(
        key="web-atvp", name="Apple TV+ (ATVP)", group="Streaming",
        description="Apple TV+ web source. Typically the highest bitrate of the major services.",
        conditions=(BuiltinCondition(type="web_provider", value=["ATVP"]),)),
    BuiltinFormat(
        key="web-max", name="Max / HBO Max", group="Streaming",
        description="Max web source, under both its current and former provider tags.",
        conditions=(BuiltinCondition(type="web_provider", value=["MAX", "HMAX"]),)),
    BuiltinFormat(
        key="web-hulu", name="Hulu", group="Streaming",
        description="Hulu web source.",
        conditions=(BuiltinCondition(type="web_provider", value=["HULU"]),)),
    BuiltinFormat(
        key="web-paramount", name="Paramount+ (PMTP)", group="Streaming",
        description="Paramount+ web source, tagged PMTP by most release groups.",
        conditions=(BuiltinCondition(type="web_provider", value=["PMTP"]),)),
    BuiltinFormat(
        key="web-sho", name="Showtime (SHO)", group="Streaming",
        description="Showtime web source.",
        conditions=(BuiltinCondition(type="web_provider", value=["SHO"]),)),
    BuiltinFormat(
        key="web-stan", name="Stan", group="Streaming",
        description="Stan web source (Australia).",
        conditions=(BuiltinCondition(type="web_provider", value=["STAN"]),)),
    BuiltinFormat(
        key="web-crave", name="Crave (CRAV)", group="Streaming",
        description="Crave web source (Canada).",
        conditions=(BuiltinCondition(type="web_provider", value=["CRAV"]),)),
    BuiltinFormat(
        key="web-anime", name="Crunchyroll / Funimation / HIDIVE", group="Streaming",
        description="The anime streaming services, which release under CR, FUNI and HIDIVE.",
        conditions=(BuiltinCondition(type="web_provider", value=["CR", "FUNI", "HIDIVE"]),)),
    BuiltinFormat(
        key="web-uk", name="UK broadcasters", group="Streaming",
        description="BBC iPlayer, Channel 4 and Channel 5 web sources.",
        conditions=(BuiltinCondition(type="web_provider", value=["iP", "ALL4", "MY5", "UKTV"]),)),
    BuiltinFormat(
        key="web-free", name="Free ad-supported (Roku / Tubi / Pluto)", group="Streaming",
        description="Ad-supported services. Usually lower bitrate than the subscription platforms.",
        default_enabled=False,
        conditions=(BuiltinCondition(type="web_provider", value=["ROKU", "TUBI", "PLUTO"]),)),
    BuiltinFormat(
        key="web-peacock", name="Peacock (PCOK)", group="Streaming",
        description="Peacock web source.",
        conditions=(BuiltinCondition(type="web_provider", value=["PCOK"]),)),
    BuiltinFormat(
        key="web-itunes", name="iTunes / Movies Anywhere", group="Streaming",
        description="Apple iTunes and Movies Anywhere digital sources.",
        conditions=(BuiltinCondition(type="web_provider", value=["iT", "MA"]),)),
)


BUILTIN_KEYS = frozenset(item.key for item in BUILTIN_FORMATS)


def condition_definition(builtin: BuiltinFormat) -> dict[str, Any]:
    """Render a built-in into the stored condition-definition shape."""

    from app.schemas.custom_formats import CUSTOM_FORMAT_SCHEMA_VERSION

    return {
        "schema_version": CUSTOM_FORMAT_SCHEMA_VERSION,
        "conditions": [
            {
                "id": f"{builtin.key}-{index}",
                "type": condition.type,
                "value": condition.value,
                "name": condition.name,
                "pattern": None,
                "required": condition.required,
                "negate": condition.negate,
                "case_sensitive": False,
                "group": None,
            }
            for index, condition in enumerate(builtin.conditions)
        ],
    }
