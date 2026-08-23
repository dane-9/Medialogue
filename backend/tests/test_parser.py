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
        "The Land of Happiness AKA Onnen maa",
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
        assert result.title == title
        assert result.year == year
        assert result.quality.canonical == quality
        assert result.edition == edition
        assert result.release_group == group


def test_tv_boundaries_and_multi_episode():
    season = parse_release("Tyler Perry's Zatima S04 2160p AMZN WEB-DL DD+ 5.1 H.265-Kitsune")
    assert season.title == "Tyler Perry's Zatima"
    assert season.season == 4
    assert season.episodes == ()
    assert season.provider == "AMZN"

    worded_pack = parse_release("Tyler Perry's Zatima Season 4 2160p AMZN WEB-DL DD+ 5.1 H.265-Kitsune")
    assert worded_pack.title == "Tyler Perry's Zatima"
    assert worded_pack.season == 4
    assert worded_pack.episodes == ()

    episode = parse_release("Hey Boo S01E02 Nikita Iman 2160p WOWP WEB-DL AAC 2.0 H.264-Kitsune")
    assert episode.title == "Hey Boo"
    assert episode.season == 1
    assert episode.episodes == (2,)
    assert episode.identity.episode_title == "Nikita Iman"
    assert episode.video.codec == "H.264"

    multi = parse_release("Show.Name.S01E01E02.1080p.WEB-DL.H.264-GROUP")
    assert multi.season == 1
    assert multi.episodes == (1, 2)

    ranged = parse_release("Show Name S02E01-E03 1080p HDTV-GROUP")
    assert ranged.season == 2
    assert ranged.episodes == (1, 2, 3)

    multi_season = parse_release("Dexters.Laboratory.S01-S04.1080p.WEB-DL.H.264-squalor")
    assert multi_season.season == 1
    assert multi_season.identity.season_numbers == (1, 2, 3, 4)
    assert multi_season.identity.to_dict()["seasons"] == [1, 2, 3, 4]


def test_hyphenated_show_titles_are_not_mistaken_for_release_groups():
    x_files = parse_release("The X-Files")
    fresh_prince = parse_release("The Fresh Prince of Bel-Air")

    assert x_files.title == "The X-Files"
    assert x_files.release_group == "NoGroup"
    assert fresh_prince.title == "The Fresh Prince of Bel-Air"
    assert fresh_prince.release_group == "NoGroup"


def test_worded_season_episode_scheme_is_parsed():
    result = parse_release(
        "The.Walking.Dead.Season.2.Episode.02.Bloodletting.REMUX-FraMeSToR"
    )

    assert result.title == "The Walking Dead"
    assert result.season == 2
    assert result.episodes == (2,)
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


def test_result_is_versioned_and_jsonable():
    result = parse_release("Movie 2020 1080p WEB-DL-G")
    assert result.parser_version
    assert result.to_dict()["parser_version"] == result.parser_version
    assert result.to_dict()["quality"]["canonical"] == "1080p WEB-DL"



def test_progressive_dvd_remux_qualities_are_canonical_supported_values():
    ntsc = parse_release("Movie 2000 480p NTSC DVD REMUX MPEG-2 DD 2.0-GROUP")
    pal = parse_release("Movie 2000 576p PAL DVD REMUX MPEG-2 DD 2.0-GROUP")

    assert ntsc.quality.canonical == "480p NTSC DVD REMUX"
    assert pal.quality.canonical == "576p PAL DVD REMUX"

    # The parser must not emit a canonical value absent from the hardcoded catalog.
    from app.parser.quality import QUALITY_BY_NAME

    assert ntsc.quality.canonical in QUALITY_BY_NAME
    assert pal.quality.canonical in QUALITY_BY_NAME


def test_leading_year_movie_directory_names_keep_the_title_candidate():
    emperor = parse_release("2000 The Emperor's New Groove")
    lilo = parse_release("2005 Lilo & Stitch 2 - Stitch Has a Glitch")

    assert emperor.title == "The Emperor's New Groove"
    assert emperor.year == 2000
    assert lilo.title == "Lilo & Stitch 2 - Stitch Has a Glitch"
    assert lilo.year == 2005


