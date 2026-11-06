"""The shipped Custom Formats must actually match the releases they describe.

A built-in is only as good as the parser vocabulary it is written against, so
these tests evaluate each one through the real evaluation path rather than
asserting on its stored definition.
"""

from __future__ import annotations

import pytest

from app.parser import parse_release
from app.services.builtin_formats import BUILTIN_FORMATS, condition_definition
from app.services.custom_formats import CustomFormat as EvaluationFormat
from app.services.custom_formats import evaluate_custom_format


BY_KEY = {item.key: item for item in BUILTIN_FORMATS}


def _evaluation(key: str, release: str, *, score: int = 0):
    builtin = BY_KEY[key]
    fmt = EvaluationFormat.from_dict(
        {
            "id": builtin.key,
            "name": builtin.name,
            "description": builtin.description,
            "enabled": True,
            "condition_definition": condition_definition(builtin),
        }
    )
    return evaluate_custom_format(fmt, parse_release(release), score=score)


def _matches(key: str, release: str) -> bool:
    return _evaluation(key, release).matched


def test_every_builtin_has_a_unique_key_and_at_least_one_condition() -> None:
    keys = [item.key for item in BUILTIN_FORMATS]
    assert len(keys) == len(set(keys))
    for item in BUILTIN_FORMATS:
        assert item.conditions, item.key
        assert item.name and item.description, item.key
        stored = condition_definition(item)["conditions"]
        assert all(condition["name"] for condition in stored), item.key
        for source, condition in zip(item.conditions, stored, strict=True):
            assert condition["name"] == (source.name or item.name), item.key


