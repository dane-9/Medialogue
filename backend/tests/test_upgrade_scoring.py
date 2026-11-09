"""When better Custom Formats make an upgrade, and the default scores behind it."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.services import upgrade_candidates as upgrades
from app.services.builtin_formats import DEFAULT_FORMAT_SCORES, DEFAULT_FORMAT_SCORES_V1

GROUPS = {"truehd": "Audio", "ddp": "Audio", "dd": "Audio", "dv": "Dynamic Range", "hdr10": "Dynamic Range",
          "hybrid": None, "hevc": "Video", "avc": "Video"}


@pytest.fixture(autouse=True)
def _groups(monkeypatch):
    monkeypatch.setattr(upgrades, "_FORMAT_GROUPS", dict(GROUPS))


def current(*formats):
    return {"breakdown": [{"custom_format_id": key, "matched": True, "contribution": score} for key, score in formats]}


def candidate(*formats):
    return [{"custom_format_id": key, "matched": True, "contribution": score} for key, score in formats]


def test_dv_over_hdr10_is_a_gain():
    assert upgrades._format_gain(current(("hdr10", 30), ("ddp", 15)), candidate(("dv", 50), ("ddp", 15))) == (20, [])


def test_a_gain_that_costs_audio_is_flagged():
    gain, worse = upgrades._format_gain(current(("hdr10", 30), ("truehd", 60)), candidate(("dv", 55), ("ddp", 15)))
    assert gain == -20 and worse == ["Audio"]
    gain, worse = upgrades._format_gain(current(("hdr10", 30), ("ddp", 30)), candidate(("dv", 50), ("ddp", 15)))
    assert gain == 5 and worse == ["Audio"]


def test_codec_swaps_and_small_extras_stay_below_the_minimum():
    assert upgrades._format_gain(current(("avc", 0), ("dd", 0)), candidate(("hevc", 0), ("dd", 0)))[0] == 0
    assert upgrades._format_gain(current(("ddp", 15)), candidate(("ddp", 15), ("hybrid", 10)))[0] < upgrades.MINIMUM_FORMAT_GAIN


def test_untagged_audio_on_the_current_release_is_unknown_not_worst():
    # A bare folder name says nothing about audio: no credit for the candidate's.
    assert upgrades._format_gain(current(), candidate(("truehd", 60))) == (0, [])
    # Dynamic range untagged means SDR, so DV still counts.
    assert upgrades._format_gain(current(), candidate(("dv", 50))) == (50, [])


def test_default_scores_rank_dv_first_and_avoid_hlg():
    assert DEFAULT_FORMAT_SCORES["hdr-dolby-vision"] > DEFAULT_FORMAT_SCORES["hdr-hdr10-plus"] > DEFAULT_FORMAT_SCORES["hdr-hdr10"]
    assert DEFAULT_FORMAT_SCORES["hdr-sdr"] == 0 and DEFAULT_FORMAT_SCORES["hdr-hlg"] < 0
    assert {DEFAULT_FORMAT_SCORES[key] for key in ("video-avc", "video-hevc", "video-av1")} == {0}
    # Lossless formats differ by less than the minimum gain: never an upgrade alone.
    lossless = [DEFAULT_FORMAT_SCORES[key] for key in ("audio-truehd", "audio-dtsx", "audio-dtshd-ma", "audio-flac", "audio-pcm")]
    assert max(lossless) - min(lossless) < upgrades.MINIMUM_FORMAT_GAIN


@pytest.mark.asyncio
async def test_default_profile_moves_only_untouched_scores():
    from app.db.bootstrap import ensure_builtin_custom_formats, ensure_default_quality_profile, refresh_default_profile_scores
    from app.models.domain import CustomFormat, ProfileCustomFormatScore, QualityProfile

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        await ensure_builtin_custom_formats(db)
        await ensure_default_quality_profile(db)
        profile = await db.scalar(select(QualityProfile))
        # An install from before: version 1 scores, one of them changed by hand.
        profile.settings = {key: value for key, value in profile.settings.items() if key != "default_scores_version"}
        keys = {row.id: row.builtin_key for row in (await db.scalars(select(CustomFormat))).all()}
        for row in (await db.scalars(select(ProfileCustomFormatScore))).all():
            # Formats added since have no version 1 score to go back to.
            row.score = DEFAULT_FORMAT_SCORES_V1.get(keys[row.custom_format_id], row.score)
            if keys[row.custom_format_id] == "hdr-sdr":
                row.score = 7
        await db.flush()

        assert await refresh_default_profile_scores(db) > 0
        scores = {keys[row.custom_format_id]: row.score for row in (await db.scalars(select(ProfileCustomFormatScore))).all()}
        assert scores["video-hevc"] == 0 and scores["hdr-hlg"] == -50
        assert scores["hdr-sdr"] == 7, "a score set by hand stays"
        # Once per defaults version.
        assert await refresh_default_profile_scores(db) == 0
    await engine.dispose()
