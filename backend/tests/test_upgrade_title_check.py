"""An upgrade result must really be the title that was searched for."""

import uuid
from xml.etree import ElementTree

import pytest

from app.core.identity import release_title_matches
from app.integrations.torznab import TORZNAB_NS, SearchResult, TorznabClient
from app.models.domain import MediaType
from app.parser import parse_release
from app.services.search import SearchTarget
from app.services.upgrade_candidates import _is_the_searched_title

WRONG_MOVIE = "50 First Dates 2004 Hybrid 2160p UHD Blu-ray Remux DV HDR10P HEVC TrueHD Atmos 7 1-CiNEPHiLES"


def _target(title: str, year: int | None, media_type: MediaType = MediaType.MOVIES, tmdb_id: int | None = 334) -> SearchTarget:
    return SearchTarget(media_type=media_type, entity_type="movie", entity_id=uuid.uuid4(), profile_entity_id=uuid.uuid4(),
                        title=title, year=year, tmdb_id=tmdb_id, original_language=None)


def _is_match(release: str, target: SearchTarget, tmdb_id: int | None = None) -> bool:
    result = SearchResult(guid="g", title=release, download_url="magnet:?", size=1, seeders=1, published=None, tmdb_id=tmdb_id)
    return _is_the_searched_title(parse_release(release), result, target)


def test_a_word_inside_the_source_tag_is_not_the_title() -> None:
    assert not _is_match(WRONG_MOVIE, _target("Ray", 2004))


@pytest.mark.parametrize("release", [
    "Ray.2004.UHD.BluRay.2160p.REMUX.HEVC.DV.HDR.DTS-HD.MA.5.1-dK",
    "Ray 2004 2160p UHD Blu-ray Remux HEVC TrueHD Atmos 7 1-GRP",
])
def test_the_same_title_and_year_is_accepted(release) -> None:
    assert _is_match(release, _target("Ray", 2004))


@pytest.mark.parametrize(("release", "title", "year"), [
    ("A.Nightmare.on.Elm.Street.2.Freddys.Revenge.1985.BluRay.1080p.DTS-HD.MA.5.1.AVC.REMUX-FraMeSToR", "A Nightmare on Elm Street Part 2: Freddy's Revenge", 1985),
    ("Rocky.2.1979.1080p.BluRay.REMUX.AVC-GRP", "Rocky II", 1979),
    ("Rocky.II.1979.1080p.BluRay.REMUX.AVC-GRP", "Rocky 2", 1979),
    ("Dune.Part.2.2024.2160p.WEB-DL.H.265-GRP", "Dune: Part Two", None),
])
def test_how_a_release_spells_a_sequel_number_does_not_matter(release, title, year) -> None:
    if title == "Dune: Part Two":
        # A spelled-out number is a different word, and is left to the id.
        assert not _is_match(release, _target(title, 2024))
        assert _is_match(release, _target(title, 2024), tmdb_id=334)
        return
    assert _is_match(release, _target(title, year))


@pytest.mark.parametrize(("left", "right"), [
    ("A Nightmare on Elm Street Part 2", "A Nightmare on Elm Street Part 3"),
    ("Rocky II", "Rocky III"),
    ("Back to the Future Part II", "Back to the Future"),
])
def test_a_different_number_is_a_different_title(left, right) -> None:
    assert not release_title_matches(left, right)


def test_a_word_called_part_stays_when_no_number_follows() -> None:
    assert not release_title_matches("Part Time", "Time")


def test_the_same_title_from_another_year_is_rejected() -> None:
    assert not _is_match("Ray.1998.1080p.BluRay.REMUX.AVC-GRP", _target("Ray", 2004))


def test_punctuation_does_not_make_a_different_title() -> None:
    assert _is_match("Mission.Impossible.Fallout.2018.2160p.WEB-DL.H.265-GRP", _target("Mission: Impossible - Fallout", 2018))


def test_the_indexers_id_vouches_for_a_differently_named_release() -> None:
    assert _is_match("La.Vita.E.Bella.1997.1080p.BluRay.REMUX.AVC-GRP", _target("Life Is Beautiful", 1997), tmdb_id=334)


def test_a_matching_id_with_the_wrong_year_is_still_rejected() -> None:
    assert not _is_match("Ray.1998.1080p.BluRay.REMUX.AVC-GRP", _target("Ray", 2004), tmdb_id=334)


def test_another_movies_id_does_not_vouch_for_it() -> None:
    assert not _is_match(WRONG_MOVIE, _target("Ray", 2004), tmdb_id=9506)


def test_a_show_needs_its_title_but_not_a_year() -> None:
    target = _target("Severance", 2022, MediaType.SHOWS)
    assert _is_match("Severance.S01.2160p.WEB-DL.H.265-GRP", target)
    assert not _is_match("Other.Show.S01.2160p.WEB-DL.H.265-GRP", target)


@pytest.mark.parametrize(("value", "expected"), [("9506", 9506), ("0", None), (None, None)])
def test_the_id_is_read_from_the_torznab_item(value, expected) -> None:
    item = ElementTree.Element("item")
    ElementTree.SubElement(item, "title").text = "Movie.2020.1080p.WEB-DL-GRP"
    if value is not None:
        ElementTree.SubElement(item, f"{{{TORZNAB_NS}}}attr", {"name": "tmdbid", "value": value})
    assert TorznabClient._parse_item(item).tmdb_id == expected
