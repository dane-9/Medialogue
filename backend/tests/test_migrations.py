"""Regression checks for applying the complete Alembic history to a fresh DB."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

from sqlalchemy import create_engine, inspect


def test_upgrade_head_on_fresh_sqlite_database_has_no_duplicate_ddl() -> None:
    """Every revision must be runnable in order, not only against ORM metadata."""

    backend_dir = Path(__file__).parents[1]
    database_path = Path(tempfile.mktemp(prefix="medialogue-migrations-", suffix=".db", dir=backend_dir))
    database_url = f"sqlite:///{database_path}"
    environment = {
        **os.environ,
        "MEDIALOGUE_DATABASE_URL": database_url,
    }
    try:
        completed = subprocess.run(
            ["alembic", "-c", str(backend_dir / "alembic.ini"), "upgrade", "head"],
            cwd=backend_dir,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            columns = {column["name"] for column in inspector.get_columns("download_clients")}
            custom_format_columns = {column["name"] for column in inspector.get_columns("custom_formats")}
            quality_profile_columns = {column["name"] for column in inspector.get_columns("quality_profiles")}
            profile_override_columns = {column["name"] for column in inspector.get_columns("media_profile_overrides")}
            show_columns = {column["name"] for column in inspector.get_columns("shows")}
            season_columns = {column["name"] for column in inspector.get_columns("seasons")}
            episode_columns = {column["name"] for column in inspector.get_columns("episodes")}
            media_file_columns = {column["name"] for column in inspector.get_columns("media_files")}
            profile_indexes = {index["name"] for index in inspector.get_indexes("quality_profiles")}
            override_indexes = {index["name"] for index in inspector.get_indexes("media_profile_overrides")}
            tables = set(inspector.get_table_names())
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)

    assert {"revision", "poll_interval_seconds", "last_polled_at"} <= columns
    assert {"enabled", "revision"} <= custom_format_columns
    assert "revision" in quality_profile_columns
    assert "revision" in profile_override_columns
    assert "revision" in show_columns
    assert "revision" in season_columns
    assert "revision" in episode_columns
    assert {"last_exists_check_at", "missing_since", "missing_check_count"} <= media_file_columns
    assert "uq_quality_profiles_name" in profile_indexes
    assert {"uq_media_profile_override_movie", "uq_media_profile_override_show"} <= override_indexes
    assert "tmdb_configurations" in tables


def test_upgrade_head_offline_sql_generation_succeeds() -> None:
    """Offline SQL must render the same complete history without DB inspection."""

    backend_dir = Path(__file__).parents[1]
    environment = {
        **os.environ,
        "MEDIALOGUE_DATABASE_URL": "sqlite:///offline-migration-check.db",
    }
    completed = subprocess.run(
        ["alembic", "-c", str(backend_dir / "alembic.ini"), "upgrade", "head", "--sql"],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "CREATE TABLE download_clients" in completed.stdout
    assert "CREATE TABLE tmdb_configurations" in completed.stdout
    assert "0005_foundation_fixes" in completed.stdout


def test_initial_migration_is_explicit_and_does_not_import_live_orm_metadata() -> None:
    backend_dir = Path(__file__).parents[1]
    migration = backend_dir / "alembic" / "versions" / "0001_initial.py"
    source = migration.read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert "app.models" not in source
    assert "op.create_table" in source

    plex_revision = backend_dir / "alembic" / "versions" / "0002_plex_configuration.py"
    plex_source = plex_revision.read_text(encoding="utf-8")
    assert "app.models" not in plex_source
    assert "PlexConfiguration.__table__" not in plex_source
    assert (backend_dir / "alembic" / "versions" / "0004_reconciliation_state.py").exists()
    assert (backend_dir / "alembic" / "versions" / "0005_foundation_fixes.py").exists()
    assert (backend_dir / "alembic" / "versions" / "0006_interactive_search.py").exists()
    assert (backend_dir / "alembic" / "versions" / "0007_custom_formats.py").exists()
    assert (backend_dir / "alembic" / "versions" / "0008_quality_profiles.py").exists()
    assert (backend_dir / "alembic" / "versions" / "0009_shows_seasons_episodes.py").exists()


def test_upgrade_existing_part11_database_to_part12() -> None:
    """A real Part 11 schema must upgrade cleanly without rebuilding the DB."""

    backend_dir = Path(__file__).parents[1]
    database_path = Path(tempfile.mktemp(prefix="medialogue-part11-upgrade-", suffix=".db", dir=backend_dir))
    database_url = f"sqlite:///{database_path}"
    environment = {**os.environ, "MEDIALOGUE_DATABASE_URL": database_url}
    try:
        to_part11 = subprocess.run(
            ["alembic", "-c", str(backend_dir / "alembic.ini"), "upgrade", "0007_custom_formats"],
            cwd=backend_dir, env=environment, capture_output=True, text=True,
        )
        assert to_part11.returncode == 0, to_part11.stdout + to_part11.stderr
        to_part12 = subprocess.run(
            ["alembic", "-c", str(backend_dir / "alembic.ini"), "upgrade", "head"],
            cwd=backend_dir, env=environment, capture_output=True, text=True,
        )
        assert to_part12.returncode == 0, to_part12.stdout + to_part12.stderr

        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            assert "revision" in {column["name"] for column in inspector.get_columns("quality_profiles")}
            assert "revision" in {column["name"] for column in inspector.get_columns("media_profile_overrides")}
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_upgrade_existing_part12_database_to_part13() -> None:
    """A real Part 12 schema must upgrade in place to the Show hierarchy additions."""

    backend_dir = Path(__file__).parents[1]
    database_path = Path(tempfile.mktemp(prefix="medialogue-part12-upgrade-", suffix=".db", dir=backend_dir))
    database_url = f"sqlite:///{database_path}"
    environment = {**os.environ, "MEDIALOGUE_DATABASE_URL": database_url}
    try:
        to_part12 = subprocess.run(
            ["alembic", "-c", str(backend_dir / "alembic.ini"), "upgrade", "0008_quality_profiles"],
            cwd=backend_dir, env=environment, capture_output=True, text=True,
        )
        assert to_part12.returncode == 0, to_part12.stdout + to_part12.stderr
        to_part13 = subprocess.run(
            ["alembic", "-c", str(backend_dir / "alembic.ini"), "upgrade", "head"],
            cwd=backend_dir, env=environment, capture_output=True, text=True,
        )
        assert to_part13.returncode == 0, to_part13.stdout + to_part13.stderr

        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            assert "revision" in {column["name"] for column in inspector.get_columns("shows")}
            assert "revision" in {column["name"] for column in inspector.get_columns("seasons")}
            assert "revision" in {column["name"] for column in inspector.get_columns("episodes")}
            assert {"last_exists_check_at", "missing_since", "missing_check_count"} <= {
                column["name"] for column in inspector.get_columns("media_files")
            }
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)


def test_online_postgres_migrations_use_installed_asyncpg_driver() -> None:
    """Container startup must not require an undeclared synchronous PG DBAPI."""

    backend_dir = Path(__file__).parents[1]
    source = (backend_dir / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "async_engine_from_config" in source
    assert 'configuration["sqlalchemy.url"] = get_settings().database_url' in source
    assert "await connection.run_sync" in source
