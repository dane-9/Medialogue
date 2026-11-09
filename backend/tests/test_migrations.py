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
    assert "'0003'" in completed.stdout


def test_migration_history_is_the_fresh_baseline_then_linear_revisions() -> None:
    backend_dir = _backend_dir()
    versions_dir = backend_dir / "alembic" / "versions"
    migrations = sorted(path for path in versions_dir.glob("*.py") if path.name != "__init__.py")
    assert [path.name for path in migrations] == [
        "0001_fresh_baseline.py", "0002_ui_selections.py", "0003_release_identity_confidence.py",
    ]

    baseline = migrations[0].read_text(encoding="utf-8")
    assert 'revision = "0001"' in baseline
    assert "down_revision = None" in baseline
    assert "Base.metadata.create_all" in baseline
    # The baseline creates every current model, so later revisions must also
    # apply cleanly on top of a database that already has their tables.
    later = migrations[1].read_text(encoding="utf-8")
    assert 'down_revision = "0001"' in later
    assert "IF NOT EXISTS" in later


def test_an_existing_movie_release_gains_its_confidence_column(tmp_path) -> None:
    import sqlite3

    backend_dir = _backend_dir()
    database_path = tmp_path / "old.db"
    environment = {**os.environ, "MEDIALOGUE_DATABASE_URL": f"sqlite:///{database_path}"}

    def alembic(*arguments: str) -> None:
        done = subprocess.run(["alembic", "-c", str(backend_dir / "alembic.ini"), *arguments],
                              cwd=backend_dir, env=environment, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr

    alembic("upgrade", "head")
    alembic("downgrade", "0002")
    connection = sqlite3.connect(database_path)
    now = "2026-01-01 00:00:00"
    for release_id, snapshot in (("a", '{"identity_confidence": 0.75}'), ("b", "{}")):
        connection.execute(
            "INSERT INTO movie_releases (id, movie_id, raw_release_name, release_state, first_seen_at, "
            "edition_override_active, parse_snapshot, created_at, updated_at) VALUES (?, 'm', 'R', 'CURRENT', ?, 0, ?, ?, ?)",
            (release_id, now, snapshot, now, now),
        )
    connection.commit()
    connection.close()

    alembic("upgrade", "head")
    connection = sqlite3.connect(database_path)
    rows = dict(connection.execute("SELECT id, identity_confidence FROM movie_releases").fetchall())
    connection.close()
    assert rows == {"a": 0.75, "b": None}


def test_a_database_created_before_ui_selections_gains_the_table(tmp_path) -> None:
    """Existing installs were created by 0001 without the table; 0002 adds it."""

    backend_dir = _backend_dir()
    database_path = tmp_path / "old.db"
    database_url = f"sqlite:///{database_path}"
    environment = {**os.environ, "MEDIALOGUE_DATABASE_URL": database_url}
    upgraded = subprocess.run(["alembic", "-c", str(backend_dir / "alembic.ini"), "upgrade", "head"],
                              cwd=backend_dir, env=environment, capture_output=True, text=True)
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE ui_selections")
            connection.exec_driver_sql("UPDATE alembic_version SET version_num = '0001'")
    finally:
        engine.dispose()
    again = subprocess.run(["alembic", "-c", str(backend_dir / "alembic.ini"), "upgrade", "head"],
                           cwd=backend_dir, env=environment, capture_output=True, text=True)
    assert again.returncode == 0, again.stdout + again.stderr
    engine = create_engine(database_url)
    try:
        assert "ui_selections" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_online_sqlite_migrations_use_installed_aiosqlite_driver() -> None:
    backend_dir = _backend_dir()
    source = (backend_dir / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "async_engine_from_config" in source
    assert 'configuration["sqlalchemy.url"] = get_settings().database_url' in source
    assert "await connection.run_sync" in source
