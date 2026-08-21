# Upgrade

The current Medialogue development line uses a clean PostgreSQL schema baseline. It does **not** import or convert Plex, TMDB, qBittorrent, or indexer configuration from an older database. Those settings live under `/config`.

## Recommended procedure for this build

1. Stop Medialogue.
2. Keep/back up `/config/medialogue.json`, `/config/secrets.enc`, `/torrent-archive`, and `MEDIALOGUE_SECRET_KEY` if you want to retain integration configuration and torrent recovery data.
3. Because this build is intended for a fresh database, remove/recreate the Medialogue PostgreSQL data volume rather than attempting an in-place schema conversion from an older build.
4. Pull/rebuild and start the target image. The entrypoint runs `alembic upgrade head` only to create/validate the current fresh schema baseline; there is no legacy integration-config migration.
5. Sign in and verify Integration Health. If `/config/medialogue.json` and `/config/secrets.enc` were not retained, configure integrations again through the UI.
6. Add/verify Storage Roots. Every newly added root remains inactive until one explicit successful scan initializes it.

## PostgreSQL major version

The supplied stack uses PostgreSQL 16. Recovery Bundles use a physical PostgreSQL base backup, so restore a bundle with the same PostgreSQL major version recorded in `backup-metadata.json`.

## `MEDIALOGUE_SECRET_KEY`

Do not change `MEDIALOGUE_SECRET_KEY` while reusing `/config/secrets.enc`. The encrypted secrets file is intentionally bound to that key; Medialogue will fail clearly instead of silently discarding credentials.
