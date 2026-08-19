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
