from app.parser import parse_release
from app.services.custom_formats import (
    CustomFormat,
    evaluate_custom_format,
    evaluate_custom_formats,
    validate_custom_format,
)


RELEASE = "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM"


def test_required_and_same_type_or_grouping():
    custom_format = CustomFormat(
        "Preferred remux",
        [
            {"type": "quality_modifier", "pattern": "REMUX", "required": True},
            {"type": "release_group", "pattern": "^FraMeSToR$"},
            {"type": "release_group", "pattern": "^LM$"},
        ],
    )
    result = evaluate_custom_format(custom_format, RELEASE, score=150)
    assert result.matched
    assert result.score_contribution == 150
    assert result.group_results["release_group"]
    assert len(result.conditions) == 3
    assert result.conditions[-1].regex_match == "LM"


def test_different_condition_types_are_and_groups():
    custom_format = CustomFormat(
        "Remux with DV",
        [
            {"type": "quality_modifier", "pattern": "REMUX", "required": True},
            {"type": "hdr_type", "pattern": "Dolby Vision"},
        ],
    )
    assert evaluate_custom_format(custom_format, RELEASE).matched
    failed = evaluate_custom_format(
        custom_format,
        "Inception 2010 2160p UHD BluRay REMUX HDR HEVC DTS-HD MA 5.1-LM",
    )
    assert not failed.matched
    assert any(not item.effective_result for item in failed.conditions)


def test_negate_and_zero_score_are_explainable():
    custom_format = CustomFormat(
        "No HDR10+",
        [{"type": "hdr_type", "pattern": "HDR10+", "required": True, "negate": True}],
    )
    result = evaluate_custom_format(custom_format, RELEASE, score=0)
    assert result.matched
    assert result.score_contribution == 0
    condition = result.conditions[0]
    assert condition.matched is False
    assert condition.effective_result is True
    assert condition.negated


def test_signed_profile_scores_and_title_overrides():
    formats = [
        CustomFormat("Hybrid", [{"type": "release_attribute", "pattern": "Hybrid"}]),
        CustomFormat("DV", [{"type": "hdr_type", "pattern": "DV"}]),
        CustomFormat("Bad group", [{"type": "release_group", "pattern": "^bad$"}]),
    ]
    scores = {formats[0].id: 100, formats[1].id: 50, formats[2].id: -1000}
    result = evaluate_custom_formats(formats, RELEASE, profile_scores=scores)
    assert result.total_score == 150
    override = evaluate_custom_formats(
        formats,
        RELEASE,
        profile_scores=scores,
        score_overrides={formats[0].id: 500},
    )
    assert override.total_score == 550


def test_more_specific_format_supersedes_broad_match_and_scores_once():
    broad = CustomFormat(
        "Dolby Vision",
        [{"type": "release_title", "pattern": r"\bDV\b"}],
        id="broad-dv",
    )
    specific = CustomFormat(
        "DV HDR10",
        [{"type": "release_title", "pattern": r"^(?=.*\bDV\b)(?=.*\bHDR\b)"}],
        id="specific-dv-hdr10",
        supersedes=(broad.id,),
    )
    result = evaluate_custom_formats(
        [broad, specific],
        "Movie.2026.2160p.BluRay.REMUX.DV.HDR-GROUP",
        profile_scores={broad.id: 45, specific.id: 50},
    )
    broad_result, specific_result = result.formats
    assert specific_result.matched is True
    assert specific_result.score_contribution == 50
    assert broad_result.matched is False
    assert broad_result.raw_matched is True
    assert broad_result.suppressed_by == (specific.id,)
    assert broad_result.suppressed_by_names == (specific.name,)
    assert broad_result.score_contribution == 0
    assert result.total_score == 50


def test_unselected_specific_format_does_not_suppress_selected_broad_format():
    broad = CustomFormat(
        "Dolby Vision",
        [{"type": "release_title", "pattern": r"\bDV\b"}],
        id="broad-dv",
    )
    specific = CustomFormat(
        "DV HDR10",
        [{"type": "release_title", "pattern": r"^(?=.*\bDV\b)(?=.*\bHDR\b)"}],
        id="specific-dv-hdr10",
        supersedes=(broad.id,),
    )

    result = evaluate_custom_formats(
        [broad, specific],
        "Movie.2026.2160p.BluRay.REMUX.DV.HDR-GROUP",
        profile_scores={broad.id: 45},
    )

    broad_result, specific_result = result.formats
    assert broad_result.matched is True
    assert broad_result.score_contribution == 45
    assert specific_result.matched is True
    assert specific_result.score_contribution == 0
    assert result.total_score == 45


def test_supersedence_is_transitive_from_the_most_specific_match():
    base = CustomFormat("Base", [{"type": "release_title", "pattern": "TAG"}], id="base")
    middle = CustomFormat("Middle", [{"type": "release_title", "pattern": "TAG"}], id="middle", supersedes=(base.id,))
    specific = CustomFormat("Specific", [{"type": "release_title", "pattern": "TAG"}], id="specific", supersedes=(middle.id,))
    result = evaluate_custom_formats([base, middle, specific], "Movie TAG", profile_scores={base.id: 10, middle.id: 20, specific.id: 30})
    assert result.total_score == 30
    assert [item.matched for item in result.formats] == [False, False, True]
    assert result.formats[0].suppressed_by_names == ("Specific",)
    assert result.formats[1].suppressed_by_names == ("Specific",)


