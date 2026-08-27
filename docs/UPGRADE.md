# Upgrade

The current Medialogue development line uses a clean SQLite schema baseline. It does **not** import PostgreSQL data or convert Plex, TMDB, qBittorrent, or indexer configuration from an older database. Those settings live under `/config`.

## Recommended procedure for this build

1. Stop Medialogue.
2. Keep/back up `/config/medialogue.json`, `/config/secrets.enc`, `/torrent-archive`, and `MEDIALOGUE_SECRET_KEY` if you want to retain integration configuration and torrent recovery data.
3. Because this build is intended for a fresh SQLite database, do not attempt to point it at or import an older PostgreSQL database.
4. Pull/rebuild and start the target image. The entrypoint runs `alembic upgrade head` only to create/validate the current fresh schema baseline; there is no legacy integration-config migration.
5. Sign in and verify Integration Health. If `/config/medialogue.json` and `/config/secrets.enc` were not retained, configure integrations again through the UI.
6. Add/verify Storage Roots. Every newly added root remains inactive until one explicit successful scan initializes it.

## SQLite database

The database is `/config/medialogue.db`. Keep `/config` on a local filesystem and run only one Medialogue container against it.

## `MEDIALOGUE_SECRET_KEY`

Do not change `MEDIALOGUE_SECRET_KEY` while reusing `/config/secrets.enc`. The encrypted secrets file is intentionally bound to that key; Medialogue will fail clearly instead of silently discarding credentials.
