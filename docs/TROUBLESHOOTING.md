# Troubleshooting

## Container is not healthy

Check:

```sh
docker compose ps
docker compose logs media-manager
```

`/readyz` verifies the database and is the container healthcheck target.

## Permission denied under /config or /torrent-archive

Confirm `PUID`/`PGID` in `.env`. The startup entrypoint prepares these two application-owned mounts automatically. If the host filesystem prevents ownership changes, pre-create the directories with permissions appropriate for that identity.

## Medialogue cannot decrypt `/config/secrets.enc`

Keep the same `MEDIALOGUE_SECRET_KEY` that was used when the file was written. Medialogue intentionally refuses to start with silently unreadable credentials. If you deliberately want a completely fresh configuration, stop the container and remove both `medialogue.json` and `secrets.enc`, then start Medialogue and configure integrations again.

## Only one qBittorrent instance fails authentication

Treat each qBittorrent client independently. If another configured instance works, the common Medialogue networking/auth path is functioning and the failing instance should be checked for its own credentials, WebUI failed-login/IP-ban state, reverse-proxy base path, and host access rules. Medialogue distinguishes rejected credentials from qBittorrent's HTTP 403 temporary IP ban and stops automatic retries after a confirmed auth failure.

## Root suddenly shows offline

Do not remove/re-add all titles. A whole-root outage is tracked separately and does not mass-convert known media to Missing. Restore the mount/network path, then recheck/scan the root.

## qBittorrent path mapping failed

Compare the path reported by qBittorrent with the container-visible media path and create a Remote Path Mapping under Settings → Storage Roots. Medialogue intentionally refuses to guess.

## Plex is red

Plex Unavailable is different from Plex Conflict. Local media remains attached when Plex is merely unavailable.

## Search returns partial results

Check per-indexer status in the search job. Indexers time out independently; successful results are retained.

## Backups to Google Drive fail

Open **Backups → Google Drive**; the last error is shown there and on the backup task in **Tasks**.

- **Google Drive is not set up on this server**: the three `MEDIALOGUE_GOOGLE_DRIVE_*` variables are missing. Set them and restart.
- **Google Drive authorization failed. Reconnect your account.**: Google stopped accepting the saved token, often because the OAuth app is in testing mode. Select **Reconnect**.
- **N file(s) could not be uploaded**: the rest went up; the next run retries the failed files.

## Uploading a backup ZIP fails

- **Request too large** from a reverse proxy: raise its request body limit (nginx `client_max_body_size`) or upload from the local network.
- **No torrents or manifests were found in that ZIP**: upload the **Medialogue Backups** folder as Google Drive downloads it, not a database copy.

## Medialogue exits during startup with `cannot import name '__version__' from 'alembic'`

This was caused by an older packaging configuration accidentally treating Medialogue's `backend/alembic/` migration-script directory as a Python package. A stale `backend/alembic/__init__.py` left behind by GitHub web uploads could then collide with the real Alembic dependency.

Use the v4-or-newer source package, publish a new image, then pull/recreate the Medialogue container. v4 restricts Python package discovery to `app*` and validates the real Alembic CLI during the Docker build and again before GHCR publishing.

If you maintain the repository through GitHub's web uploader, remember that uploading a newer folder does not delete obsolete files already in the repository. Delete obsolete files explicitly or use a normal Git checkout/sync.
