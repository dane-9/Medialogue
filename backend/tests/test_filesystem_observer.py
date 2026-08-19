from app.integrations.filesystem import FilesystemObserver


def test_scan_discovers_media_and_disc_structures_without_writes(tmp_path):
    movie = tmp_path / "Movie 2020"
    movie.mkdir()
    (movie / "Movie.2020.mkv").write_bytes(b"media")
    (movie / "poster.jpg").write_bytes(b"sidecar")
    (movie / "VIDEO_TS").mkdir()
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    observations = FilesystemObserver().scan_root(str(tmp_path))

    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert before == after
    assert len(observations) == 1
    assert observations[0].media_files == ("Movie.2020.mkv",)
    assert observations[0].has_dvd_structure
    assert not observations[0].has_bluray_structure
