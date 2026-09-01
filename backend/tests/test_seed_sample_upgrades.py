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
