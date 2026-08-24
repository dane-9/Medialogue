"""Torznab item parsing, focused on the swarm counts.

Indexers are inconsistent about how they report the swarm: some send a
`leechers` attribute, some send only `peers` (the swarm total), and some send
neither.  The UI shows seeders and leechers side by side, so the parser has to
normalise all three shapes.
"""

from xml.etree import ElementTree

from app.integrations.torznab import TORZNAB_NS, TorznabClient


def _item(**attrs: str) -> ElementTree.Element:
    xml = "".join(f'<t:attr name="{name}" value="{value}"/>' for name, value in attrs.items())
    return ElementTree.fromstring(
        f'<item xmlns:t="{TORZNAB_NS}"><guid>g</guid><title>Release</title>{xml}</item>'
    )


def test_leechers_are_taken_directly_when_reported() -> None:
    result = TorznabClient._parse_item(_item(seeders="20", leechers="3"))
    assert (result.seeders, result.leechers) == (20, 3)


def test_leechers_are_derived_from_the_peer_total() -> None:
    # peers is the whole swarm, so the leechers are whatever is not seeding.
    result = TorznabClient._parse_item(_item(seeders="20", peers="21"))
    assert (result.seeders, result.leechers) == (20, 1)


def test_an_explicit_leecher_count_wins_over_peers() -> None:
    result = TorznabClient._parse_item(_item(seeders="20", leechers="3", peers="99"))
    assert result.leechers == 3


def test_a_peer_total_below_the_seeder_count_clamps_to_zero() -> None:
    # The two attributes are sampled independently and can disagree; a negative
    # leecher count would be nonsense to render.
    result = TorznabClient._parse_item(_item(seeders="20", peers="4"))
    assert result.leechers == 0


def test_missing_swarm_attributes_stay_none() -> None:
    result = TorznabClient._parse_item(_item())
    assert (result.seeders, result.leechers) == (None, None)


def test_peers_without_seeders_cannot_be_split() -> None:
    result = TorznabClient._parse_item(_item(peers="10"))
    assert result.leechers is None


def test_languages_come_from_the_torznab_attribute() -> None:
    result = TorznabClient._parse_item(_item(language="English"))
    assert result.languages == ("English",)


def test_a_comma_separated_language_attribute_is_split() -> None:
    result = TorznabClient._parse_item(_item(language="English, French"))
    assert result.languages == ("English", "French")


def test_iso_codes_are_resolved_to_display_names() -> None:
    assert TorznabClient._parse_item(_item(language="eng")).languages == ("English",)
    assert TorznabClient._parse_item(_item(language="ja")).languages == ("Japanese",)


def test_an_unrecognised_language_is_dropped_rather_than_invented() -> None:
    assert TorznabClient._parse_item(_item(language="Klingon")).languages == ()


def test_a_plain_language_element_is_read_for_indexers_that_ignore_the_spec() -> None:
    item = ElementTree.fromstring(
        f'<item xmlns:t="{TORZNAB_NS}"><guid>g</guid><title>Release</title><language>French</language></item>'
    )
    assert TorznabClient._parse_item(item).languages == ("French",)


def test_no_language_information_yields_no_languages() -> None:
    # The empty tuple lets the aggregation fall through to the title parse.
    assert TorznabClient._parse_item(_item(seeders="5")).languages == ()
