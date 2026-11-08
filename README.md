# Medialogue

A self-hosted manager for a movie and show library that lives where qBittorrent downloaded it. Medialogue matches what is on disk against TMDB, watches qBittorrent, finds upgrades and replacements through Torznab indexers, and keeps every torrent backed up so the whole library can download again onto a new server. Media is never moved or renamed.

## Install with Docker Compose

```sh
cp .env.example .env          # review PUID/PGID, paths and the media folder
docker compose up -d --build
docker compose exec media-manager cat /config/initial-setup-token
```

Open `http://HOST:8000`, enter the setup token, and create the administrator. Then, in **Settings**:

1. add TMDB (recommended before the first scan), qBittorrent, indexers and Plex as needed;
2. add your media folders, and run the first scan of each one by hand.

### TrueNAS SCALE / Dockge

Publish an image with the manual **Publish GHCR Image** workflow in GitHub Actions, and make the package public. In Dockge, create a stack from `compose.truenas-dockge.yml`, and put the values from `.env.truenas-dockge.example` in Dockge's `.env` editor, never in the compose text.

## What to know

- **Media mounts are writable.** Upgrades, replacements and **Delete with files** remove old media. To keep a folder untouched, set it to **Read only** in Settings → Media Folders. A folder set to read/write on a read-only mount is refused.
- **Path mappings.** When qBittorrent reports paths that differ from the container's (for example `/downloads/movies` vs `/media/movies`), add a mapping in Settings → Media Folders. Medialogue never guesses one.
- **HTTPS.** Behind an HTTPS reverse proxy, set `COOKIE_SECURE=true`.
- **Keep `/config` on local storage**, not SMB/NFS. It holds the SQLite database, the settings, and `application-secret`, which unlocks the stored credentials.

## Backups

Connect Google Drive under **Backups → Google Drive**. This needs a Google OAuth web client with the Drive API enabled, set through `MEDIALOGUE_GOOGLE_DRIVE_CLIENT_ID`, `_CLIENT_SECRET` and `_REDIRECT_URI` (`https://HOST/api/v1/backups/google/callback`). Every torrent, its manifest and a daily database copy go to a **Medialogue Backups** folder.

Nothing in Drive is encrypted: torrent files carry tracker passkeys, and database copies hold the integration credentials. Keep the folder private.

### Restore after losing the server

1. Install Medialogue and add the media folders and qBittorrent clients with the same container paths as before.
2. Download the **Medialogue Backups** folder from Drive (one ZIP, or several parts).
3. **Backups → Restore → Upload backup ZIP**, check the plan, and re-add the ready torrents. qBittorrent downloads everything again into the original folders.

To bring back the library and settings too, stop Medialogue first. Then unzip a `database/medialogue-database-<time>.zip`, copy `database/medialogue.db` and the files under `config/` into `/config`, and start it again.

## Upgrading

1. **Backups → Google Drive → Back up now**, or copy `/config` and `/torrent-archive` somewhere safe.
2. Pull the new image (or rebuild), and restart. Database migrations run on start; if one fails, the container does not become ready, and your data is untouched.
3. Read `CHANGELOG.md` for anything to act on.

Downgrading is not supported. Restore the backup instead.

## Development

```sh
./scripts/verify.sh            # lint, tests, API contract, production build
cd frontend && npm run dev     # Vite, proxying /api to localhost:8000
```

Third-party licences are in `THIRD_PARTY_NOTICES.md`.
