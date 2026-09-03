# Backup and Restore

Medialogue has three persistent recovery layers.

## File-backed integration configuration

`/config/medialogue.json` is the source of truth for Plex, TMDB, qBittorrent-client, and indexer settings. Passwords, tokens, and API keys are stored separately in `/config/secrets.enc`, encrypted with AES-GCM using a key derived from `MEDIALOGUE_SECRET_KEY`.

Back up both files together and retain the matching `MEDIALOGUE_SECRET_KEY`. If the key was generated, back up `/config/application-secret` with the config dataset. A copied `secrets.enc` file is intentionally unusable with a different secret key.

## Torrent archive

`/torrent-archive` retains archived `.torrent` files and manifests even when a torrent is removed from qBittorrent or a logical Movie/Show record is removed from the app.

Do not treat this directory as disposable cache.

## Recovery Bundle

Settings → Backup / Recovery creates a ZIP containing:

- a consistent `database/medialogue.db` created with SQLite's online backup API;
- `config/live/medialogue.json` and `config/live/secrets.enc`;
- torrent archive data and recovery manifests;
- a sensitive human-readable configuration export for disaster recovery;
- a readable library inventory;
- current recovery-format and application-version metadata.

The bundle contains credentials and database contents. Store it as sensitive backup material.

The export requires the current account password in the recovery form. The password is checked before any export job or capability is created, and it is not stored in browser state.

## Restore overview

1. Stop Medialogue.
2. Preserve the current `/config/medialogue.db` separately if it may still be needed.
3. Restore `database/medialogue.db` from the bundle to `/config/medialogue.db` with ownership matching the configured `PUID`/`PGID`.
4. Restore `config/live/medialogue.json` and `config/live/secrets.enc` to `/config` and configure the same `MEDIALOGUE_SECRET_KEY`.
5. Restore `/torrent-archive` contents.
6. Recreate deployment paths/media mounts and remote path mappings for the new host.
7. Start Medialogue.
8. Verify storage/integration health; newly added roots still require one explicit initialization scan.
9. Open **Settings → Backup / Recovery → Bulk torrent re-import** and choose **Inspect recovery plan**.
10. Review every row before selecting anything. Medialogue classifies archived torrents as:
   - **Ready** - archive bytes exist, the original qBittorrent client (or a unique name/scope replacement) is available, the saved qBittorrent path still resolves inside an enabled media folder, and the torrent is not already present;
   - **Already present** - the target qBittorrent client already reports that info hash, so Medialogue will not submit it again;
   - **Blocked** - required archive/client/path context is missing or unsafe. Correct the configuration and inspect again.
11. Select the Ready rows to recover and use **Review and submit**. The signed preview is short-lived and is revalidated before the job starts; if clients, paths, media folders, archive data, or qBittorrent contents changed, Medialogue requires a new preview.
12. Confirm the bulk submission. Each torrent is validated again immediately before it is sent to qBittorrent.

Bulk recovery restores the preserved qBittorrent save path, category, tags, applicable torrent settings, and captured per-file priorities. A file whose archived qBittorrent priority was `0` remains unselected. Per-file selection evidence is mandatory; an archive without a complete current-format priority map is blocked and cannot be submitted.

Medialogue does not create or move recovered media directories. The saved path is sent to qBittorrent only after it resolves through any configured qBittorrent remote-path mapping into the appropriate enabled Movies or Shows media folder. qBittorrent remains responsible for downloading and placement.

Bulk recovery is never automatic. Inspecting a plan is read-only, and a signed preview plus an explicit confirmation is required before any torrent is submitted.

Medialogue deliberately does not offer a one-click in-place database restore in the web UI.
