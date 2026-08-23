"""Recovery-bundle export services.

A recovery bundle is intentionally more than a logical application export. It
contains a PostgreSQL physical base backup, archived torrent evidence, a
human-readable configuration export, and a human-readable library inventory.
The physical backup is produced with PostgreSQL's own ``pg_basebackup`` tool;
Medialogue never copies a live PGDATA directory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from enum import Enum
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.integration_config import get_integration_config_store
from app.db import session as db_session
from app.models.domain import (
    CustomFormat,
    DownloadClient,
    Episode,
    EpisodeMediaMap,
    Indexer,
    Job,
    JobStatus,
    MediaDirectory,
    MediaFile,
    MediaProfileOverride,
    Movie,
    MovieRelease,
    MovieReleaseTorrent,
    MovieTag,
    PlexConfiguration,
    PlexObservation,
    Problem,
    ProfileCustomFormatScore,
    QualityProfile,
    RemotePathMapping,
    Schedule,
    Season,
    Show,
    ShowRelease,
    ShowReleaseTorrent,
    StorageRoot,
    Tag,
    TMDBConfiguration,
    Torrent,
    TorrentClientObservation,
    ParseEvidence,
)
from app.services.events import create_event
from app.services.jobs import update_job
from app.services.torrent_archive import MANIFEST_SCHEMA_VERSION


RECOVERY_SCHEMA_VERSION = 1
_LIBRARY_INVENTORY_SCHEMA_VERSION = 1
_CONFIGURATION_EXPORT_SCHEMA_VERSION = 1
_PROGRESS_RE = re.compile(r"\((\d{1,3})%\)")
_VERSION_RE = re.compile(r"(?:PostgreSQL\)?\s+)(\d+)(?:\.\d+)*", re.IGNORECASE)

BaseBackupRunner = Callable[[Path, Settings, Callable[[int, str], Awaitable[None]] | None], Awaitable[dict[str, Any]]]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return str(value)


def _model_dict(row: Any) -> dict[str, Any]:
    return {column.name: _json_value(getattr(row, column.name)) for column in row.__table__.columns}


async def _model_rows(db: AsyncSession, model: type) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(model))).all()
    return [_model_dict(row) for row in rows]


def application_version() -> str:
    try:
        return importlib_metadata.version("medialogue-backend")
    except importlib_metadata.PackageNotFoundError:
        return "0.1.0"


def recovery_export_root(settings: Settings | None = None) -> Path:
    return Path((settings or get_settings()).recovery_export_dir)


def recovery_bundle_path(job_id: UUID, settings: Settings | None = None) -> Path:
    return recovery_export_root(settings) / f"medialogue-recovery-{job_id}.zip"


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def cleanup_expired_recovery_exports(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    root = recovery_export_root(settings)
    if not root.is_dir():
        return
    cutoff = utcnow() - timedelta(hours=max(1, settings.recovery_export_retention_hours))
    for path in root.glob("medialogue-recovery-*.zip"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue
    for path in root.glob(".recovery-tmp-*"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _pg_connection_args(settings: Settings) -> tuple[list[str], dict[str, str]]:
    url = make_url(settings.database_url)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("Recovery physical backups require PostgreSQL.")
    args: list[str] = []
    if url.host:
        args.extend(["--host", url.host])
    if url.port:
        args.extend(["--port", str(url.port)])
    if url.username:
        args.extend(["--username", url.username])
    if url.database:
        args.extend(["--dbname", url.database])
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    # Allow common libpq connection controls from SQLAlchemy query arguments
    # without placing credentials in the process list.
    for key, env_name in {
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
    }.items():
        value = url.query.get(key)
        if value:
            env[env_name] = str(value)
    return args, env


async def _tool_version(binary: str) -> tuple[str | None, int | None]:
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
    except (FileNotFoundError, PermissionError, OSError):
        return None, None
    value = stdout.decode(errors="replace").strip()
    match = _VERSION_RE.search(value)
    return value or None, int(match.group(1)) if match else None


async def database_backup_metadata(db: AsyncSession) -> dict[str, Any]:
    bind = db.get_bind()
    backend = bind.dialect.name if bind is not None else "unknown"
    if backend != "postgresql":
        return {
            "backend": backend,
            "server_version": None,
            "server_version_num": None,
            "server_major": None,
            "migration_revision": None,
            "custom_tablespaces": [],
        }
    server_version = str(await db.scalar(text("SHOW server_version")) or "")
    server_version_num = str(await db.scalar(text("SHOW server_version_num")) or "")
    server_major = int(server_version_num) // 10000 if server_version_num.isdigit() else None
    migration_revision = await db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    tablespaces = (
        await db.execute(
            text(
                "SELECT spcname, pg_tablespace_location(oid) AS location "
                "FROM pg_tablespace "
                "WHERE spcname NOT IN ('pg_default', 'pg_global') "
                "AND pg_tablespace_location(oid) <> ''"
            )
        )
    ).mappings().all()
    return {
        "backend": backend,
        "server_version": server_version,
        "server_version_num": server_version_num,
        "server_major": server_major,
        "migration_revision": str(migration_revision) if migration_revision else None,
        "custom_tablespaces": [dict(row) for row in tablespaces],
    }


async def recovery_capabilities(db: AsyncSession, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    db_meta = await database_backup_metadata(db)
    tool_version, tool_major = await _tool_version(settings.pg_basebackup_bin)
    archive = Path(settings.torrent_archive_dir)
    output = recovery_export_root(settings)
    try:
        _ensure_private_directory(output)
        output_writable = os.access(output, os.W_OK)
    except OSError:
        output_writable = False
    archive_readable = archive.is_dir() and os.access(archive, os.R_OK)
    compatible = bool(
        db_meta["backend"] == "postgresql"
        and tool_major is not None
        and db_meta.get("server_major") == tool_major
        and not db_meta.get("custom_tablespaces")
        and output_writable
    )
    reasons: list[str] = []
    if db_meta["backend"] != "postgresql":
        reasons.append("The configured database is not PostgreSQL.")
    if tool_major is None:
        reasons.append("pg_basebackup is not installed or executable.")
    elif db_meta.get("server_major") and db_meta["server_major"] != tool_major:
        reasons.append(
            f"pg_basebackup major {tool_major} does not match PostgreSQL server major {db_meta['server_major']}."
        )
    if db_meta.get("custom_tablespaces"):
        reasons.append("Custom PostgreSQL tablespaces are not supported by the bundled exporter yet.")
    if not output_writable:
        reasons.append("The recovery export directory is not writable.")
    if not archive_readable:
        reasons.append("The torrent archive mount is unavailable; the database can be backed up but the bundle would be incomplete.")
    return {
        "supported": compatible and archive_readable,
        "database_backend": db_meta["backend"],
        "postgres_server_version": db_meta.get("server_version"),
        "postgres_server_major": db_meta.get("server_major"),
        "pg_basebackup_available": tool_major is not None,
        "pg_basebackup_version": tool_version,
        "pg_basebackup_major": tool_major,
        "migration_revision": db_meta.get("migration_revision"),
        "custom_tablespaces": db_meta.get("custom_tablespaces") or [],
        "torrent_archive_readable": archive_readable,
        "export_directory_writable": output_writable,
        "export_directory": str(output),
        "retention_hours": settings.recovery_export_retention_hours,
        "reasons": reasons,
    }


async def run_pg_basebackup(
    destination: Path,
    settings: Settings,
    progress: Callable[[int, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Produce a consistent physical PostgreSQL base backup.

    Passwords are passed through ``PGPASSWORD`` rather than command-line
    arguments. The official Docker image supplies a pg_basebackup matching the
    PostgreSQL major version used by the bundled Compose deployment.
    """

    args, env = _pg_connection_args(settings)
    destination.mkdir(parents=True, exist_ok=False)
    command = [
        settings.pg_basebackup_bin,
        *args,
        "--pgdata",
        str(destination),
        "--format=plain",
        "--wal-method=stream",
        "--checkpoint=fast",
        "--progress",
        "--verbose",
        "--no-password",
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    tail: list[str] = []
    assert process.stderr is not None
    while True:
        raw = await process.stderr.readline()
        if not raw:
            break
        line = raw.decode(errors="replace").strip()
        if line:
            tail.append(line)
            tail = tail[-20:]
            match = _PROGRESS_RE.search(line)
            if match and progress:
                await progress(int(match.group(1)), line)
    return_code = await process.wait()
    if return_code != 0:
        shutil.rmtree(destination, ignore_errors=True)
        detail = "\n".join(tail[-8:]) or f"pg_basebackup exited with status {return_code}."
        raise RuntimeError(detail)
    return {"tool": "pg_basebackup", "format": "plain", "wal_method": "stream"}


async def build_configuration_export(db: AsyncSession, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "schema_version": _CONFIGURATION_EXPORT_SCHEMA_VERSION,
        "generated_at": utcnow().isoformat(),
        "contains_sensitive_values": True,
        "sensitivity_note": (
            "Integration credentials are included so this export can assist disaster recovery. "
            "The Recovery Bundle must be stored as sensitive material."
        ),
        "runtime": {
            "app_name": settings.app_name,
            "environment": settings.environment,
            "config_dir": settings.config_dir,
            "torrent_archive_dir": settings.torrent_archive_dir,
            "recovery_export_dir": settings.recovery_export_dir,
            "session_ttl_hours": settings.session_ttl_hours,
            "cookie_secure": settings.cookie_secure,
            "cookie_samesite": settings.cookie_samesite,
            "api_prefix": settings.api_prefix,
            "log_level": settings.log_level,
        },
        "runtime_sensitive": {
            "database_url": settings.database_url,
            "secret_key": settings.secret_key,
            "note": "These values are intentionally included for disaster recovery and must be protected.",
        },
        "storage_roots": await _model_rows(db, StorageRoot),
        "remote_path_mappings": await _model_rows(db, RemotePathMapping),
        # Integration settings and credentials are file-backed. This recovery
        # export is already explicitly sensitive, so include a decrypted
        # logical snapshot in addition to copying the original /config files.
        "integration_configuration": get_integration_config_store().export_for_recovery(),
        "integration_runtime_state": {
            "download_clients": await _model_rows(db, DownloadClient),
            "indexers": await _model_rows(db, Indexer),
            "plex": await _model_rows(db, PlexConfiguration),
            "tmdb": await _model_rows(db, TMDBConfiguration),
        },
        "schedules": await _model_rows(db, Schedule),
        "custom_formats": await _model_rows(db, CustomFormat),
        "quality_profiles": await _model_rows(db, QualityProfile),
        "profile_custom_format_scores": await _model_rows(db, ProfileCustomFormatScore),
        "media_profile_overrides": await _model_rows(db, MediaProfileOverride),
        "tags": await _model_rows(db, Tag),
    }


async def build_library_inventory(db: AsyncSession) -> dict[str, Any]:
    sections: dict[str, list[dict[str, Any]]] = {
        "movies": await _model_rows(db, Movie),
        "movie_releases": await _model_rows(db, MovieRelease),
        "movie_tags": await _model_rows(db, MovieTag),
        "shows": await _model_rows(db, Show),
        "seasons": await _model_rows(db, Season),
        "episodes": await _model_rows(db, Episode),
        "show_releases": await _model_rows(db, ShowRelease),
        "media_directories": await _model_rows(db, MediaDirectory),
        "media_files": await _model_rows(db, MediaFile),
        "episode_media_mappings": await _model_rows(db, EpisodeMediaMap),
        "torrents": await _model_rows(db, Torrent),
        "torrent_client_observations": await _model_rows(db, TorrentClientObservation),
        "movie_release_torrents": await _model_rows(db, MovieReleaseTorrent),
        "show_release_torrents": await _model_rows(db, ShowReleaseTorrent),
        "plex_observations": await _model_rows(db, PlexObservation),
        "parse_evidence": await _model_rows(db, ParseEvidence),
        "open_and_historical_problems": await _model_rows(db, Problem),
    }
    return {
        "schema_version": _LIBRARY_INVENTORY_SCHEMA_VERSION,
        "generated_at": utcnow().isoformat(),
        "counts": {name: len(rows) for name, rows in sections.items()},
        **sections,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe_archive_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        try:
            mode = path.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISLNK(mode):
            continue
        if stat.S_ISREG(mode):
            files.append(path)
    return sorted(files)


def _torrent_arcname(relative: Path) -> str:
    parts = relative.parts
    if parts and parts[0] == "manifests":
        return str(Path("manifests", *parts[1:]))
    if parts and parts[0] == "torrents":
        return str(Path("torrent-archive", *parts))
    return str(Path("torrent-archive", "misc", relative))


def _zip_tree(bundle: zipfile.ZipFile, root: Path, prefix: str) -> int:
    count = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        bundle.write(path, str(Path(prefix) / path.relative_to(root)))
        count += 1
    return count


def _archive_inventory(root: Path, files: list[Path]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": utcnow().isoformat(),
        "archive_root": str(root),
        "files": [
            {
                "source_relative_path": str(path.relative_to(root)),
                "bundle_path": _torrent_arcname(path.relative_to(root)),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in files
        ],
    }


def _build_bundle_zip(temp_zip: Path, temp_root: Path, torrent_root: Path, archive_files: list[Path]) -> None:
    with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        bundle.write(temp_root / "backup-metadata.json", "backup-metadata.json")
        _zip_tree(bundle, temp_root / "database", "database")
        _zip_tree(bundle, temp_root / "config", "config")
        _zip_tree(bundle, temp_root / "inventory", "inventory")
        for path in archive_files:
            bundle.write(path, _torrent_arcname(path.relative_to(torrent_root)))


async def _job_progress(job_id: UUID, percent: int, stage: str, detail: str | None = None) -> None:
    async with db_session.async_session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None or job.status in {JobStatus.CANCELLED, JobStatus.INTERRUPTED}:
            return
        await update_job(
            db,
            job,
            progress={"percent": max(0, min(100, int(percent))), "stage": stage, "detail": detail},
        )
        await db.commit()


async def run_recovery_export(
    job_id: UUID,
    *,
    base_backup_runner: BaseBackupRunner = run_pg_basebackup,
    settings: Settings | None = None,
    database_metadata_override: dict[str, Any] | None = None,
) -> None:
    """Build a complete Recovery Bundle for a persisted Job."""

    settings = settings or get_settings()
    output_root = recovery_export_root(settings)
    _ensure_private_directory(output_root)
    cleanup_expired_recovery_exports(settings)
    final_path = recovery_bundle_path(job_id, settings)
    final_path.unlink(missing_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".recovery-tmp-{job_id}-", dir=output_root))
    try:
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is None or job.status == JobStatus.CANCELLED:
                return
            await update_job(db, job, status=JobStatus.RUNNING, progress={"percent": 2, "stage": "preparing"})
            await db.commit()

        async with db_session.async_session_factory() as db:
            db_meta = database_metadata_override or await database_backup_metadata(db)
            if db_meta.get("backend") != "postgresql" and database_metadata_override is None:
                raise RuntimeError("Recovery physical backups require PostgreSQL.")
            if db_meta.get("custom_tablespaces"):
                raise RuntimeError(
                    "Custom PostgreSQL tablespaces are configured. Medialogue refuses to run a plain physical base backup "
                    "until explicit tablespace mappings are supported."
                )
            tool_version, tool_major = await _tool_version(settings.pg_basebackup_bin)
            server_major = db_meta.get("server_major")
            # An injected runner is used by unit tests; production always checks
            # the bundled pg_basebackup/server-major compatibility.
            if base_backup_runner is run_pg_basebackup:
                if tool_major is None:
                    raise RuntimeError("pg_basebackup is unavailable in the Medialogue container.")
                if server_major is not None and tool_major != server_major:
                    raise RuntimeError(
                        f"pg_basebackup major {tool_major} does not match PostgreSQL server major {server_major}."
                    )
            configuration = await build_configuration_export(db, settings)
            inventory = await build_library_inventory(db)

        await _job_progress(job_id, 8, "database_backup", "Starting PostgreSQL physical base backup")

        async def base_progress(value: int, detail: str) -> None:
            mapped = 8 + round(max(0, min(100, value)) * 0.47)
            await _job_progress(job_id, mapped, "database_backup", detail)

        physical_dir = temp_root / "database" / "physical-base-backup"
        base_backup_result = await base_backup_runner(physical_dir, settings, base_progress)
        await _job_progress(job_id, 58, "inventory", "Writing configuration and library inventory")

        _write_json(temp_root / "config" / "application-config-export.json", configuration)
        config_store = get_integration_config_store()
        for source in (
            config_store.config_path,
            config_store.secrets_path,
            Path(settings.config_dir) / "setup-state.json",
            Path(settings.config_dir) / "custom-format-layout.json",
        ):
            if source.is_file():
                destination = temp_root / "config" / "live" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        _write_json(temp_root / "inventory" / "library-inventory.json", inventory)

        torrent_root = Path(settings.torrent_archive_dir)
        archive_files = _safe_archive_files(torrent_root)
        archive_inventory = await asyncio.to_thread(_archive_inventory, torrent_root, archive_files)
        _write_json(temp_root / "inventory" / "torrent-archive-inventory.json", archive_inventory)

        backup_timestamp = utcnow()
        expires_at = backup_timestamp + timedelta(hours=max(1, settings.recovery_export_retention_hours))
        metadata = {
            "recovery_schema_version": RECOVERY_SCHEMA_VERSION,
            "backup_timestamp": backup_timestamp.isoformat(),
            "application_version": application_version(),
            "application_package": "medialogue-backend",
            "postgresql": {
                "server_version": db_meta.get("server_version"),
                "server_version_num": db_meta.get("server_version_num"),
                "major_version": db_meta.get("server_major"),
                "base_backup": _json_value(base_backup_result),
                "pg_basebackup_version": tool_version,
            },
            "schema_migration_revision": db_meta.get("migration_revision"),
            "torrent_manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "configuration_export_schema_version": _CONFIGURATION_EXPORT_SCHEMA_VERSION,
            "library_inventory_schema_version": _LIBRARY_INVENTORY_SCHEMA_VERSION,
            "sensitive": True,
            "sensitive_note": (
                "This bundle contains a physical database backup and integration credentials. "
                "Treat it like a password/database backup."
            ),
            "download_expires_at": expires_at.isoformat(),
        }
        _write_json(temp_root / "backup-metadata.json", metadata)

        await _job_progress(job_id, 72, "packaging", "Packaging database and torrent recovery evidence")
        temp_zip = output_root / f".{final_path.name}.tmp"
        temp_zip.unlink(missing_ok=True)
        await asyncio.to_thread(_build_bundle_zip, temp_zip, temp_root, torrent_root, archive_files)
        os.replace(temp_zip, final_path)
        try:
            final_path.chmod(0o600)
        except OSError:
            pass
        bundle_sha256 = await asyncio.to_thread(_sha256, final_path)
        bundle_size = final_path.stat().st_size

        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is None:
                return
            summary = dict(job.summary or {})
            summary.update(
                {
                    "message": "Recovery Bundle is ready for download.",
                    "download_ready": True,
                    "download_url": f"/api/v1/recovery/exports/{job_id}/download",
                    "bundle_filename": final_path.name,
                    "bundle_path": str(final_path),
                    "bundle_size": bundle_size,
                    "bundle_sha256": bundle_sha256,
                    "expires_at": expires_at.isoformat(),
                    "postgres_major_version": db_meta.get("server_major"),
                    "migration_revision": db_meta.get("migration_revision"),
                    "torrent_archive_files": len(archive_files),
                }
            )
            await update_job(db, job, status=JobStatus.COMPLETED, progress={"percent": 100, "stage": "completed"}, summary=summary)
            await create_event(
                db,
                "recovery.export_completed",
                entity_type="job",
                entity_id=job.id,
                message="Recovery Bundle export completed.",
                details={
                    "bundle_filename": final_path.name,
                    "bundle_size": bundle_size,
                    "bundle_sha256": bundle_sha256,
                    "expires_at": expires_at.isoformat(),
                    "torrent_archive_files": len(archive_files),
                },
            )
            await db.commit()
    except Exception as exc:
        final_path.unlink(missing_ok=True)
        async with db_session.async_session_factory() as db:
            job = await db.get(Job, job_id)
            if job is not None and job.status not in {JobStatus.CANCELLED, JobStatus.INTERRUPTED}:
                await update_job(
                    db,
                    job,
                    status=JobStatus.FAILED,
                    error={"code": "RECOVERY_EXPORT_FAILED", "message": str(exc)},
                )
                await create_event(
                    db,
                    "recovery.export_failed",
                    entity_type="job",
                    entity_id=job.id,
                    message="Recovery Bundle export failed.",
                    details={"error": str(exc)},
                )
                await db.commit()
    finally:
        (output_root / f".{final_path.name}.tmp").unlink(missing_ok=True)
        shutil.rmtree(temp_root, ignore_errors=True)
