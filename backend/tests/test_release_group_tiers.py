"""TRaSH's Remux and WEB release group tiers, as built-in Custom Formats."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.db import session as db_session
from app.db.bootstrap import ensure_builtin_custom_formats, ensure_default_quality_profile, refresh_default_profile_scores
from app.models.domain import CustomFormat, ProfileCustomFormatScore, QualityProfile
from app.parser import parse_release
from app.services import upgrade_candidates as module
from app.services.builtin_formats import (
    BANNED_RELEASE_GROUP,
    BUILTIN_FORMATS,
    DEFAULT_FORMAT_SCORES,
    RELEASE_GROUP_FORMAT,
    RELEASE_GROUP_FORMAT_KEYS,
    condition_definition,
)
from app.services.custom_formats import CustomFormat as EvaluationFormat, evaluate_custom_formats
from tests.test_upgrade_candidates import client  # noqa: F401


def _matching(title: str) -> set[str]:
    formats = [
        EvaluationFormat.from_dict({"id": str(uuid.uuid4()), "name": item.key, "description": "", "enabled": True,
                                    "condition_definition": condition_definition(item)})
        for item in BUILTIN_FORMATS if item.group == RELEASE_GROUP_FORMAT
    ]
    result = evaluate_custom_formats(formats, parse_release(title), profile_scores={}, score_overrides={}, context={})
    return {item.custom_format_name for item in result.formats if item.matched}


@pytest.mark.parametrize(("title", "expected"), [
    ("Dune.2021.2160p.UHD.BluRay.REMUX.DV.HEVC.TrueHD.7.1-FraMeSToR", {"group-remux-tier-01"}),
    ("Dune 2021 1080p BluRay Remux AVC DTS-HD MA 5.1 - KRaLiMaRKo", {"group-remux-tier-03"}),
    ("Dune.2021.2160p.AMZN.WEB-DL.DDP5.1.H.265-FLUX", {"group-web-tier-01"}),
    ("Dune 2021 1080p WEB-DL DD+5.1 H.264-monkee.mkv", {"group-web-tier-02"}),
    # NTb sits in both lists, at the tier for what it released.
    ("Dune.2021.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-NTb", {"group-remux-tier-03"}),
    ("Dune.2021.1080p.WEB-DL.DD5.1.H.264-NTb", {"group-web-tier-01"}),
    # The right group on the wrong kind of release, or only in the title.
    ("Dune.2021.1080p.BluRay.x264-FraMeSToR", set()),
    ("Dune.2021.2160p.WEB-DL.DV.H.265-FraMeSToR", set()),
    ("FLUX.Capacitor.2021.1080p.WEB-DL.H.264-UNKNOWN", set()),
])
def test_a_release_matches_the_tier_of_its_group_and_kind(title, expected) -> None:
    assert _matching(title) == expected


@pytest.mark.parametrize("title", [
    "Back to the Future Part III (1990) [2160p REMUX] [HEVC DV HDR10+ Dolby TrueHD Atmos 7 1 24-bit Audio English] [Data Lass]",
    "A Beautiful Mind 2001 DUAL iNTERNAL UHD BluRay 2160p TrueHD Atmos 7 1 DV HDR10 REMUX-seedpool",
    "2 Fast 2 Furious 2003 BluRay 2160p Remux DV HDR10+ HEVC DTS-X 7.1-DepraveD",
    "Some.Movie.2020.2160p.WEB-DL-DEPRAVED.mkv",
])
def test_banned_groups_are_recognised(title) -> None:
    assert "group-banned" in _matching(title)
    assert BANNED_RELEASE_GROUP.search(title)


@pytest.mark.parametrize("title", ["Seedpool.Stories.2020.2160p.WEB-DL.H.265-FLUX", "Data.Lass.2020.1080p.WEB-DL.H.264-CMRG"])
def test_a_banned_name_elsewhere_in_the_title_is_fine(title) -> None:
    assert "group-banned" not in _matching(title)


@pytest.mark.parametrize(("title", "penalised"), [
    ("Dune.2021.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1", True),
    ("Dune.2021.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-FGT", False),
    ("Dune 2021 1080p BluRay Remux AVC DTS-HD MA 5.1 - KRaLiMaRKo", False),
    ("Dune (2021) [1080p REMUX] [Some Group]", False),
])
def test_only_a_release_with_no_group_credited_is_penalised(title, penalised) -> None:
    assert ("attr-no-group" in _matching(title)) is penalised


def _audio_score(title: str) -> set[str]:
    formats = [
        EvaluationFormat.from_dict({"id": item.key, "name": item.key, "description": "", "enabled": True,
                                    "condition_definition": condition_definition(item, supersedes=item.supersedes)})
        for item in BUILTIN_FORMATS if item.group == "Audio"
    ]
    result = evaluate_custom_formats(formats, parse_release(title), profile_scores={item.id: DEFAULT_FORMAT_SCORES[item.id] for item in formats}, score_overrides={}, context={})
    return {item.custom_format_name for item in result.formats if item.matched}


def test_only_the_best_audio_track_counts() -> None:
    assert _audio_score("Anger Management 2003 1080p BluRay REMUX ENG LATINO POR TrueHD 5 1 DDP5 1 H264 MKV-GRP") == {"audio-truehd"}
    assert _audio_score("Movie.2020.1080p.WEB-DL.DDP5.1.H.264-GRP") == {"audio-ddplus"}


def test_hybrid_and_no_group_scores() -> None:
    assert DEFAULT_FORMAT_SCORES["attr-hybrid"] == -5
    assert DEFAULT_FORMAT_SCORES["attr-no-group"] == -5


def test_a_top_tier_outweighs_an_hdr_tag_but_not_dolby_vision() -> None:
    assert [DEFAULT_FORMAT_SCORES[key] for key in RELEASE_GROUP_FORMAT_KEYS] == [40, 25, 10, 40, 25, 10]
    assert DEFAULT_FORMAT_SCORES["group-remux-tier-01"] > DEFAULT_FORMAT_SCORES["hdr-hdr10"]
    assert DEFAULT_FORMAT_SCORES["group-remux-tier-01"] < DEFAULT_FORMAT_SCORES["hdr-dolby-vision"]


def _point_format(monkeypatch, *ids: str) -> None:
    monkeypatch.setitem(module._FORMAT_GROUPS, ids[0], RELEASE_GROUP_FORMAT)
    for other in ids[1:]:
        monkeypatch.setitem(module._FORMAT_GROUPS, other, "Audio")


def test_a_better_group_alone_is_not_an_upgrade(monkeypatch) -> None:
    _point_format(monkeypatch, "tier", "audio")
    current = {"breakdown": [{"custom_format_id": "audio", "matched": True, "contribution": 50}]}
    candidate = [
        {"custom_format_id": "audio", "matched": True, "contribution": 50},
        {"custom_format_id": "tier", "matched": True, "contribution": 30},
    ]
    assert module._format_gain(current, candidate) == (0, [])


def test_group_points_are_counted_only_from_tier_formats(monkeypatch) -> None:
    _point_format(monkeypatch, "tier", "audio")
    rows = [
        {"custom_format_id": "audio", "matched": True, "contribution": 50},
        {"custom_format_id": "tier", "matched": True, "contribution": 3},
        {"custom_format_id": "tier", "matched": False, "contribution": 9},
    ]
    assert module._release_group_points(rows) == 3


def test_an_existing_default_profile_gains_the_tiers_once_and_keeps_your_edits(client) -> None:  # noqa: F811
    async def scenario():
        async with db_session.async_session_factory() as db:
            await ensure_builtin_custom_formats(db)
            await ensure_default_quality_profile(db)
            profile = await db.scalar(select(QualityProfile).where(QualityProfile.name == "Default"))
            formats = {row.builtin_key: row.id for row in (await db.scalars(select(CustomFormat))).all()}
            for key in RELEASE_GROUP_FORMAT_KEYS:
                row = await db.scalar(select(ProfileCustomFormatScore).where(
                    ProfileCustomFormatScore.profile_id == profile.id, ProfileCustomFormatScore.custom_format_id == formats[key]))
                await db.delete(row)
            profile.settings = {**profile.settings, "default_scores_version": 2}
            await db.commit()

            assert await refresh_default_profile_scores(db) == len(RELEASE_GROUP_FORMAT_KEYS)
            await db.commit()
            row = await db.scalar(select(ProfileCustomFormatScore).where(
                ProfileCustomFormatScore.profile_id == profile.id, ProfileCustomFormatScore.custom_format_id == formats["group-remux-tier-01"]))
            assert row.score == 40
            row.score = 7
            await db.commit()

            assert await refresh_default_profile_scores(db) == 0
            await db.refresh(row)
            assert row.score == 7

    asyncio.run(scenario())
