"""The shipped Custom Formats must actually match the releases they describe.

A built-in is only as good as the parser vocabulary it is written against, so
these tests evaluate each one through the real evaluation path rather than
asserting on its stored definition.
"""

from __future__ import annotations

import pytest

from app.parser import parse_release_name
from app.services.builtin_formats import BUILTIN_FORMATS, condition_definition
from app.services.custom_formats import CustomFormat as EvaluationFormat
from app.services.custom_formats import evaluate_custom_format


BY_KEY = {item.key: item for item in BUILTIN_FORMATS}


def _matches(key: str, release: str) -> bool:
    builtin = BY_KEY[key]
    fmt = EvaluationFormat.from_dict(
        {
            "id": builtin.key,
            "name": builtin.name,
            "description": builtin.description,
            "media_scope": builtin.media_scope,
            "enabled": True,
            "condition_definition": condition_definition(builtin),
        }
    )
    return evaluate_custom_format(fmt, parse_release_name(release)).matched


def test_every_builtin_has_a_unique_key_and_at_least_one_condition() -> None:
    keys = [item.key for item in BUILTIN_FORMATS]
    assert len(keys) == len(set(keys))
    for item in BUILTIN_FORMATS:
        assert item.conditions, item.key
        assert item.name and item.description, item.key


