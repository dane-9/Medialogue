"""Language detection in release names.

Follows Radarr's LanguageParser: names matched anywhere in the title,
abbreviations matched as whole tokens, a few codes that only count in upper
case, and an ``Unknown`` fallback so a result always states something.
"""

import pytest

from app.parser.release_parser import parse_release


def languages(title: str) -> tuple[str, ...]:
    return parse_release(title).languages


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Movie.2020.GERMAN.1080p.BluRay.x264-GRP", "German"),
        ("Movie.2020.FRENCH.1080p.WEB-DL-GRP", "French"),
        ("Film.2019.KOR.1080p.BluRay-X", "Korean"),
        ("Movie.2021.JAP.1080p.BluRay.x264-Z", "Japanese"),
        ("Movie.2018.ITA.1080p.WEB-DL-Q", "Italian"),
        ("Movie.2018.VIE.1080p.WEB-DL-Q", "Vietnamese"),
        ("Movie.2018.Hungarian.1080p-Q", "Hungarian"),
        ("Movie.2018.Tagalog.1080p-Q", "Tagalog"),
    ],
)
def test_a_declared_language_is_detected(title: str, expected: str) -> None:
    assert expected in languages(title)


def test_a_release_that_declares_nothing_is_unknown_rather_than_english() -> None:
    assert languages("Inception 2010 1080p BluRay REMUX AVC DTS-HD MA 5.1-GROUP") == ("Unknown",)


@pytest.mark.parametrize(
    ("title", "code"),
    [
        ("Movie.2018.ES.1080p.WEB-DL-Q", "Spanish"),
        ("Movie.2018.DE.1080p.WEB-DL-Q", "German"),
        ("Movie.2018.CZ.1080p.WEB-DL-Q", "Czech"),
        ("Movie.2018.PL.1080p.WEB-DL-Q", "Polish"),
    ],
)
def test_upper_case_codes_are_detected(title: str, code: str) -> None:
    assert code in languages(title)


def test_an_audio_format_is_not_mistaken_for_a_language() -> None:
    assert languages("Show.S01E01.DTS-ES.1080p-Y") == ("Unknown",)


def test_german_dl_carries_the_original_audio_too() -> None:
    assert languages("Movie.2020.GERMAN.DL.1080p.BluRay.x264-GRP") == ("German", "Original")


def test_german_ml_adds_english_as_well() -> None:
    assert languages("Movie.2020.GERMAN.ML.1080p.BluRay.x264-GRP") == ("German", "Original", "English")


def test_web_dl_alone_does_not_imply_a_dual_language_german_release() -> None:
    assert languages("Movie.2020.GERMAN.1080p.WEB-DL.x264-GRP") == ("German",)


def test_multiple_languages_are_all_reported_without_duplicates() -> None:
    result = languages("Movie.2020.German.English.German.1080p-GRP")
    assert result.count("German") == 1
    assert set(result) >= {"German", "English"}
