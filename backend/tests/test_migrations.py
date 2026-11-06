"""Regression checks for the single current clean-install Alembic baseline."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

from sqlalchemy import create_engine, inspect


def _backend_dir() -> Path:
    return Path(__file__).parents[1]


def test_upgrade_head_on_fresh_sqlite_database_matches_current_schema() -> None:
    backend_dir = _backend_dir()
    database_path = Path(tempfile.mktemp(prefix="medialogue-migrations-", suffix=".db", dir=backend_dir))
    database_url = f"sqlite:///{database_path}"
    environment = {**os.environ, "MEDIALOGUE_DATABASE_URL": database_url}
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
            tables = set(inspector.get_table_names())
            download_client_columns = {column["name"] for column in inspector.get_columns("download_clients")}
            media_directory_columns = {column["name"]: column for column in inspector.get_columns("media_directories")}
            torrent_columns = {column["name"]: column for column in inspector.get_columns("torrents")}
            movie_columns = {column["name"] for column in inspector.get_columns("movies")}
            movie_release_columns = {column["name"] for column in inspector.get_columns("movie_releases")}
            show_columns = {column["name"] for column in inspector.get_columns("shows")}
            storage_root_columns = {column["name"] for column in inspector.get_columns("storage_roots")}
            discovery_columns = {column["name"] for column in inspector.get_columns("media_discoveries")}
            schedule_columns = {column["name"] for column in inspector.get_columns("schedules")}
            job_columns = {column["name"] for column in inspector.get_columns("jobs")}
            job_indexes = {index["name"] for index in inspector.get_indexes("jobs")}
            tracker_health_columns = {
                column["name"] for column in inspector.get_columns("torrent_tracker_observations")
            }
            tracker_health_indexes = {
                index["name"] for index in inspector.get_indexes("torrent_tracker_observations")
            }
            torrent_observation_columns = {
                column["name"] for column in inspector.get_columns("torrent_client_observations")
            }
            upgrade_candidate_columns = {
                column["name"] for column in inspector.get_columns("upgrade_candidates")
            }
        finally:
            engine.dispose()
    finally:
        database_path.unlink(missing_ok=True)

    assert {
        "movies",
        "shows",
        "problems",
        "events",
        "jobs",
        "plex_configurations",
        "tmdb_configurations",
        "download_clients",
        "storage_roots",
        "media_directories",
        } <= tables
    assert "media_discoveries" in tables
    assert "confirmation_nonces" in tables
    assert "release_blocklist" in tables
    assert "discovery_mode" in storage_root_columns
    assert {
        "last_health_checked_at",
        "last_success_at",
        "latency_ms",
        "last_error",
    } <= download_client_columns
    assert media_directory_columns["storage_root_id"]["nullable"] is True
    assert "BIGINT" in str(torrent_columns["total_size"]["type"]).upper()
    assert "poster_ref" in movie_columns
    assert "poster_ref" in show_columns
    assert "edition_override_active" in movie_release_columns
    assert {"edition_override_active", "manual_edition_override"} <= discovery_columns
    assert {"last_started_at", "last_finished_at", "next_run_at", "last_status", "last_duration_ms"} <= schedule_columns
    assert "schedule_id" in job_columns
    assert {"ix_jobs_created", "uq_jobs_active_schedule"} <= job_indexes
    assert {"endpoint_key", "health_state", "terminal_confirmed", "last_success_at"} <= tracker_health_columns
    assert "ix_torrent_tracker_active_state" in tracker_health_indexes
    assert {
        "download_speed",
        "upload_speed",
        "eta_seconds",
        "amount_left",
        "ratio",
        "seeds",
        "seeds_total",
        "leeches",
        "leeches_total",
        "availability",
    } <= torrent_observation_columns
    assert {
        "execution_state",
        "submitted_info_hash",
        "selected_download_client_id",
        "torrent_id",
        "incoming_release_ids",
        "cleanup_completed_at",
        "search_results",
    } <= upgrade_candidate_columns


def test_upgrade_head_offline_sql_generation_succeeds() -> None:
    backend_dir = _backend_dir()
    environment = {**os.environ, "MEDIALOGUE_DATABASE_URL": "sqlite:///offline-migration-check.db"}
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
    assert "CREATE TABLE plex_configurations" in completed.stdout
    assert "INSERT INTO alembic_version" in completed.stdout
    assert "'0001'" in completed.stdout


def test_migration_history_is_the_single_fresh_baseline() -> None:
    backend_dir = _backend_dir()
    versions_dir = backend_dir / "alembic" / "versions"
    migrations = sorted(path for path in versions_dir.glob("*.py") if path.name != "__init__.py")
    assert [path.name for path in migrations] == ["0001_fresh_baseline.py"]

    source = migrations[0].read_text(encoding="utf-8")
    assert 'revision = "0001"' in source
    assert "down_revision = None" in source
    assert "Base.metadata.create_all" in source


def test_online_sqlite_migrations_use_installed_aiosqlite_driver() -> None:
    backend_dir = _backend_dir()
    source = (backend_dir / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "async_engine_from_config" in source
    assert 'configuration["sqlalchemy.url"] = get_settings().database_url' in source
    assert "await connection.run_sync" in source
