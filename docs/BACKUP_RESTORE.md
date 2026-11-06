# Backup and Restore

Medialogue's backup exists for one situation: the server dies and the media on it is lost. Medialogue keeps every torrent your library came from, copies it to Google Drive, and can re-add all of them to qBittorrent on a new server so everything downloads again into the same folders.

Everything lives on the **Backups** page, which has three tabs: **Torrents**, **Google Drive** and **Restore**.

## What is kept

### The torrent archive

`/torrent-archive` holds, for every relevant torrent qBittorrent reports:

- `torrents/<shard>/<info hash>.torrent`, the torrent file itself;
- `manifests/<shard>/<info hash>.json`, the manifest: which movie or show it belongs to, the save path, category and tags it had in each qBittorrent client, which files were selected, and the client's per-torrent settings.

Archiving happens automatically as qBittorrent is polled. The archive holds what is on the server, so a restore brings back exactly that:

- A torrent is **kept** while a release in the library still uses it, or any qBittorrent client still lists it. Every cross-seed of a release is kept, so a restore can still download it if one tracker drops the torrent. A movie or show removed from Medialogue keeps its torrents while qBittorrent still has them.
- A torrent is **removed** from the archive once every qBittorrent client that had it has confirmed removing it and no release you still have uses it. That happens after an upgrade or replacement cleans up the old release, or after you remove something from both Medialogue and qBittorrent. The daily **Housekeeping** task does this, and Event History records each one.
- A client being offline never counts as a removal, and neither do missing files: a dead disk never costs a torrent its backup.

Do not treat this directory as a disposable cache.

### The Google Drive copy

When Google Drive is connected and backups are turned on, Medialogue keeps a **Medialogue Backups** folder in your Drive:

```text
Medialogue Backups/
├── torrents/   <info hash>.torrent
├── manifests/  <info hash>.json
└── database/   medialogue-database-<time>.zip
```

- **Torrents and manifests** are copied every 10 minutes (configurable in **Tasks**). Only new or changed files are uploaded, and each upload is checked against the checksum Drive reports. A manifest is uploaded again only when something a restore uses changes (paths, file selection, settings), not when live statistics are refreshed.
- **Database copies** hold the library database (a consistent SQLite online backup) and the configuration: `medialogue.json`, `secrets.enc`, `setup-state.json`, `custom-format-layout.json` and `application-secret`. They are made on their own schedule (daily by default, set on the **Google Drive** tab) and the newest few are kept (7 by default). Older copies are moved to Drive's trash only after a new one is confirmed.

A torrent removed from the archive stays in Drive for 30 days, then the next backup deletes it from Drive too, in case an upgrade or removal needs undoing. Nothing else is ever deleted from Drive. If the Drive folder itself is deleted, the next run recreates it and uploads everything again.

> **Nothing in Drive is encrypted.** Torrent files contain your tracker passkeys, and the database copies contain the application secret together with `secrets.enc`, so they reveal the qBittorrent, indexer, Plex and TMDB credentials. Anyone who can open the folder can use them. Keep the folder private.

## Setting up Google Drive

1. In Google Cloud, enable the Google Drive API and create an OAuth client of type **Web application**.
2. Set `MEDIALOGUE_GOOGLE_DRIVE_CLIENT_ID`, `MEDIALOGUE_GOOGLE_DRIVE_CLIENT_SECRET` and `MEDIALOGUE_GOOGLE_DRIVE_REDIRECT_URI` in the deployment environment. The redirect URI is Medialogue's address followed by `/api/v1/backups/google/callback`, and must match the OAuth client's authorized redirect URI exactly. Use HTTPS, or localhost for local development. Both Compose files pass these variables through.
3. Restart Medialogue, open **Backups → Google Drive → Connect Google Drive**, and allow access. Medialogue asks only for the `drive.file` scope, which covers the files it creates.
4. Select **Turn on backups**. The first copy starts within a minute.

