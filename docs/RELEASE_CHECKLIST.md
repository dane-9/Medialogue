# Release Checklist

This checklist is for validating a Medialogue source release before deploying it on a home server.

## Source and configuration

- Extract the complete release tree; do not copy only `docker-compose.yml`.
- Copy `.env.example` to `.env`.
- Replace the example PostgreSQL password and Medialogue secret key.
- Review `PUID`, `PGID`, timezone, persistent paths, and media bind mounts.
- Keep media mounts read-only; duplicate cleanup is performed outside Medialogue.

## Preflight

When development dependencies are installed, run:

```sh
./scripts/release-check.sh
```

The repository CI additionally validates PostgreSQL 16 migrations, the frontend production build, and a clean Docker Compose boot.

## First boot

```sh
docker compose config -q
docker compose up -d --build
docker compose ps
curl -fsS http://HOST:8000/readyz
```

The application entrypoint applies Alembic migrations before Uvicorn starts. A failed migration therefore prevents the application from becoming ready.

Sign in with the initial `admin` / `adminadmin` credentials and use the first-run checklist. The default-password warning remains visible until the password is changed.

## Safety checks

Before using write-capable actions:

- confirm every Storage Root points at the intended container-visible path;
- confirm each root is marked Movies or Shows correctly;
- confirm media mounts remain read-only for ordinary Medialogue operation;
- test Plex, qBittorrent, metadata, and indexer connections as applicable;
- confirm remote path mappings when qBittorrent reports paths different from container paths;
- run the first full library scan explicitly.

There is no global operations toggle. New storage roots remain inert until their first explicit successful scan.

## Backup before upgrade

Create a Recovery Bundle before upgrading an existing installation. Keep it outside the Medialogue host and treat it as sensitive because it contains database contents and integration credentials.

See [Upgrade](UPGRADE.md) and [Backup / Restore](BACKUP_RESTORE.md).
