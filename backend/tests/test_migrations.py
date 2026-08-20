"""Regression checks for the clean-install Alembic baseline.

Medialogue deliberately does not preserve pre-v9 migration compatibility.
Incompatible development builds are deployed against a freshly initialized
application database, so CI verifies one current baseline rather than upgrade
paths from historical schemas.
"""

from __future__ import annotations

import ast
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
            show_columns = {column["name"] for column in inspector.get_columns("shows")}
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
    assert {
        "last_health_checked_at",
        "last_success_at",
        "latency_ms",
        "last_error",
        "poll_interval_seconds",
    } <= download_client_columns
    assert media_directory_columns["storage_root_id"]["nullable"] is True
    assert "BIGINT" in str(torrent_columns["total_size"]["type"]).upper()
    assert "poster_ref" in movie_columns
    assert "poster_ref" in show_columns


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


def test_migration_history_is_intentionally_squashed_to_one_short_baseline() -> None:
    backend_dir = _backend_dir()
    versions_dir = backend_dir / "alembic" / "versions"
    migrations = sorted(path for path in versions_dir.glob("*.py") if path.name != "__init__.py")
    assert [path.name for path in migrations] == ["0001_fresh_baseline.py"]

    source = migrations[0].read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(migrations[0]))
    revision = down_revision = object()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        if "revision" in names:
            revision = node.value.value
        if "down_revision" in names:
            down_revision = node.value.value
    assert revision == "0001"
    assert down_revision is None
    # Keep revision identifiers well below Alembic's default VARCHAR(32).
    assert len(str(revision)) <= 32
    assert "Base.metadata.create_all" in source


def test_online_postgres_migrations_use_installed_asyncpg_driver() -> None:
    backend_dir = _backend_dir()
    source = (backend_dir / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "async_engine_from_config" in source
    assert 'configuration["sqlalchemy.url"] = get_settings().database_url' in source
    assert "await connection.run_sync" in source