The Google account token is stored encrypted in `/config/backup-settings.enc`. **Disconnect** stops backups and forgets the token; nothing already in Drive is deleted. If Google stops accepting the token (for example, an OAuth app left in testing mode expires its grants), the Google Drive tab shows the error and **Reconnect** fixes it.

## Checking what is protected

The **Torrents** tab lists every archived torrent and every release in the library that has none. The header shows how many library releases can be restored, the archive counts, and the archive folder; it turns red if the folder is unavailable.

The **Backup** column shows, for each row:

- **On Drive**: archived and copied to Google Drive;
- **Only here**: archived on this server, waiting for the next copy;
- **Not archived**: the torrent file could not be saved yet. Use the retry button on the row while the torrent is still in qBittorrent;
- **No torrent**: a release in the library that Medialogue never saw a torrent for, for example files added by hand. These cannot be downloaded again.

The **Not restorable** filter shows everything that is not yet safely on Drive.

## Restoring after losing the server

1. **Install Medialogue** on the new server and complete first-run setup.
2. **Add the media folders and qBittorrent clients again**, with the same container paths as before. If the paths qBittorrent reports differ from Medialogue's, add the remote path mappings too.
3. **In Google Drive, download the Medialogue Backups folder.** Drive delivers a folder as a ZIP, split into several ZIPs when it is large.
4. **Open Backups → Restore → Upload backup ZIP** and choose the ZIP, or all of its parts at once. Medialogue takes the `.torrent` files and manifests from it and lists them on the Torrents tab. Database copies and anything else in the ZIP are ignored. Uploading the same ZIP again changes nothing.
5. **Check what can be re-added.** The plan opens by itself after an upload. Each torrent is one of:
   - **Ready**: it can go back to its original client and save path;
   - **Already in qBittorrent**: it is skipped;
   - **Blocked**: something is missing, with the reason shown. For example, its save path is not inside an enabled media folder, or no matching qBittorrent client exists. Fix the setup and check again.
6. **Select the ready torrents and re-add them.** Each one goes back with its original save path, category, tags and file selection (files that were not downloaded stay unselected), and qBittorrent downloads it again.

A fresh install usually has new qBittorrent client IDs. Each torrent goes to its original client when that still exists. Otherwise it goes to a client with the same name and media type, or, when there is exactly one client for that media type, to that one.

Re-adding is never automatic. The plan is read-only, expires after 15 minutes, and is checked again before anything is sent. Torrents are added stopped, given their file selection and settings, then started (unless they were paused before). Queue position, transfer totals, renamed files and tracker edits are not restored. Medialogue never moves or deletes media during a restore.

### Uploads behind a reverse proxy

A backup ZIP can be large. Allow large request bodies on `/api/v1/backups/restore/upload`: nginx's default `client_max_body_size` is 1 MB, and Cloudflare's proxy caps uploads at 100 MB, so upload from the local network if needed. The upload is staged in `/config/uploads` while it is read, and removed afterwards.

### Restoring settings and the library database

Restoring settings is manual. Unzip the newest `database/medialogue-database-<time>.zip` from the downloaded folder, then, with Medialogue stopped:

1. copy `database/medialogue.db` to `/config/medialogue.db`;
2. copy the files under `config/` to `/config`, including `application-secret`, which unlocks `secrets.enc`;
3. make the files owned by the configured `PUID`/`PGID`, and start Medialogue.

This brings back qBittorrent clients, indexers, media folders, quality profiles, custom formats and the library, so step 2 of the restore above is already done. Restore a database copy only into the same Medialogue version that made it, or a newer one, which migrates it on start.

## Upgrading from the encrypted backups

Earlier versions made encrypted `.mlbackup` snapshots with a recovery key, and a Recovery Bundle ZIP export. Both have been replaced by the plain Drive copy above. The first backup run after upgrading creates the new folder layout alongside any old snapshots. Old `.mlbackup` files can still be opened with `backend/scripts/restore_backup.py` from an earlier release, together with their recovery key.
