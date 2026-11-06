"""A title whose source is lost is Replace Media's, not Upgrade Media's."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.api.upgrades import _left_to_replace_media


def _row(*, movie_id=None, season_id=None, execution_state=None):
    return SimpleNamespace(movie_id=movie_id, season_id=season_id, execution_state=execution_state)


def test_a_title_replace_media_handles_leaves_upgrade_media() -> None:
    movie, season = uuid.uuid4(), uuid.uuid4()
    replacing = {str(movie), str(season)}
    assert _left_to_replace_media(_row(movie_id=movie), replacing)
    assert _left_to_replace_media(_row(season_id=season), replacing)


def test_other_titles_stay_in_upgrade_media() -> None:
    assert not _left_to_replace_media(_row(movie_id=uuid.uuid4()), {str(uuid.uuid4())})


def test_an_upgrade_under_way_stays_so_its_progress_shows() -> None:
    movie = uuid.uuid4()
    assert not _left_to_replace_media(_row(movie_id=movie, execution_state="downloading"), {str(movie)})
