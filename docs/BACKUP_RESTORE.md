# Backup and Restore

Medialogue has three persistent recovery layers.

## File-backed integration configuration

`/config/medialogue.json` is the source of truth for Plex, TMDB, qBittorrent-client, and indexer settings. Passwords, tokens, and API keys are stored separately in `/config/secrets.enc`, encrypted with AES-GCM using a key derived from `MEDIALOGUE_SECRET_KEY`.

Back up both files together and retain the matching `MEDIALOGUE_SECRET_KEY`. A copied `secrets.enc` file is intentionally unusable with a different secret key.

## Torrent archive

`/torrent-archive` retains archived `.torrent` files and manifests even when a torrent is removed from qBittorrent or a logical Movie/Show record is removed from the app.

Do not treat this directory as disposable cache.

## Recovery Bundle

Settings → Backup / Recovery creates a ZIP containing:

- a PostgreSQL physical base backup produced with `pg_basebackup`;
- `config/live/medialogue.json` and `config/live/secrets.enc`;
- torrent archive data and recovery manifests;
- a sensitive human-readable configuration export for disaster recovery;
- a readable library inventory;
- compatibility/version metadata.

The bundle contains credentials and database contents. Store it as sensitive backup material.

## Restore overview

1. Stop Medialogue and PostgreSQL.
2. Read `backup-metadata.json` and provision the recorded PostgreSQL major version.
3. Restore the physical base backup into an empty PostgreSQL data volume. During the current clean-baseline development phase, restore only a database produced by the same compatible Medialogue schema; old database schemas are not converted in place.
4. Restore `config/live/medialogue.json` and `config/live/secrets.enc` to `/config` and configure the same `MEDIALOGUE_SECRET_KEY`.
5. Restore `/torrent-archive` contents.
6. Recreate deployment paths/media mounts and remote path mappings for the new host.
7. Start PostgreSQL, then Medialogue.
8. Verify storage/integration health; newly added roots still require one explicit initialization scan.

Medialogue deliberately does not offer a one-click in-place database restore in the web UI.
