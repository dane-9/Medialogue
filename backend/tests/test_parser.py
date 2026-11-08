import pytest

from app.parser import parse_release


MOVIE_CORPUS = [
    (
        "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM",
        "Inception",
        2010,
        "2160p BluRay REMUX",
        None,
        "LM",
    ),
    (
        "Repo Men 2010 Theatrical Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-FraMeSToR",
        "Repo Men",
        2010,
        "2160p BluRay REMUX",
        "Theatrical Cut",
        "FraMeSToR",
    ),
    (
        "The Survivor 1981 Extended 2160p UHD BluRay REMUX DV HDR HEVC FLAC 1.0-PiG30N",
        "The Survivor",
        1981,
        "2160p BluRay REMUX",
        "Extended Cut",
        "PiG30N",
    ),
    (
        "Disclosure Day 2026 REPACK 1080p BluRay REMUX AVC TrueHD 7.1 Atmos-CiNEPHiLES",
        "Disclosure Day",
        2026,
        "1080p BluRay REMUX",
        None,
        "CiNEPHiLES",
    ),
    (
        "The Grey 2012 Open Matte 1080p BluRay REMUX AVC DTS-HD MA 5.1-TiTTE",
        "The Grey",
        2012,
        "1080p BluRay REMUX",
        "Open Matte",
        "TiTTE",
    ),
    (
        "Rose of Nevada 2026 2160p iT WEB-DL DD+ 5.1 H.265-SCOPE",
        "Rose of Nevada",
        2026,
        "2160p WEB-DL",
        None,
        "SCOPE",
    ),
    (
        "Toy Story 5 2026 2160p MA WEB-DL DD+ 5.1 Atmos DV H.265-BYNDR",
        "Toy Story 5",
        2026,
        "2160p WEB-DL",
        None,
        "BYNDR",
    ),
    (
        "The Desert Child 2026 Hybrid FRENCH 2160p iT WEB-DL TrueHD 7.1 Atmos DV HDR10+ H.265-126811",
        "The Desert Child",
        2026,
        "2160p WEB-DL",
        None,
        "126811",
    ),
    (
        "The Land of Happiness AKA Onnen maa 1993 576i FIN PAL DVD5 MPEG-2 DD 3.1-SPLiFF",
        "The Land of Happiness",
        1993,
        "Full Disc DVD5",
        None,
        "SPLiFF",
    ),
    (
        "Coraline 2009 480p NTSC BRA DVD9 MPEG-2 DD 5.1-Potatin",
        "Coraline",
        2009,
        "Full Disc DVD9",
        None,
        "Potatin",
    ),
    (
        "The Godfather Part II 1974 576i GBR PAL DVD5 DVD9 MPEG-2 DD 5.1-12GaugeShotgun",
        "The Godfather Part II",
        1974,
        "Full Disc DVD5/DVD9",
        None,
        "12GaugeShotgun",
    ),
]


def test_real_world_movie_corpus():
    for raw, title, year, quality, edition, group in MOVIE_CORPUS:
        result = parse_release(raw)
        assert result.identity.title_candidate == title
        assert result.identity.year == year
        assert result.quality.canonical == quality
        assert result.edition == edition
        assert result.release_group == group

    alias = parse_release(
        "The Land of Happiness AKA Onnen maa 1993 576i FIN PAL DVD5 MPEG-2 DD 3.1-SPLiFF"
    )
    assert alias.identity.alternate_titles == ("Onnen maa",)


def test_tv_boundaries_and_multi_episode():
    season = parse_release("Tyler Perry's Zatima S04 2160p AMZN WEB-DL DD+ 5.1 H.265-Kitsune")
    assert season.identity.title_candidate == "Tyler Perry's Zatima"
    assert season.identity.season == 4
    assert season.identity.episodes == ()
    assert season.provider == "AMZN"

    worded_pack = parse_release("Tyler Perry's Zatima Season 4 2160p AMZN WEB-DL DD+ 5.1 H.265-Kitsune")
    assert worded_pack.identity.title_candidate == "Tyler Perry's Zatima"
    assert worded_pack.identity.season == 4
    assert worded_pack.identity.episodes == ()

    episode = parse_release("Hey Boo S01E02 Nikita Iman 2160p WOWP WEB-DL AAC 2.0 H.264-Kitsune")
    assert episode.identity.title_candidate == "Hey Boo"
    assert episode.identity.season == 1
    assert episode.identity.episodes == (2,)
    assert episode.identity.episode_title == "Nikita Iman"
    assert episode.video.codec == "H.264"

    multi = parse_release("Show.Name.S01E01E02.1080p.WEB-DL.H.264-GROUP")
    assert multi.identity.season == 1
    assert multi.identity.episodes == (1, 2)

    ranged = parse_release("Show Name S02E01-E03 1080p HDTV-GROUP")
    assert ranged.identity.season == 2
    assert ranged.identity.episodes == (1, 2, 3)

    multi_season = parse_release("Dexters.Laboratory.S01-S04.1080p.WEB-DL.H.264-squalor")
    assert multi_season.identity.season == 1
    assert multi_season.identity.seasons == (1, 2, 3, 4)
    assert multi_season.identity.to_dict()["seasons"] == [1, 2, 3, 4]


