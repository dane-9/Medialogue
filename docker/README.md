# Container packaging

The production Dockerfile builds the React frontend and FastAPI backend into one Medialogue application image with a SQLite database under `/config`.

## Startup sequence

1. The container starts as root only long enough to prepare `/config` and `/torrent-archive` and apply `PUID`/`PGID`.
2. It drops privileges to `appuser`.
3. `alembic upgrade head` applies pending schema migrations.
4. Uvicorn starts only if migrations succeed.
5. Docker health uses `/readyz`, which verifies SQLite connectivity.

Media mounts are **never** chowned or reorganized by the entrypoint.

## Mounts

- `/config`: writable application-owned configuration, SQLite database, and export area. Use local storage rather than SMB/NFS.
- `/torrent-archive`: writable durable torrent recovery archive.
- `/media/...`: only explicit media binds chosen by the administrator. Prefer read-only. Use read/write only when GUI deletion is desired and the configured runtime identity has host filesystem permission.

## Recovery export tooling

Recovery Bundles use SQLite's online backup API and validate the copied database before packaging. No external database server or backup binary is required.

See `docs/INSTALLATION.md`, `docs/UPGRADE.md`, and `docs/BACKUP_RESTORE.md` for operational procedures.
