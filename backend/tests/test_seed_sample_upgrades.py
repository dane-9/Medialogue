from scripts.seed_sample_upgrades import _comparison, _matched_formats


def _by_name(release_name: str) -> dict[str, dict]:
    return {item["custom_format_name"]: item for item in _matched_formats(release_name)}


def test_seeded_dv_hdr_sample_models_builtin_takeover() -> None:
    formats = _by_name("Movie.2026.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC-GROUP")

    assert formats["DV HDR10"]["matched"] is True
    assert formats["DV HDR10"]["contribution"] == 50
    assert formats["Dolby Vision"]["raw_matched"] is True
    assert formats["Dolby Vision"]["matched"] is False
    assert formats["HDR10"]["raw_matched"] is True
    assert formats["HDR10"]["matched"] is False
    assert formats["HDR10"]["suppressed_by_names"] == ["DV HDR10"]


def test_seeded_dv_hdr10_plus_and_truehd_atmos_each_score_once() -> None:
    formats = _by_name("Movie.2026.DV.HDR10+.TrueHD.7.1.Atmos-GROUP")

    assert formats["DV HDR10+"]["matched"] is True
    assert formats["Dolby Vision"]["matched"] is False
    assert formats["HDR10+"]["matched"] is False
    assert formats["TrueHD Atmos"]["matched"] is True
    assert formats["TrueHD"]["matched"] is False


def test_seeded_upgrade_improvements_exclude_suppressed_formats() -> None:
    snapshot, added, _current_score, candidate_score = _comparison(
        "Movie.2026.1080p.WEB-DL.H.264-GROUP",
        "Movie.2026.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC-GROUP",
    )

    names = {item["custom_format_name"] for item in added}
    assert "DV HDR10" in names
    assert "Dolby Vision" not in names
    assert "HDR10" not in names
    assert candidate_score == sum(
        item["contribution"]
        for item in snapshot["candidate"]["formats"]
        if item["matched"]
    )


def test_seeded_movie_comparison_carries_matching_editions() -> None:
    snapshot, _added, _current_score, _candidate_score = _comparison(
        "Blade.Runner.1982.The.Final.Cut.1080p.BluRay.x264-GROUP",
        "Blade.Runner.1982.The.Final.Cut.2160p.UHD.BluRay.REMUX-GROUP",
        current_quality="1080p Encode",
        current_edition="Final Cut",
        candidate_edition="Final Cut",
    )

    assert snapshot["current"]["edition"] == "Final Cut"
    assert snapshot["current"]["releases"][0]["edition"] == "Final Cut"
    assert snapshot["candidate"]["edition"] == "Final Cut"


def test_seeded_audio_formats_match_the_same_punctuation_as_the_real_parser() -> None:
    master = _by_name("Movie.2026.1080p.BluRay.REMUX.DTS-HD.MA.5.1-GROUP")
    high_resolution = _by_name("Movie 2026 1080p BluRay DTS HD HRA 5.1 GROUP")

    assert master["DTS-HD MA"]["contribution"] == 45
    assert "DTS-HD HRA" not in master
    assert high_resolution["DTS-HD HRA"]["contribution"] == 20
    assert "DTS-HD MA" not in high_resolution


def test_seeded_hyphenated_source_format_is_not_lost() -> None:
    formats = _by_name("Movie.2026.2160p.WEB-DL.DDP5.1.H.265-GROUP")

    assert formats["WEB-DL"]["contribution"] == 10