def test_complete_multi_season_show_release_is_structured():
    result = parse_release(
        "Batman.The.Animated.Series.COMPLETE.S01-S04.1080p.BluRay."
        "DTS-HD.MA.H.264-BTN"
    )

    assert result.identity.title_candidate == "Batman The Animated Series"
    assert result.identity.season == 1
    assert result.identity.seasons == (1, 2, 3, 4)
    assert result.quality.canonical == "1080p Encode"
    assert result.release_group == "BTN"
    assert result.attributes.complete
    assert "COMPLETE" in result.attributes.values
    assert result.attributes.to_dict()["complete"] is True
    assert "COMPLETE" not in result.unknown_tokens


def test_hyphenated_show_titles_are_not_mistaken_for_release_groups():
    x_files = parse_release("The X-Files")
    fresh_prince = parse_release("The Fresh Prince of Bel-Air")

    assert x_files.identity.title_candidate == "The X-Files"
    assert x_files.release_group == "NoGroup"
    assert fresh_prince.identity.title_candidate == "The Fresh Prince of Bel-Air"
    assert fresh_prince.release_group == "NoGroup"


def test_worded_season_episode_scheme_is_parsed():
    result = parse_release(
        "The.Walking.Dead.Season.2.Episode.02.Bloodletting.REMUX-FraMeSToR"
    )

    assert result.identity.title_candidate == "The Walking Dead"
    assert result.identity.season == 2
    assert result.identity.episodes == (2,)
    assert result.identity.episode_title == "Bloodletting"
    assert result.release_group == "FraMeSToR"


def test_quality_precedence_and_encode_codec_preservation():
    remux = parse_release("Movie 2020 2160p UHD BluRay REMUX HEVC-G")
    assert remux.quality.canonical == "2160p BluRay REMUX"

    encode = parse_release("Movie 2020 1080p BluRay x264-G")
    assert encode.quality.canonical == "1080p Encode"
    assert encode.video.codec == "x264"

    dvd = parse_release("Movie 2020 480p NTSC DVD5 DVD9 MPEG-2-G")
    assert dvd.quality.canonical == "Full Disc DVD5/DVD9"


def test_structured_tokens_and_hybrid_is_not_edition():
    result = parse_release(
        "Inception 2010 Hybrid 2160p UHD BluRay REMUX DV HDR HEVC DTS-HD MA 5.1-LM"
    )
    assert result.edition is None
    assert result.attributes.hybrid
    assert result.hdr.dolby_vision and result.hdr.hdr
    assert result.video.codec == "HEVC"
    assert result.audio.codec == "DTS-HD MA"
    assert result.audio.channels == "5.1"


def test_no_group_and_unknown_tokens_are_explicit():
    no_group = parse_release("Night of the Living Deb 2015 1080p BluRay REMUX AVC DTS-HD MA 5.1")
    assert no_group.release_group == "NoGroup"
    assert "release_group_inferred_nogroup" in no_group.warnings

    future = parse_release("Movie 2020 1080p WEB-DL SomeFutureToken H.264-G")
    assert "SomeFutureToken" in future.unknown_tokens


def test_result_snapshot_is_jsonable():
    result = parse_release("Movie 2020 1080p WEB-DL-G")
    assert "parser_version" not in result.to_dict()
    assert result.to_dict()["quality"]["canonical"] == "1080p WEB-DL"



def test_progressive_dvd_remux_qualities_are_canonical_supported_values():
    ntsc = parse_release("Movie 2000 480p NTSC DVD REMUX MPEG-2 DD 2.0-GROUP")
    pal = parse_release("Movie 2000 576p PAL DVD REMUX MPEG-2 DD 2.0-GROUP")

    assert ntsc.quality.canonical == "480p DVD REMUX"
    assert pal.quality.canonical == "576p DVD REMUX"
    assert ntsc.quality.tv_standard == "NTSC"
    assert pal.quality.tv_standard == "PAL"

    from app.parser.quality import QUALITY_BY_NAME

    assert ntsc.quality.canonical in QUALITY_BY_NAME
    assert pal.quality.canonical in QUALITY_BY_NAME