def test_unselected_format_can_match_without_affecting_profile_score():
    custom_format = CustomFormat(
        "Revision",
        [{"type": "release_title", "pattern": r"\bREPACK\b", "score_offset": 25}],
        id="revision",
    )

    unselected = evaluate_custom_formats(
        [custom_format],
        "Show S01E01 REPACK 1080p WEB-DL-GRP",
        profile_scores={},
    )
    assert unselected.formats[0].matched is True
    assert unselected.formats[0].score_offset == 25
    assert unselected.formats[0].score_contribution == 0
    assert unselected.total_score == 0

    # Explicitly selecting a format with a zero base score is different from
    # not selecting it: its per-match offset is then allowed to contribute.
    selected = evaluate_custom_formats(
        [custom_format],
        "Show S01E01 REPACK 1080p WEB-DL-GRP",
        profile_scores={custom_format.id: 0},
    )
    assert selected.formats[0].score_contribution == 25
    assert selected.total_score == 25


def test_highest_matching_rule_offset_is_added_to_the_profile_score():
    custom_format = CustomFormat(
        "Revision",
        [
            {"type": "release_title", "pattern": r"\bREPACK\b", "score_offset": 1},
            {"type": "release_title", "pattern": r"\bREPACK2\b", "score_offset": 2},
        ],
    )
    result = evaluate_custom_format(custom_format, "Show S01E01 REPACK2 1080p WEB-DL-GRP", score=10)
    assert result.matched
    assert result.score_offset == 2
    assert result.score_contribution == 12


def test_named_regex_group_can_supply_a_dynamic_score_offset():
    custom_format = CustomFormat(
        "Repack",
        [{"type": "release_title", "pattern": r"\bREPACK(?P<score_offset>\d*)\b", "score_offset": 1}],
    )
    plain = evaluate_custom_format(custom_format, "Show S01E01 REPACK 1080p WEB-DL-GRP", score=5)
    numbered = evaluate_custom_format(custom_format, "Show S01E01 REPACK27 1080p WEB-DL-GRP", score=5)
    assert (plain.score_offset, plain.score_contribution) == (1, 6)
    assert (numbered.score_offset, numbered.score_contribution) == (27, 32)


def test_indexer_and_release_title_use_context_and_regex():
    custom_format = CustomFormat(
        "Indexer and release",
        [
            {"type": "indexer", "pattern": "Tracker", "required": True},
            {"type": "release_title", "pattern": r"Hybrid\s+2160p"},
        ],
    )
    result = evaluate_custom_format(custom_format, parse_release(RELEASE), context={"indexer": "Tracker"})
    assert result.matched


def test_invalid_regex_is_reported_by_validation_and_evaluation():
    custom_format = CustomFormat("Invalid", [{"type": "release_title", "pattern": "["}])
    assert validate_custom_format(custom_format)
    result = evaluate_custom_format(custom_format, RELEASE)
    assert not result.matched
    assert result.conditions[0].reason and "invalid regex" in result.conditions[0].reason


def test_application_owned_json_roundtrip():
    custom_format = CustomFormat(
        "Hybrid",
        [{"type": "release_attribute", "name": "Hybrid", "pattern": "Hybrid"}],
    )
    restored = CustomFormat.from_dict(custom_format.to_dict())
    assert restored.name == custom_format.name
    assert restored.conditions[0].type == "release_attribute"



def test_every_part11_condition_type_uses_expected_structured_evidence():
    web_release = "Inception 2010 Hybrid IMAX 2160p AMZN WEB-DL DV HDR10+ HEVC TrueHD 7.1 Atmos-LM"
    remux_release = "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM"
    french_release = "The Desert Child 2026 Hybrid FRENCH 2160p iT WEB-DL TrueHD 7.1 Atmos DV HDR10+ H.265-126811"

    cases = [
        ("release_title", {"pattern": r"Inception 2010 Hybrid"}, web_release, {}),
        ("release_group", {"pattern": r"^LM$"}, web_release, {}),
        ("quality", {"pattern": "2160p WEB-DL"}, web_release, {}),
        ("quality_modifier", {"pattern": "REMUX"}, remux_release, {}),
        ("resolution", {"pattern": "2160p"}, web_release, {}),
        ("source", {"pattern": "WEB-DL"}, web_release, {}),
        ("edition", {"pattern": "IMAX"}, web_release, {}),
        ("language", {"pattern": "FRENCH"}, french_release, {}),
        ("indexer", {"pattern": "PTP"}, web_release, {"indexer": "PTP"}),
        ("web_provider", {"pattern": "AMZN"}, web_release, {}),
        ("video_codec", {"pattern": "HEVC"}, web_release, {}),
        ("audio_codec", {"pattern": "TrueHD"}, web_release, {}),
        ("audio_channels", {"pattern": "7.1"}, web_release, {}),
        ("hdr_type", {"pattern": "Dolby Vision"}, web_release, {}),
        ("release_attribute", {"pattern": "Hybrid"}, web_release, {}),
    ]

    for condition_type, data, release, context in cases:
        custom_format = CustomFormat(
            f"Test {condition_type}",
            [{"type": condition_type, "required": True, **data}],
        )
        result = evaluate_custom_format(custom_format, release, context=context)
        assert result.matched, (condition_type, result.conditions[0].reason, result.conditions[0].evidence)
