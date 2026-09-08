# Backup and Restore

Medialogue has three persistent recovery layers.

## File-backed integration configuration

`/config/medialogue.json` is the source of truth for Plex, TMDB, qBittorrent-client, and indexer settings. Passwords, tokens, and API keys are stored separately in `/config/secrets.enc`, encrypted with AES-GCM using a key derived from `MEDIALOGUE_SECRET_KEY`.

Back up both files together and retain the matching `MEDIALOGUE_SECRET_KEY`. If the key was generated, back up `/config/application-secret` with the config dataset. A copied `secrets.enc` file is intentionally unusable with a different secret key.

## Torrent archive

`/torrent-archive` retains archived `.torrent` files and manifests even when a torrent is removed from qBittorrent or a logical Movie/Show record is removed from the app. One `.torrent` is stored per info hash, with separate recovery settings and file selections for each observed client. Detail polling refreshes those selections. Legacy manifests remain readable.

Do not treat this directory as disposable cache.

The archive screen distinguishes **Verified locally**, **Included in last backup**, and the last observed **Tracker source** status. Remote coverage compares the saved recovery inputs with the last verified uploaded snapshot; it does not continuously check whether somebody deleted that snapshot in Drive. A writable mount is only **Storage available**.

Use **Details** to inspect client-specific settings and selected files. Filters identify incomplete local archives, removed torrents, and changes awaiting a remote backup. **Rebuild archive catalogue** imports verified manifests missing from the database without contacting qBittorrent or inventing live client presence. It restores torrent recovery records and historical media descriptions, not the complete Movie/Show database.

## Scheduled Google Drive backups

1. Enable the Google Drive API and create a Google OAuth **Web application** client.
2. Configure `MEDIALOGUE_GOOGLE_DRIVE_CLIENT_ID`, `MEDIALOGUE_GOOGLE_DRIVE_CLIENT_SECRET`, and `MEDIALOGUE_GOOGLE_DRIVE_REDIRECT_URI` in the deployment environment. The redirect URI must exactly match the OAuth client's authorized URI and end in `/api/v1/backups/google/callback`. Use HTTPS, or localhost for local development. Both supplied Compose files pass these variables through.
3. Restart Medialogue. Open **Settings → Backups → Connect Google Drive** and authorize access. Medialogue requests the limited `drive.file` scope and creates a visible **Medialogue Backups** folder.
4. Generate a recovery key, save it outside this server, acknowledge that it is saved, and save the backup settings. An existing recovery key can also be entered when rebuilding an installation. The UI does not show a saved key again.
5. Choose a schedule, save it, and select **Back up now**. The default retention is seven daily and four weekly restore points. Scheduling stays manual until explicitly enabled.

Snapshots are self-contained encrypted `.mlbackup` files. Encryption uses a random 256-bit recovery key and streaming AES-GCM, independently of `MEDIALOGUE_SECRET_KEY`. OAuth tokens and the working recovery key are encrypted locally in `/config/backup-settings.enc`; that file is intentionally excluded from exported snapshots. Restoring an installation requires reconnecting Google Drive. Keep using the same OAuth application to discover its previous app-owned snapshots.

The snapshot coordinates torrent observation commits with archive capture, uses SQLite's online backup API, generates inventories from the copied database, and packages staged archive files. Concurrent configuration changes invalidate capture rather than producing a mixed snapshot. Remote upload happens after local capture releases the observer. The plaintext intermediate is removed after encryption and is not available through the manual export download endpoint.

Uploads resume after transient connection failures and refresh expired access tokens. A snapshot counts as successful only after Google's stored SHA-256 and size match the encrypted local file. Retention then moves older verified snapshots owned by this installation to Drive trash; it always preserves the new successful snapshot. A failed upload or verification does not prune prior backups. Daily and weekly slots may refer to the same snapshot. Snapshots from another installation and unverified files are not automatically pruned.

**Browse restore points** lists snapshots accessible to the connected OAuth application and opens them in Drive for download. **Disconnect** stops scheduled backups and removes the local account token without deleting Drive files. Tasks exposes backup/search progress and schedule controls. Google authorization expiry or revoked access appears as an actionable reconnect error; testing-mode OAuth apps may require renewed authorization, so configure the OAuth application for ongoing unattended use.

Google integration references: [OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server), [Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth), and [resumable uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads).

## Decrypt and inspect a downloaded snapshot

From a checkout with the backend dependencies installed:

```text
python backend/scripts/restore_backup.py medialogue-snapshot.mlbackup restored
```

The tool prompts for the saved recovery key without echoing it. It verifies authentication, archive checksums, and SQLite integrity before publishing the staged directory. It rejects path traversal and existing destinations. For an unencrypted manual Recovery Bundle, pass its `.zip` filename instead; no recovery key is needed. Staging has a 100 GiB extracted-size limit.

The first-run setup screen also provides these recovery instructions. Staging never overwrites a running installation. Follow the restore overview below using the staged files; obtain the original application secret from `config/application-config-export.json` → `runtime_sensitive.secret_key`. This application secret unlocks the restored integration credentials and is distinct from the backup's recovery key.

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
5. Copy the staged `torrent-archive/torrents` directory to `/torrent-archive/torrents`, and the staged top-level `manifests` directory to `/torrent-archive/manifests`.
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

Torrents are added stopped, then file priorities and supported settings are applied before starting. Captured stopped/paused state is retained. Supported settings include automatic management, sequential/first-last options, speed limits, complete share limits, force-start, and super-seeding. Queue position, transfer totals, renamed file paths, tracker edits, and private resume data are not replayed. A setting/selection/start failure attempts to remove the new torrent entry without deleting files.

Medialogue does not create or move recovered media directories. The saved path is sent to qBittorrent only after it resolves through any configured qBittorrent remote-path mapping into the appropriate enabled Movies or Shows media folder. qBittorrent remains responsible for downloading and placement.

Bulk recovery is never automatic. Inspecting a plan is read-only, and a signed preview plus an explicit confirmation is required before any torrent is submitted.

Medialogue deliberately does not offer a one-click in-place database restore in the web UI.