def test_the_same_resolution_is_one_full_disc_quality_whatever_the_broadcast_standard():
    pal = parse_release("Movie 2000 576p PAL DVD MPEG-2 DD 2.0-GROUP")
    ntsc = parse_release("Movie 2000 576p NTSC DVD MPEG-2 DD 2.0-GROUP")

    assert pal.quality.canonical == ntsc.quality.canonical == "Full Disc DVD"
    assert (pal.quality.tv_standard, ntsc.quality.tv_standard) == ("PAL", "NTSC")


def test_a_full_disc_dvd_without_a_standard_token_is_still_classified():
    parsed = parse_release("Movie 2000 480p DVD MPEG-2 DD 2.0-GROUP")

    assert parsed.quality.canonical == "Full Disc DVD"
    assert parsed.quality.tv_standard is None


def test_extended_resolution_and_source_combinations_are_structured():
    from app.parser.quality import QUALITY_BY_NAME

    cases = {
        "Movie 2026 2160p WEB-DL AV1-GROUP": "2160p WEB-DL",
        "Movie 2026 1440p WEBRip H.265-GROUP": "1440p WEBRip",
        "Movie 2026 1080i HDTV MPEG-2-GROUP": "1080i HDTV",
        "Movie 2026 1080i BluRay REMUX AVC DTS-HD MA 5.1-GROUP": "1080i BluRay REMUX",
        "Movie 2026 1080i BluRay x264-GROUP": "1080i Encode",
        "Movie 2026 540p WEB-DL H.264-GROUP": "540p WEB-DL",
        "Movie 2020 720p BluRay REMUX AVC-GROUP": "720p BluRay REMUX",
        "Movie 2000 576i PAL DVD MPEG-2-GROUP": "Full Disc DVD",
        "Movie 2000 480p DVD REMUX MPEG-2-GROUP": "480p DVD REMUX",
    }

    for release_name, expected in cases.items():
        parsed = parse_release(release_name)
        assert parsed.quality.canonical == expected
        assert expected in QUALITY_BY_NAME


def test_full_disc_dvd_counts_are_evidence_not_separate_qualities():
    cases = {
        "Movie 2000 2xDVD9 MPEG-2-GROUP": ("Full Disc DVD9", "2xDVD9"),
        "Movie 2000 3xDVD5 MPEG-2-GROUP": ("Full Disc DVD5", "3xDVD5"),
        "Movie 2000 DVD9x2 MPEG-2-GROUP": ("Full Disc DVD9", "2xDVD9"),
        "Movie 2000 2xDVD5 DVD9 MPEG-2-GROUP": ("Full Disc DVD5/DVD9", "2xDVD5 + DVD9"),
    }

    for release_name, (quality, layout) in cases.items():
        parsed = parse_release(release_name)
        assert parsed.identity.title_candidate == "Movie"
        assert parsed.quality.canonical == quality
        assert parsed.quality.disc_type == layout


def test_unlisted_numeric_resolution_keeps_its_structured_source():
    parsed = parse_release("Movie 2026 900p WEB-DL H.264-GROUP")

    assert parsed.quality.canonical == "900p WEB-DL"
    assert parsed.quality.resolution == "900p"
    assert parsed.quality.source == "WEB-DL"


def test_leading_year_movie_directory_names_keep_the_title_candidate():
    emperor = parse_release("2000 The Emperor's New Groove")
    lilo = parse_release("2005 Lilo & Stitch 2 - Stitch Has a Glitch")

    assert emperor.identity.title_candidate == "The Emperor's New Groove"
    assert emperor.identity.year == 2000
    assert lilo.identity.title_candidate == "Lilo & Stitch 2 - Stitch Has a Glitch"
    assert lilo.identity.year == 2005


def test_vc1_is_recognized_as_video_codec_not_unknown_release_text():
    result = parse_release("9 2009 1080p BluRay REMUX VC-1 DTS-HD MA 5.1-Spark")

    assert result.identity.title_candidate == "9"
    assert result.identity.year == 2009
    assert result.video.codec == "VC-1"
    assert "VC-1" not in result.unknown_tokens


