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
            {"type": "quality_modifier", "value": "REMUX", "required": True},
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
            {"type": "quality_modifier", "value": "REMUX", "required": True},
            {"type": "hdr_type", "value": "Dolby Vision"},
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
        [{"type": "hdr_type", "value": "HDR10+", "required": True, "negate": True}],
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
        CustomFormat("Hybrid", [{"type": "release_attribute", "value": "Hybrid"}]),
        CustomFormat("DV", [{"type": "hdr_type", "value": "DV"}]),
        CustomFormat("Bad group", [{"type": "release_group", "pattern": "^bad$"}]),
    ]
    scores = {"Hybrid": 100, "DV": 50, "Bad group": -1000}
    result = evaluate_custom_formats(formats, RELEASE, profile_scores=scores)
    assert result.total_score == 150
    override = evaluate_custom_formats(
        formats,
        RELEASE,
        profile_scores=scores,
        score_overrides={formats[0].id: 500},
    )
    assert override.total_score == 550


def test_indexer_and_release_title_use_context_and_regex():
    custom_format = CustomFormat(
        "Indexer and release",
        [
            {"type": "indexer", "value": "Tracker", "required": True},
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
        [{"type": "release_attribute", "name": "Hybrid", "value": "Hybrid"}],
    )
    restored = CustomFormat.from_dict(custom_format.to_dict())
    assert restored.name == custom_format.name
    assert restored.conditions[0].condition_type == "release_attribute"



def test_every_part11_condition_type_uses_expected_structured_evidence():
    web_release = "Inception 2010 Hybrid IMAX 2160p AMZN WEB-DL DV HDR10+ HEVC TrueHD 7.1 Atmos-LM"
    remux_release = "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM"
    french_release = "The Desert Child 2026 Hybrid FRENCH 2160p iT WEB-DL TrueHD 7.1 Atmos DV HDR10+ H.265-126811"

    cases = [
        ("release_title", {"pattern": r"Inception 2010 Hybrid"}, web_release, {}),
        ("release_group", {"pattern": r"^LM$"}, web_release, {}),
        ("quality", {"value": "2160p WEB-DL"}, web_release, {}),
        ("quality_modifier", {"value": "REMUX"}, remux_release, {}),
        ("resolution", {"value": "2160p"}, web_release, {}),
        ("source", {"value": "WEB-DL"}, web_release, {}),
        ("edition", {"value": "IMAX"}, web_release, {}),
        ("language", {"value": "FRENCH"}, french_release, {}),
        ("indexer", {"value": "PTP"}, web_release, {"indexer": "PTP"}),
        ("web_provider", {"value": "AMZN"}, web_release, {}),
        ("video_codec", {"value": "HEVC"}, web_release, {}),
        ("audio_codec", {"value": "TrueHD"}, web_release, {}),
        ("audio_channels", {"value": "7.1"}, web_release, {}),
        ("hdr_type", {"value": "Dolby Vision"}, web_release, {}),
        ("release_attribute", {"value": "Hybrid"}, web_release, {}),
    ]

    for condition_type, data, release, context in cases:
        custom_format = CustomFormat(
            f"Test {condition_type}",
            [{"type": condition_type, "required": True, **data}],
        )
        result = evaluate_custom_format(custom_format, release, context=context)
        assert result.matched, (condition_type, result.conditions[0].reason, result.conditions[0].evidence)
