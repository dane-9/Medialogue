# Backup and Restore

Medialogue has two independent recovery layers.

## Torrent archive

`/torrent-archive` retains archived `.torrent` files and manifests even when a torrent is removed from qBittorrent or a logical Movie/Show record is removed from the app.

Do not treat this directory as disposable cache.

## Recovery Bundle

Settings → Backup / Recovery creates a ZIP containing:

- a PostgreSQL physical base backup produced with `pg_basebackup`;
- torrent archive data and recovery manifests;
- a configuration export;
- a readable library inventory;
- compatibility/version metadata.

The bundle contains credentials and database contents. Store it as sensitive backup material.

## Restore overview

1. Stop Medialogue and PostgreSQL.
2. Read `backup-metadata.json` and provision the recorded PostgreSQL major version.
3. Restore the physical base backup into an empty PostgreSQL data volume.
4. Restore `/torrent-archive` contents.
5. Recreate deployment secrets/paths from the configuration export, adapting host-specific paths where necessary.
6. Recreate media mounts and remote path mappings for the new host.
7. Start PostgreSQL, then Medialogue. Startup migrations may advance an older restored schema.
8. Verify storage/integration health; newly added roots still require one explicit initialization scan.

Medialogue deliberately does not offer a one-click in-place database restore in the web UI.