def test_tracker_naming_convention_distinguishes_encodes_and_full_discs() -> None:
    decimal_title = parse_release(
        "Jackass.2.5.2007.480p.PMTP.WEB-DL.AAC.2.0.H.264-Muffin"
    )
    dvd_rip = parse_release(
        "Love Finds Andy Hardy 1938 480p DVDRip DD 1.0 x264-HANDJOB"
    )
    bluray = parse_release("Ava 2020 1080p AUS Blu-ray AVC DTS-HD MA 5.1-NAND")
    uhd = parse_release(
        "Antibodies 2020 2160p USA UHD Blu-ray HEVC TrueHD 7.1 Atmos-BOMBARDEiRS"
    )
    dvd = parse_release("Melody Time 1948 Censored 480p NTSC DVD MPEG-2 DD 2.0-Nadya")

    assert decimal_title.identity.title_candidate == "Jackass 2.5"
    assert decimal_title.quality.canonical == "480p WEB-DL"
    assert dvd_rip.quality.canonical == "480p DVD Encode"
    assert bluray.quality.canonical == "Full Disc BluRay"
    assert bluray.regions == ("AUS",)
    assert uhd.quality.canonical == "Full Disc UHD BluRay"
    assert uhd.regions == ("USA",)
    assert dvd.quality.canonical == "Full Disc DVD"


def test_tracker_identity_metadata_and_revision_markers_are_structured() -> None:
    geographic = parse_release(
        "The Office (US) 2005 S01 1080p WEB-DL DDP5.1 H.264-GRP"
    )
    revision = parse_release(
        "Movie 2024 REPACK2 PROPER3 RERIP2 3D 1080p WEB-DL Dual-Audio Auro3D DDP5.1-GRP"
    )
    no_group = parse_release("Movie 2024 1080p WEB-DL MULTI DDP5.1-NOGRP")

    assert geographic.identity.title_candidate == "The Office"
    assert geographic.identity.geographic == "US"
    assert "US" in geographic.regions
    assert "DDP5" not in geographic.unknown_tokens
    assert revision.attributes.repack_revision == 2
    assert revision.attributes.proper_revision == 3
    assert revision.attributes.rerip_revision == 2
    assert revision.attributes.three_d is True
    assert revision.audio.dual_audio is True
    assert revision.audio.auro_3d is True
    assert no_group.release_group == "NoGroup"
    assert no_group.audio.multi_audio is True


def test_season_folder_names_cover_the_common_layouts() -> None:
    from app.parser import parse_season_folder

    assert parse_season_folder("Season 1") == 1
    assert parse_season_folder("Season.1") == 1
    assert parse_season_folder("Season_1") == 1
    assert parse_season_folder("Season 01") == 1
    assert parse_season_folder("S01") == 1
    assert parse_season_folder("S1") == 1
    assert parse_season_folder("Series 2") == 2
    assert parse_season_folder("Specials") == 0
    assert parse_season_folder("Season 0") == 0
    assert parse_season_folder("S1940") == 1940
    assert parse_season_folder("Season 1960") == 1960
    assert parse_season_folder("Extras") is None
    assert parse_season_folder("Season") is None
    assert parse_season_folder("Dollface 2019") is None


def test_year_seasons_parse_as_season_and_episode() -> None:
    from app.parser import parse_release

    identity = parse_release(
        "Tom and Jerry (1940) - S1960E01 - Switchin' Kitten (1080p AMZN WEB-DL x265 Ghost)"
    ).identity
    assert identity.season == 1960
    assert identity.episodes == (1,)
    assert identity.title_candidate == "Tom and Jerry"
    assert identity.episode_title == "Switchin' Kitten"


def test_episode_only_filenames_yield_episode_numbers() -> None:
    from app.parser import extract_episode_numbers

    assert extract_episode_numbers("01 - Pilot") == (1,)
    assert extract_episode_numbers("11 - The One With The Thing") == (11,)
    assert extract_episode_numbers("01. Pilot") == (1,)
    assert extract_episode_numbers("E01 - Pilot") == (1,)
    assert extract_episode_numbers("Episode 4") == (4,)
    assert extract_episode_numbers("01-03 - Triple") == (1, 2, 3)
    assert extract_episode_numbers("1917") == ()
    assert extract_episode_numbers("300") == ()
    assert extract_episode_numbers("Some Title") == ()


