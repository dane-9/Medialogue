# Medialogue

A self-hosted manager for a movie and show library that lives where qBittorrent downloaded it. Medialogue matches what is on disk against TMDB, watches qBittorrent, finds upgrades and replacements through Torznab indexers, and keeps every torrent backed up so the whole library can download again onto a new server. Media is never moved or renamed.

## Install with Docker Compose

Create a `compose.yaml`:

```yaml
services:
  medialogue:
    image: ghcr.io/dane-9/medialogue:latest
    container_name: medialogue
    restart: unless-stopped
    ports:
      - 8000:8000
    environment:
      - PUID=1000                  # user and group that own your media
      - PGID=1000
      - TZ=Etc/UTC
      - MEDIALOGUE_COOKIE_SECURE=false   # true behind an HTTPS reverse proxy
    volumes:
      - /path/to/medialogue/config:/config                    # database and settings, on local storage
      - /path/to/medialogue/torrent-archive:/torrent-archive  # backed-up .torrent files
      - /path/to/movies:/media/movies
      - /path/to/shows:/media/shows
```

The media paths inside the container (`/media/movies`, `/media/shows`) are what you add as media folders in Settings. Start it with `docker compose up -d`, open `http://HOST:8000`, and create the administrator. Then, in **Settings**:

1. add TMDB (recommended before the first scan), qBittorrent, indexers and Plex as needed;
2. add your media folders, and run the first scan of each one by hand.

To build from source instead, clone the repository, copy `.env.example` to `.env`, and run `docker compose up -d --build` with the included `docker-compose.yml`.

## What to know

- **Media mounts are writable.** Upgrades, replacements and **Delete with files** remove old media. To keep a folder untouched, set it to **Read only** in Settings → Media Folders. A folder set to read/write on a read-only mount is refused.
- **Path mappings.** When qBittorrent reports paths that differ from the container's (for example `/downloads/movies` vs `/media/movies`), add a mapping in Settings → Media Folders. Medialogue never guesses one.
- **HTTPS.** Behind an HTTPS reverse proxy, set `MEDIALOGUE_COOKIE_SECURE=true`.
- **Keep `/config` on local storage**, not SMB/NFS. It holds the SQLite database, the settings, and `application-secret`, which unlocks the stored credentials.

## Backups

Connect Google Drive under **Backups → Google Drive**. Once, create an OAuth client in your own Google Cloud project; the page walks through it:

1. Create a project and enable the **Google Drive API**.
2. In **Google Auth Platform**, set it up with audience **External**, then **Publish app** (while it is in Testing, Google signs Medialogue out every 7 days).
3. Create a client of type **TVs and Limited Input devices**, and paste its ID and secret on the Backups page.

Then **Connect Google Drive** shows a code to enter at `google.com/device` on any phone or computer. No redirect URI or HTTPS is needed. (`MEDIALOGUE_GOOGLE_DRIVE_CLIENT_ID` and `_CLIENT_SECRET` still work in place of the page.)

Every torrent, its manifest and a daily database copy go to a **Medialogue Backups** folder.

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