@pytest.mark.parametrize(
    ("key", "release"),
    [
        ("video-hevc", "Movie 2024 2160p BluRay REMUX HEVC-GRP"),
        ("video-hevc", "Movie 2024 1080p WEB-DL x265-GRP"),
        ("video-avc", "Movie 2024 1080p BluRay REMUX AVC-GRP"),
        ("video-avc", "Movie 2024 1080p WEB-DL H.264-GRP"),
        ("video-av1", "Movie 2024 2160p WEB-DL AV1-GRP"),
        ("audio-ddplus", "Movie 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("audio-ddplus", "Movie 2024 1080p WEB-DL DD+5.1 x265-GRP"),
        ("audio-ddplus", "Movie 2024 1080p WEB-DL EAC3 5.1-GRP"),
        ("audio-ddplus", "Movie 2024 1080p WEB-DL E-AC-3 5.1-GRP"),
        ("audio-dtsx", "Movie 2024 2160p BluRay REMUX DTS-X 7.1-GRP"),
        ("audio-truehd", "Movie 2024 2160p BluRay REMUX TrueHD 7.1 Atmos-GRP"),
        ("audio-dtshd-ma", "Movie 2024 1080p BluRay DTS-HD MA 5.1 x264-GRP"),
        ("audio-dts", "Movie 2024 1080p BluRay DTS 5.1 x264-GRP"),
        ("audio-dd", "Movie 2024 1080p BluRay AC3 2.0 x264-GRP"),
        ("hdr-dolby-vision", "Movie 2024 2160p WEB-DL DV HDR10+ x265-FLUX"),
        ("hdr-dolby-vision", "Movie 2024 2160p WEB-DL DOVI x265-FLUX"),
        ("hdr-dolby-vision", "Movie 2024 2160p WEB-DL Dolby.Vision x265-FLUX"),
        ("hdr-dv-hdr10", "Movie 2024 2160p WEB-DL DoVi HDR10 x265-FLUX"),
        ("hdr-dv-hdr10", "Movie 2024 2160p UHD BluRay REMUX DV HDR HEVC-GRP"),
        ("hdr-hdr10-plus", "Movie 2024 2160p WEB-DL DV HDR10+ x265-FLUX"),
        ("hdr-hdr10", "Movie 2024 2160p WEB-DL HDR x265-FLUX"),
        ("hdr-hdr10", "Movie 2024 2160p WEB-DL HDR10 x265-FLUX"),
        ("hdr-sdr", "Movie 2024 2160p BluRay SDR x265-GRP"),
        ("hdr-sdr", "Movie 2024 2160p BluRay x265-GRP"),
        ("attr-repack-proper", "Movie 2024 1080p WEB-DL REPACK DDP5.1 x264-GRP"),
        ("attr-repack-proper", "Movie 2024 1080p WEB-DL REPACK2 DDP5.1 x264-GRP"),
        ("attr-repack-proper", "Movie 2024 1080p WEB-DL REPACK3 DDP5.1 x264-GRP"),
        ("attr-proper", "Movie 2024 1080p WEB-DL PROPER DDP5.1 x264-GRP"),
        ("attr-proper", "Movie 2024 1080p WEB-DL PROPER2 DDP5.1 x264-GRP"),
        ("attr-proper", "Movie 2024 1080p WEB-DL PROPER3 DDP5.1 x264-GRP"),
        ("attr-rerip", "Movie 2024 1080p WEB-DL RERIP DDP5.1 x264-GRP"),
        ("attr-rerip", "Movie 2024 1080p WEB-DL RERIP2 DDP5.1 x264-GRP"),
        ("attr-rerip", "Movie 2024 1080p WEB-DL RERIP3 DDP5.1 x264-GRP"),
        ("attr-hybrid", "Movie 2024 2160p Hybrid BluRay REMUX HEVC-GRP"),
        ("web-amzn", "Show S01E01 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("web-atvp", "Show S01E01 2160p ATVP WEB-DL DDP5.1 H.265-NTb"),
        ("web-bcore", "Movie 2024 2160p BCORE WEB-DL DDP5.1 H.265-GRP"),
        ("web-max", "Show S01E01 1080p HMAX WEB-DL DDP5.1 H.264-NTb"),
        ("web-paramount", "Show S01E01 1080p PMTP WEB-DL DDP5.1 H.264-NTb"),
        ("web-sho", "Show S01E01 1080p SHO WEB-DL DDP5.1 H.264-NTb"),
        ("web-itunes", "Movie 2024 2160p iT WEB-DL DDP5.1 H.265-GRP"),
        ("web-movies-anywhere", "Movie 2024 2160p MA WEB-DL DDP5.1 H.265-GRP"),
    ],
)
def test_builtin_matches_a_representative_release(key: str, release: str) -> None:
    assert _matches(key, release), f"{key} should match {release}"


@pytest.mark.parametrize(
    ("key", "tag", "offset"),
    [
        ("attr-repack-proper", "REPACK", 1),
        ("attr-repack-proper", "REPACK2", 2),
        ("attr-repack-proper", "REPACK3", 3),
        ("attr-proper", "PROPER", 1),
        ("attr-proper", "PROPER2", 2),
        ("attr-proper", "PROPER3", 3),
        ("attr-rerip", "RERIP", 1),
        ("attr-rerip", "RERIP2", 2),
        ("attr-rerip", "RERIP3", 3),
    ],
)
def test_revision_formats_apply_the_number_as_the_score_offset(key: str, tag: str, offset: int) -> None:
    result = _evaluation(key, f"Movie 2024 1080p WEB-DL {tag} DDP5.1 x264-GRP", score=10)
    assert result.score_offset == offset
    assert result.score_contribution == 10 + offset


@pytest.mark.parametrize(
    ("key", "release"),
    [
        ("video-hevc", "Movie 2024 1080p BluRay REMUX AVC-GRP"),
        ("video-av1", "Movie 2024 1080p WEB-DL x265-GRP"),
        ("audio-truehd", "Movie 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("audio-dtshd-ma", "Movie 2024 1080p BluRay DTS 5.1 x264-GRP"),
        ("audio-dd", "Movie 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("audio-ddplus", "Movie 2024 1080p BluRay AC3 2.0 x264-GRP"),
        ("hdr-dolby-vision", "Movie 2024 2160p WEB-DL HDR10+ x265-GRP"),
        ("hdr-dv-hdr10", "Movie 2024 2160p WEB-DL DV HDR10+ x265-GRP"),
        ("hdr-hdr10", "Movie 2024 2160p WEB-DL HDR10+ x265-GRP"),
        ("hdr-sdr", "Movie 2024 1080p BluRay SDR x265-GRP"),
        ("hdr-sdr", "Movie 2024 2160p WEB-DL HDR10 x265-GRP"),
        ("hdr-sdr", "Movie 2024 2160p WEB-DL DOVI x265-GRP"),
        ("hdr-sdr", "Movie 2024 2160p HDTV HLG x265-GRP"),
        ("web-amzn", "Show S01E01 1080p DSNP WEB-DL DDP5.1 H.264-NTb"),
        ("web-paramount", "Show S01E01 1080p AMZN WEB-DL DDP5.1 H.264-NTb"),
        ("web-sho", "Show S01E01 1080p PMTP WEB-DL DDP5.1 H.264-NTb"),
        ("web-itunes", "Movie 2024 2160p MA WEB-DL DDP5.1 H.265-GRP"),
        ("web-movies-anywhere", "Movie 2024 2160p iT WEB-DL DDP5.1 H.265-GRP"),
        ("web-movies-anywhere", "Movie 2024 1080p BluRay DTS-HD MA 5.1 x264-GRP"),
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


def test_atmos_matches_only_its_specific_carrier_format() -> None:

    truehd_atmos = "Movie 2024 2160p BluRay REMUX TrueHD 7.1 Atmos-GRP"
    assert _matches("audio-truehd", truehd_atmos)
    assert _matches("audio-truehd-atmos", truehd_atmos)
    assert not _matches("audio-ddplus-atmos", truehd_atmos)

    ddp_atmos = "Movie 2024 2160p AMZN WEB-DL DDP5.1 Atmos H.265-NTb"
    assert _matches("audio-ddplus", ddp_atmos)
    assert _matches("audio-ddplus-atmos", ddp_atmos)
    assert not _matches("audio-truehd-atmos", ddp_atmos)


def test_a_carrier_without_atmos_does_not_match_the_atmos_formats() -> None:
    plain = "Movie 2024 2160p BluRay REMUX TrueHD 7.1-GRP"
    assert _matches("audio-truehd", plain)
    assert not _matches("audio-truehd-atmos", plain)


def test_withdrawn_catch_all_formats_are_not_in_the_catalogue() -> None:
    assert {
        "audio-atmos",
        "audio-surround",
        "audio-stereo",
        "hdr-any",
        "attr-remastered",
        "web-free",
        "web-stan",
        "web-uk",
        "web-anime",
        "web-crave",
    }.isdisjoint(BY_KEY)


def test_dts_variants_do_not_bleed_into_each_other() -> None:
    assert not _matches("audio-dts", "Movie 2024 1080p BluRay DTS-ES 6.1 x264-GRP")
    assert not _matches("audio-dts", "Movie 2024 1080p BluRay DTS-HD MA 5.1 x264-GRP")
    assert not _matches("audio-dts-es", "Movie 2024 1080p BluRay DTS 5.1 x264-GRP")
    assert not _matches("audio-dtshd-ma", "Movie 2024 1080p BluRay DTS-ES 6.1 x264-GRP")


def test_default_profile_scores_reflect_the_intended_ranking() -> None:
    """The orderings that were asked for explicitly, pinned so a later edit
    cannot quietly invert them."""

    from app.services.builtin_formats import DEFAULT_FORMAT_SCORES as scores

    assert scores["audio-truehd"] > scores["audio-dtsx"]
    assert scores["audio-flac"] > scores["audio-pcm"]
    assert scores["hdr-dolby-vision"] > scores["hdr-hdr10-plus"]
    assert scores["hdr-sdr"] == 10
    assert scores["hdr-hlg"] == 0
    dynamic = {key: value for key, value in scores.items() if key.startswith("hdr-")}
    assert max(dynamic, key=lambda key: dynamic[key]) == "hdr-dv-hdr10-plus"
    streaming = {key: value for key, value in scores.items() if key.startswith("web-")}
    assert streaming["web-bcore"] > 0
    assert all(value == 0 for key, value in streaming.items() if key != "web-bcore")


def test_unsafe_and_full_disc_qualities_are_left_out_of_the_default_profile() -> None:
    from app.services.builtin_formats import DEFAULT_EXCLUDED_QUALITY_DEFINITIONS
    from app.parser import list_quality_definitions

    known = {item.name for item in list_quality_definitions()}
    assert DEFAULT_EXCLUDED_QUALITY_DEFINITIONS <= known
    assert "1080i Encode" in DEFAULT_EXCLUDED_QUALITY_DEFINITIONS
    assert {name for name in known if name.startswith("Full Disc")} == {
        name for name in DEFAULT_EXCLUDED_QUALITY_DEFINITIONS if name.startswith("Full Disc")
    }


def test_interlaced_bluray_qualities_are_first_class_definitions() -> None:
    from app.parser import list_quality_definitions

    definitions = {item.name: item for item in list_quality_definitions()}

    assert definitions["1080i BluRay REMUX"].scan_type == "interlaced"
    assert definitions["1080i Encode"].scan_type == "interlaced"
    assert definitions["1080p BluRay REMUX"].rank > definitions["1080i BluRay REMUX"].rank
    assert definitions["1080i BluRay REMUX"].rank > definitions["720p BluRay REMUX"].rank


@pytest.mark.parametrize(
    ("key", "release"),
    [
        ("audio-dtshd-hra", "Movie 2024 1080p BluRay DTS-HD HRA 5.1 x264-GRP"),
        ("hdr-dv-hdr10-plus", "Movie 2024 2160p WEB-DL DV HDR10+ x265-FLUX"),
    ],
)
def test_new_builtins_match_their_releases(key: str, release: str) -> None:
    assert _matches(key, release)


@pytest.mark.parametrize(
    ("key", "release"),
    [
        ("audio-dts", "Movie 2024 1080p BluRay DTS-HD HRA 5.1 x264-GRP"),
        ("hdr-dv-hdr10-plus", "Movie 2024 2160p WEB-DL DV HDR10 x265-FLUX"),
        ("hdr-dv-hdr10", "Movie 2024 2160p WEB-DL DV HDR10+ x265-FLUX"),
        ("hdr-dv-hdr10-plus", "Movie 2024 2160p WEB-DL HDR10+ x265-FLUX"),
    ],
)
def test_new_builtins_stay_out_of_each_others_territory(key: str, release: str) -> None:
    assert not _matches(key, release)


def test_format_group_pairs_formats_that_fill_the_same_part_of_a_release() -> None:
    from app.services.builtin_formats import format_group

    assert format_group("audio-dtshd-ma", None) == "Audio"
    assert format_group("hdr-dv-hdr10-plus", None) == "Dynamic Range"
    # A user format is grouped only when every positive condition agrees.
    audio_only = {"conditions": [{"type": "audio_codec"}, {"type": "release_title", "negate": True}]}
    assert format_group(None, audio_only) == "Audio"
    mixed = {"conditions": [{"type": "audio_codec"}, {"type": "hdr_type"}]}
    assert format_group(None, mixed) is None
    assert format_group(None, {"conditions": [{"type": "release_title"}]}) is None


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        # DDPA is the scene shorthand for DD+ Atmos.
        ("Movie 2024 1080p WEB-DL DDPA5.1 H.265-GRP", {"audio-ddplus-atmos"}),
        ("Movie 2024 1080p WEB-DL DDPA 5.1 H.264-GRP", {"audio-ddplus-atmos"}),
        # HDR10+ spelled out still reads as HDR10+, and pairs with DV.
        ("Movie 2024 2160p WEB-DL HDR10Plus HEVC-GRP", {"hdr-hdr10-plus"}),
        ("Movie 2024 2160p WEB-DL DoVi HDR10Plus HEVC-GRP", {"hdr-dv-hdr10-plus"}),
        ("Movie 2024 2160p WEB-DL DV HDR10.Plus HEVC-GRP", {"hdr-dv-hdr10-plus"}),
    ],
)
def test_alternate_spellings_resolve_to_one_format(release: str, expected: set[str]) -> None:
    from app.services.custom_formats import evaluate_custom_formats

    formats = [
        EvaluationFormat.from_dict({
            "id": item.key, "name": item.name, "description": item.description, "enabled": True,
            "condition_definition": condition_definition(item, supersedes=item.supersedes),
        })
        for item in BUILTIN_FORMATS
        if item.group in {"Audio", "Dynamic Range"}
    ]
    matched = {item.custom_format_id for item in evaluate_custom_formats(formats, parse_release(release)).formats if item.matched}
    assert matched == expected