def test_dolby_digital_plus_is_recognised_under_every_spelling() -> None:
    from app.parser import parse_release

    for name in (
        "X 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb",
        "X 2024 1080p WEB-DL DDP 5.1 x265",
        "X 2024 1080p WEB-DL DD+5.1 x265",
        "X 2024 1080p WEB-DL EAC3 5.1",
        "X 2024 1080p WEB-DL E-AC-3 5.1",
    ):
        audio = parse_release(name).audio
        assert audio.codec == "DD+", name
        assert audio.channels == "5.1", name


def test_channel_layout_attached_to_the_codec_is_read() -> None:
    from app.parser import parse_release

    for name, codec in (
        ("X 2160p WEB-DL TrueHD7.1", "TrueHD"),
        ("X 1080p BluRay DTS-HD MA5.1", "DTS-HD MA"),
        ("X 1080p WEB AAC2.0", "AAC"),
    ):
        audio = parse_release(name).audio
        assert audio.codec == codec, name
        assert audio.channels is not None, name


def test_plain_dts_and_ac3_are_distinguished_from_their_lossless_variants() -> None:
    from app.parser import parse_release

    assert parse_release("X 1080p BluRay DTS 5.1 x264").audio.codec == "DTS"
    assert parse_release("X 1080p BluRay DTS-HD MA 5.1").audio.codec == "DTS-HD MA"
    assert parse_release("X 1080p BluRay AC3 2.0").audio.codec == "DD"
    assert parse_release("X 1080p WEB-DL DD 5.1").audio.codec == "DD"


def test_dts_extended_surround_is_distinct_from_core_dts() -> None:
    from app.parser import parse_release

    for name in ("X 1080p BluRay DTS-ES 5.1", "X 1080p BluRay DTS-ES Discrete 6.1", "X 1080p BluRay DTS ES Matrix 6.1"):
        assert parse_release(name).audio.codec == "DTS-ES", name
    assert parse_release("X 1080p BluRay DTS 5.1").audio.codec == "DTS"
    assert parse_release("X 1080p BluRay DTS-HD MA 5.1").audio.codec == "DTS-HD MA"
    assert parse_release("X 2160p BluRay DTS-X 7.1").audio.codec == "DTS:X"


def test_paramount_plus_is_recognised_under_its_scene_tag() -> None:
    from app.parser import parse_release

    for name in (
        "Show S01E01 1080p PMTP WEB-DL DDP5.1 H.264-NTb",
        "Show S01E01 1080p Paramount+ WEB-DL DDP5.1-NTb",
    ):
        assert parse_release(name).provider == "PMTP", name


def test_bravia_core_is_recognised_under_its_scene_tag() -> None:
    from app.parser import parse_release

    assert parse_release("Movie 2024 2160p BCORE WEB-DL DDP5.1 H.265-GRP").provider == "BCORE"


def test_short_provider_tags_are_bounded_by_the_web_dl_marker() -> None:
    from app.parser import parse_release

    assert parse_release("Show S01E01 1080p CR WEB-DL AAC2.0-NTb").provider == "CR"
    assert parse_release("Show S01E01 1080p iP WEB-DL AAC2.0-NTb").provider == "iP"
    assert parse_release("Movie 2024 1080p BluRay DTS-HD MA 5.1 x264-GRP").provider is None


def test_uncut_and_super_sized_are_editions_in_their_own_right():
    assert parse_release("Movie 2010 Uncut 1080p BluRay x264-GROUP").edition == "Uncut"
    assert parse_release("Movie 2007 Super-Sized 1080p BluRay x264-GROUP").edition == "Super-Sized"


def test_a_release_naming_both_takes_the_longer_edition():
    parsed = parse_release("Superbad 2007 Super-Sized and Uncut 1080p BluRay x264-GROUP")
    assert parsed.edition == "Super-Sized"


def test_the_combined_edition_is_not_offered_as_a_choice():
    from app.parser.release_parser import EDITION_OPTIONS

    assert "Uncut" in EDITION_OPTIONS
    assert "Super-Sized" in EDITION_OPTIONS
    assert "Super-Sized and Uncut" not in EDITION_OPTIONS


def test_redux_is_detected_as_a_movie_edition():
    parsed = parse_release("Apocalypse.Now.1979.Redux.2160p.UHD.BluRay.REMUX-GROUP")

    assert parsed.edition == "Redux"
    assert parsed.identity.title_candidate == "Apocalypse Now"