@pytest.mark.parametrize(
    ("key", "release"),
    [
        # Video codecs, across the spellings release groups actually use.
        ("video-hevc", "Movie 2024 2160p BluRay REMUX HEVC-GRP"),
        ("video-hevc", "Movie 2024 1080p WEB-DL x265-GRP"),
        ("video-avc", "Movie 2024 1080p BluRay REMUX AVC-GRP"),
        ("video-avc", "Movie 2024 1080p WEB-DL H.264-GRP"),
        ("video-av1", "Movie 2024 2160p WEB-DL AV1-GRP"),
        # Dolby Digital Plus under all four spellings.
        ("audio-ddplus", "Movie 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("audio-ddplus", "Movie 2024 1080p WEB-DL DD+5.1 x265-GRP"),
        ("audio-ddplus", "Movie 2024 1080p WEB-DL EAC3 5.1-GRP"),
        ("audio-ddplus", "Movie 2024 1080p WEB-DL E-AC-3 5.1-GRP"),
        # Lossless and object audio.
        ("audio-truehd", "Movie 2024 2160p BluRay REMUX TrueHD 7.1 Atmos-GRP"),
        ("audio-atmos", "Movie 2024 2160p BluRay REMUX TrueHD 7.1 Atmos-GRP"),
        ("audio-dtshd-ma", "Movie 2024 1080p BluRay DTS-HD MA 5.1 x264-GRP"),
        ("audio-dts", "Movie 2024 1080p BluRay DTS 5.1 x264-GRP"),
        ("audio-dd", "Movie 2024 1080p BluRay AC3 2.0 x264-GRP"),
        ("audio-surround", "Movie 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        # HDR flavours are distinct values, so they score independently.
        ("hdr-dolby-vision", "Movie 2024 2160p WEB-DL DV HDR10+ x265-FLUX"),
        ("hdr-hdr10-plus", "Movie 2024 2160p WEB-DL DV HDR10+ x265-FLUX"),
        # Release traits.
        ("attr-repack-proper", "Movie 2024 1080p WEB-DL REPACK DDP5.1 x264-GRP"),
        ("attr-hybrid", "Movie 2024 2160p Hybrid BluRay REMUX HEVC-GRP"),
        # Streaming providers.
        ("web-amzn", "Show S01E01 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("web-atvp", "Show S01E01 2160p ATVP WEB-DL DDP5.1 H.265-NTb"),
        ("web-max", "Show S01E01 1080p HMAX WEB-DL DDP5.1 H.264-NTb"),
        ("web-paramount", "Show S01E01 1080p PMTP WEB-DL DDP5.1 H.264-NTb"),
        ("web-sho", "Show S01E01 1080p SHO WEB-DL DDP5.1 H.264-NTb"),
        ("web-stan", "Show S01E01 1080p STAN WEB-DL DDP5.1 H.264-NTb"),
        ("web-crave", "Show S01E01 1080p CRAV WEB-DL DDP5.1 H.264-NTb"),
        ("web-anime", "Show S01E01 1080p CR WEB-DL AAC2.0-NTb"),
        ("web-uk", "Show S01E01 1080p iP WEB-DL AAC2.0-NTb"),
    ],
)
def test_builtin_matches_a_representative_release(key: str, release: str) -> None:
    assert _matches(key, release), f"{key} should match {release}"


@pytest.mark.parametrize(
    ("key", "release"),
    [
        # A codec format must not match a different codec.
        ("video-hevc", "Movie 2024 1080p BluRay REMUX AVC-GRP"),
        ("video-av1", "Movie 2024 1080p WEB-DL x265-GRP"),
        # Plain DD+ is not lossless TrueHD, and plain DTS is not DTS-HD MA.
        ("audio-truehd", "Movie 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("audio-dtshd-ma", "Movie 2024 1080p BluRay DTS 5.1 x264-GRP"),
        # Dolby Digital must not be confused with Dolby Digital Plus.
        ("audio-dd", "Movie 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("audio-ddplus", "Movie 2024 1080p BluRay AC3 2.0 x264-GRP"),
        # A stereo release is not surround.
        ("audio-surround", "Movie 2024 1080p BluRay AC3 2.0 x264-GRP"),
        # HDR10+ is not Dolby Vision.
        ("hdr-dolby-vision", "Movie 2024 2160p WEB-DL HDR10+ x265-GRP"),
        # A provider format must not match another provider.
        ("web-amzn", "Show S01E01 1080p DSNP WEB-DL DDP5.1 H.264-NTb"),
        ("web-paramount", "Show S01E01 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("web-sho", "Show S01E01 1080p PMTP WEB-DL DDP5.1 H.264-NTb"),
    ],
)
def test_builtin_does_not_match_an_unrelated_release(key: str, release: str) -> None:
    assert not _matches(key, release), f"{key} should not match {release}"


REQUIRED_AUDIO = {
    "audio-pcm": "Movie 2024 2160p BluRay REMUX LPCM 5.1-GRP",
    "audio-flac": "Movie 2024 1080p BluRay FLAC 2.0 x264-GRP",
    "audio-opus": "Movie 2024 1080p WEB-DL AV1 Opus 5.1-GRP",
    "audio-aac": "Movie 2024 1080p WEB-DL AAC2.0 x264-GRP",
    "audio-dts": "Movie 2024 1080p BluRay DTS 5.1 x264-GRP",
    "audio-dts-es": "Movie 2024 1080p BluRay DTS-ES 6.1 x264-GRP",
    "audio-dtshd-ma": "Movie 2024 1080p BluRay DTS-HD MA 5.1 x264-GRP",
    "audio-dtsx": "Movie 2024 2160p BluRay REMUX DTS-X 7.1-GRP",
    "audio-ddplus": "Movie 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb",
    "audio-ddplus-atmos": "Movie 2024 2160p AMZN WEB-DL DDP5.1 Atmos H.265-NTb",
    "audio-truehd": "Movie 2024 2160p BluRay REMUX TrueHD 7.1-GRP",
    "audio-truehd-atmos": "Movie 2024 2160p BluRay REMUX TrueHD 7.1 Atmos-GRP",
}


@pytest.mark.parametrize(("key", "release"), sorted(REQUIRED_AUDIO.items()))
def test_every_required_audio_codec_has_a_working_builtin(key: str, release: str) -> None:
    assert key in BY_KEY, f"{key} is missing from the built-in catalogue"
    assert _matches(key, release), f"{key} should match {release}"


def test_atmos_matches_both_its_carrier_and_the_combined_format() -> None:
    """A TrueHD Atmos release is TrueHD, so both formats match and both score."""

    truehd_atmos = "Movie 2024 2160p BluRay REMUX TrueHD 7.1 Atmos-GRP"
    assert _matches("audio-truehd", truehd_atmos)
    assert _matches("audio-truehd-atmos", truehd_atmos)
    assert _matches("audio-atmos", truehd_atmos)
    # ...but it is not DD+ Atmos.
    assert not _matches("audio-ddplus-atmos", truehd_atmos)

    ddp_atmos = "Movie 2024 2160p AMZN WEB-DL DDP5.1 Atmos H.265-NTb"
    assert _matches("audio-ddplus", ddp_atmos)
    assert _matches("audio-ddplus-atmos", ddp_atmos)
    assert not _matches("audio-truehd-atmos", ddp_atmos)


def test_a_carrier_without_atmos_does_not_match_the_atmos_formats() -> None:
    plain = "Movie 2024 2160p BluRay REMUX TrueHD 7.1-GRP"
    assert _matches("audio-truehd", plain)
    assert not _matches("audio-truehd-atmos", plain)
    assert not _matches("audio-atmos", plain)


def test_dts_variants_do_not_bleed_into_each_other() -> None:
    assert not _matches("audio-dts", "Movie 2024 1080p BluRay DTS-ES 6.1 x264-GRP")
    assert not _matches("audio-dts", "Movie 2024 1080p BluRay DTS-HD MA 5.1 x264-GRP")
    assert not _matches("audio-dts-es", "Movie 2024 1080p BluRay DTS 5.1 x264-GRP")
    assert not _matches("audio-dtshd-ma", "Movie 2024 1080p BluRay DTS-ES 6.1 x264-GRP")
