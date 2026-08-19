from app.db.base import Base
from app.models.domain import Movie, MovieRelease, Torrent


def test_uuid_primary_keys_and_required_tables() -> None:
    assert isinstance(Movie.__table__.c.id.type, object)
    assert Movie.__table__.c.id.primary_key
    assert MovieRelease.__table__.c.movie_id.foreign_keys
    assert any("info_hash" in constraint.columns for constraint in Torrent.__table__.constraints if constraint.__class__.__name__ == "UniqueConstraint")
    assert "movies" in Base.metadata.tables
    assert "jobs" in Base.metadata.tables


def test_model_defaults_are_callable() -> None:
    movie = Movie(title="Test", sort_title="Test")
    assert movie.id is None  # SQLAlchemy assigns Python defaults on flush.


def test_tmdb_ids_are_unique_and_scope_supports_both() -> None:
    from app.models.domain import CustomFormat, Indexer, MediaScope, Show

    movie_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in Movie.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    show_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in Show.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("tmdb_id",) in movie_unique
    assert ("tmdb_id",) in show_unique
    assert MediaScope.BOTH.value == "both"
    assert CustomFormat.__table__.c.media_scope is not None
    assert Indexer.__table__.c.scope is not None
    assert "score" not in CustomFormat.__table__.c
    assert "enabled" in CustomFormat.__table__.c


def test_show_hierarchy_tracks_revisions_and_independent_file_presence() -> None:
    from app.models.domain import Episode, MediaFile, Season, Show

    assert "revision" in Show.__table__.c
    assert "revision" in Season.__table__.c
    assert "revision" in Episode.__table__.c
    assert {"last_exists_check_at", "missing_since", "missing_check_count"} <= set(MediaFile.__table__.c.keys())