@pytest.mark.parametrize(
    ("name", "title", "year", "quality"),
    [
        ("500.Days.of.Summer.1080p.2009.DTS-HD.MA.5.1.AVC.REMUX-FraMeSToR", "500 Days of Summer", 2009, "1080p BluRay REMUX"),
        ("Movie.Name.2160p.2019.UHD.BluRay-GRP", "Movie Name", 2019, "2160p Encode"),
        ("Movie.Name.1080p.BluRay.2019-GRP", "Movie Name", 2019, "1080p Encode"),
        ("Blade.Runner.2049.1080p.2017.BluRay-GRP", "Blade Runner 2049", 2017, "1080p Encode"),
        ("Show.Name.1080p.2019.S01E01.WEB-DL-GRP", "Show Name", 2019, "1080p WEB-DL"),
        ("1917.2019.2160p.UHD.BluRay.REMUX.HDR.HEVC-GRP", "1917", 2019, "2160p BluRay REMUX"),
    ],
)
def test_release_tokens_before_the_year_are_not_part_of_the_title(name, title, year, quality) -> None:
    parsed = parse_release(name)

    assert parsed.identity.title_candidate == title
    assert parsed.identity.year == year
    assert parsed.quality.canonical == quality


def test_a_remux_without_its_source_is_a_bluray_remux_but_a_dvd_remux_stays_one() -> None:
    assert parse_release("Movie.2009.1080p.DTS-HD.MA.5.1.AVC.REMUX-GRP").quality.canonical == "1080p BluRay REMUX"
    assert parse_release("Movie.2009.DVD.REMUX.MPEG-2-GRP").quality.source == "DVD"


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("Gangs.of.New.York.Remastered.2002.BluRay.1080p.DTS-HD.MA.5.1.AVC.REMUX-FraMeSToR", "Gangs of New York"),
        ("Movie.Remastered.1080p.2009.BluRay-GRP", "Movie"),
        ("The.Remaster.Show.2019.1080p-GRP", "The Remaster Show"),
        ("Remastered.2019.1080p.WEB-DL-GRP", "Remastered"),
    ],
)
def test_remastered_right_before_the_year_is_not_part_of_the_title(name, title) -> None:
    assert parse_release(name).identity.title_candidate == title


def test_alternate_and_international_cuts_are_editions() -> None:
    from app.parser.release_parser import EDITION_OPTIONS

    assert parse_release("Movie.2010.Alternate.Cut.1080p.BluRay-GRP").edition == "Alternate Cut"
    assert parse_release("Movie.2010.Alternative.Cut.1080p.BluRay-GRP").edition == "Alternate Cut"
    assert parse_release("Movie.2010.International.Cut.1080p.BluRay-GRP").edition == "International Cut"
    assert {"Alternate Cut", "International Cut"} <= set(EDITION_OPTIONS)


def test_hdr10p_is_hdr10_plus() -> None:
    parsed = parse_release("Jurassic.World.Rebirth.2025.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HDR10P.HEVC.HYBRID.REMUX-FraMeSToR")
    assert parsed.hdr.hdr10_plus and not parsed.hdr.hdr10
    assert parsed.hdr.values == ("Dolby Vision", "HDR10+")


@pytest.mark.parametrize(
    ("name", "edition"),
    [
        ("Sahara.2005.OpenMatte.BluRay.1080p.TrueHD.5.1.AVC.REMUX-FraMeSToR", "Open Matte"),
        ("Movie.2010.DirectorsCut.1080p.BluRay-GRP", "Director's Cut"),
        ("Movie.2010.Open.Matte.1080p.BluRay-GRP", "Open Matte"),
    ],
)
def test_editions_are_found_with_their_words_run_together(name, edition) -> None:
    assert parse_release(name).edition == edition


@pytest.mark.parametrize(
    ("name", "title", "edition"),
    [
        ("Twilight.Eclipse.Extended.Cut.2010.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-OiCuNTs", "Twilight Eclipse", "Extended Cut"),
        ("Twilight.Extended.2008.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-RiPPERSDELiGHT", "Twilight", "Extended Cut"),
        ("Movie.Directors.Cut.Remastered.1999.1080p.BluRay-GRP", "Movie", "Director's Cut"),
        ("Apocalypse.Now.Redux.1979.1080p.BluRay-GRP", "Apocalypse Now", "Redux"),
        # A film named like an edition keeps its title (its edition is not asserted here).
        ("The.Final.Cut.2004.1080p.BluRay-GRP", "The Final Cut", None),
    ],
)
def test_an_edition_right_before_the_year_is_not_part_of_the_title(name, title, edition) -> None:
    parsed = parse_release(name)
    assert parsed.identity.title_candidate == title
    if edition is not None:
        assert parsed.edition == edition
