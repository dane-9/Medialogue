# Release Checklist

This checklist is for validating a Medialogue source release before deploying it on a home server.

## Source and configuration

- Extract the complete release tree; do not copy only `docker-compose.yml`.
- Copy `.env.example` to `.env`.
- Leave `MEDIALOGUE_SECRET_KEY` unset to let Medialogue generate and persist `/config/application-secret`, or set it to a long random value and retain it with the config backup.
- Review `PUID`, `PGID`, timezone, persistent paths, and media bind mounts.
- Keep media mounts read-only; duplicate cleanup is performed outside Medialogue.

## Preflight

When development dependencies are installed, run:

```sh
./scripts/release-check.sh
```

The repository CI additionally validates SQLite migrations and backups, the frontend production build, and a clean Docker Compose boot.

## First boot

```sh
docker compose config -q
docker compose up -d --build
docker compose ps
curl -fsS http://HOST:8000/readyz
```

The application entrypoint applies Alembic migrations before Uvicorn starts. A failed migration therefore prevents the application from becoming ready.

Read the one-time setup token and complete the First Run screen:

```sh
docker compose exec media-manager cat /config/initial-setup-token
```

Choose an administrator password of at least 12 characters. The token is deleted after setup.

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