def test_vc1_is_recognized_as_video_codec_not_unknown_release_text():
    result = parse_release("9 2009 1080p BluRay REMUX VC-1 DTS-HD MA 5.1-Spark")

    assert result.title == "9"
    assert result.year == 2009
    assert result.video.codec == "VC-1"
    assert "VC-1" not in result.unknown_tokens


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
    # Shows organised by production year, e.g. Tom and Jerry.
    assert parse_season_folder("S1940") == 1940
    assert parse_season_folder("Season 1960") == 1960
    # Anything that is not a season folder must stay unrecognised.
    assert parse_season_folder("Extras") is None
    assert parse_season_folder("Season") is None
    assert parse_season_folder("Dollface 2019") is None


def test_year_seasons_parse_as_season_and_episode() -> None:
    from app.parser import parse_release_name

    identity = parse_release_name(
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
    # A bare number with no separator is a title, not an episode index.
    assert extract_episode_numbers("1917") == ()
    assert extract_episode_numbers("300") == ()
    assert extract_episode_numbers("Some Title") == ()


def test_dolby_digital_plus_is_recognised_under_every_spelling() -> None:
    from app.parser import parse_release_name

    for name in (
        "X 2024 1080p AMZN WEB-DL DDP5.1 H.264-NTb",
        "X 2024 1080p WEB-DL DDP 5.1 x265",
        "X 2024 1080p WEB-DL DD+5.1 x265",
        "X 2024 1080p WEB-DL EAC3 5.1",
        "X 2024 1080p WEB-DL E-AC-3 5.1",
    ):
        audio = parse_release_name(name).audio
        assert audio.codec == "DD+", name
        assert audio.channels == "5.1", name


def test_channel_layout_attached_to_the_codec_is_read() -> None:
    from app.parser import parse_release_name

    for name, codec in (
        ("X 2160p WEB-DL TrueHD7.1", "TrueHD"),
        ("X 1080p BluRay DTS-HD MA5.1", "DTS-HD MA"),
        ("X 1080p WEB AAC2.0", "AAC"),
    ):
        audio = parse_release_name(name).audio
        assert audio.codec == codec, name
        assert audio.channels is not None, name


def test_plain_dts_and_ac3_are_distinguished_from_their_lossless_variants() -> None:
    from app.parser import parse_release_name

    assert parse_release_name("X 1080p BluRay DTS 5.1 x264").audio.codec == "DTS"
    assert parse_release_name("X 1080p BluRay DTS-HD MA 5.1").audio.codec == "DTS-HD MA"
    # AC3 is Dolby Digital; it must not be mistaken for Dolby Digital Plus.
    assert parse_release_name("X 1080p BluRay AC3 2.0").audio.codec == "DD"
    assert parse_release_name("X 1080p WEB-DL DD 5.1").audio.codec == "DD"


def test_dts_extended_surround_is_distinct_from_core_dts() -> None:
    from app.parser import parse_release_name

    for name in ("X 1080p BluRay DTS-ES 5.1", "X 1080p BluRay DTS-ES Discrete 6.1", "X 1080p BluRay DTS ES Matrix 6.1"):
        assert parse_release_name(name).audio.codec == "DTS-ES", name
    assert parse_release_name("X 1080p BluRay DTS 5.1").audio.codec == "DTS"
    assert parse_release_name("X 1080p BluRay DTS-HD MA 5.1").audio.codec == "DTS-HD MA"
    assert parse_release_name("X 2160p BluRay DTS-X 7.1").audio.codec == "DTS:X"


def test_paramount_plus_is_recognised_under_its_scene_tag() -> None:
    from app.parser import parse_release_name

    for name in (
        "Show S01E01 1080p PMTP WEB-DL DDP5.1 H.264-NTb",
        "Show S01E01 1080p Paramount+ WEB-DL DDP5.1-NTb",
    ):
        assert parse_release_name(name).provider == "PMTP", name


def test_bravia_core_is_recognised_under_its_scene_tag() -> None:
    from app.parser import parse_release_name

    assert parse_release_name("Movie 2024 2160p BCORE WEB-DL DDP5.1 H.265-GRP").provider == "BCORE"


def test_short_provider_tags_are_bounded_by_the_web_dl_marker() -> None:
    from app.parser import parse_release_name

    assert parse_release_name("Show S01E01 1080p CR WEB-DL AAC2.0-NTb").provider == "CR"
    assert parse_release_name("Show S01E01 1080p iP WEB-DL AAC2.0-NTb").provider == "iP"
    # MA here is DTS-HD Master Audio, not the Movies Anywhere provider.
    assert parse_release_name("Movie 2024 1080p BluRay DTS-HD MA 5.1 x264-GRP").provider is None
