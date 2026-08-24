"""Release language resolution, following Radarr's AggregateLanguages.

The release title is the weakest signal in the chain, and the order of
precedence is the whole point of these tests.
"""

from app.parser.language_aggregation import aggregate_languages, has_multiple_languages
from app.parser.release_parser import parse_release_name


def resolve(title: str, **kwargs) -> tuple[str, ...]:
    return aggregate_languages(
        release_title=title,
        parsed_languages=parse_release_name(title).languages,
        **kwargs,
    )


def test_the_indexer_wins_over_the_title() -> None:
    # The tracker knows what it is serving; a GERMAN tag in the name does not
    # override an indexer that says French.
    assert resolve(
        "Movie.2020.GERMAN.1080p.BluRay-GRP",
        indexer_languages=("French",),
        original_language="en",
    ) == ("French",)


def test_an_untagged_release_falls_back_to_the_original_language() -> None:
    # The case that started this: no language token anywhere in the name.
    assert resolve(
        "12 Angry Men 1957 UHD BluRay 2160p FLAC 2 0 DV HEVC REMUX-FraMeSToR",
        media_title="12 Angry Men",
        original_language="en",
    ) == ("English",)


def test_a_foreign_film_falls_back_to_its_own_original_language() -> None:
    assert resolve(
        "Oldboy 2003 1080p BluRay x264-GRP",
        media_title="Oldboy",
        original_language="ko",
    ) == ("Korean",)


def test_a_language_word_in_the_media_title_is_not_a_release_language() -> None:
    # "The French Connection" is English; the word is part of the name.
    assert resolve(
        "The French Connection 1971 1080p BluRay x264-GRP",
        media_title="The French Connection",
        original_language="en",
    ) == ("English",)


def test_a_language_tag_outside_the_media_title_still_counts() -> None:
    # Same word, but this release really is French.
    assert "French" in resolve(
        "The French Connection 1971 FRENCH 1080p BluRay x264-GRP",
        media_title="The French Connection",
        original_language="en",
    )


def test_german_dl_resolves_original_to_the_works_own_language() -> None:
    assert resolve(
        "Movie.2020.GERMAN.DL.1080p.BluRay.x264-GRP",
        media_title="Movie",
        original_language="en",
    ) == ("German", "English")


def test_original_becomes_unknown_when_no_original_language_is_known() -> None:
    # Better to admit ignorance than to invent a language.
    assert resolve("Movie.2020.GERMAN.DL.1080p.BluRay.x264-GRP", media_title="Movie") == ("German", "Unknown")


def test_the_multi_setting_applies_to_a_multi_tagged_release() -> None:
    assert resolve(
        "Movie.2020.MULTi.1080p.BluRay.x264-GRP",
        indexer_multi_languages=("English", "French"),
        media_title="Movie",
        original_language="en",
    ) == ("English", "French")


def test_the_multi_setting_is_added_to_a_language_the_title_declared() -> None:
    result = resolve(
        "Movie.2020.MULTi.GERMAN.1080p.BluRay-GRP",
        indexer_multi_languages=("English",),
        media_title="Movie",
        original_language="en",
    )
    assert result == ("German", "English")


def test_the_multi_setting_is_ignored_without_a_multi_tag() -> None:
    assert resolve(
        "Movie.2020.1080p.BluRay.x264-GRP",
        indexer_multi_languages=("English", "French"),
        media_title="Movie",
        original_language="de",
    ) == ("German",)


def test_nothing_known_at_all_stays_unknown() -> None:
    assert resolve("Movie.2020.1080p.BluRay.x264-GRP", media_title="Movie") == ("Unknown",)


def test_an_unrecognised_original_language_code_does_not_become_a_language() -> None:
    assert resolve("Movie.2020.1080p.BluRay-GRP", media_title="Movie", original_language="zz") == ("Unknown",)


def test_multi_tags_are_recognised_in_their_common_spellings() -> None:
    assert has_multiple_languages("Movie.2020.MULTi.1080p-GRP")
    assert has_multiple_languages("Movie.2020.DUAL.AUDIO.1080p-GRP")
    assert has_multiple_languages("Movie.2020.MULTiSUB.1080p-GRP")
    assert not has_multiple_languages("Movie.2020.1080p.BluRay-GRP")
    # "Multiverse" is not a multi tag.
    assert not has_multiple_languages("Spider-Man.Multiverse.2023.1080p-GRP")


def test_german_dl_of_a_german_film_reports_an_unidentified_second_track() -> None:
    # Radarr's behaviour: the original language is already listed, so the DL
    # tag's second audio track stays honestly unidentified.
    assert resolve(
        "Movie.2020.GERMAN.DL.1080p.BluRay.x264-GRP",
        media_title="Movie",
        original_language="de",
    ) == ("German", "Unknown")
