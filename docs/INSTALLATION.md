# Installation

Medialogue is designed to run as one Docker Compose application container with an embedded SQLite database.

## Requirements

- Docker Engine with Docker Compose v2, or a Compose-compatible manager such as Dockge.
- A host that can mount the Movie/Show paths you want Medialogue to inspect.
- Network access from the Medialogue container to the services you configure, such as TMDB, Plex, qBittorrent, and Prowlarr/Torznab endpoints.

## Fresh install

1. Extract the Medialogue source release ZIP (or clone the repository) into its own directory. The supplied Compose stack builds the application image from that directory, so keep `Dockerfile`, `backend/`, `frontend/`, and `docker/` beside `docker-compose.yml`.
2. Create `.env` from the supplied example:

   ```sh
   cp .env.example .env
   ```

3. An application secret is generated automatically and stored at
   `/config/application-secret` with restrictive permissions. To provide an
   explicit override instead, set `MEDIALOGUE_SECRET_KEY` in the local
   environment. Generate a suitable value with:

   ```sh
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

4. Add only the media bind mounts Medialogue should be able to see. Prefer `:ro` initially. For example:

   ```yaml
   volumes:
     - /mnt/media/Movies:/media/movies:ro
     - /mnt/media/Shows:/media/shows:ro
   ```

5. Start the stack:

   ```sh
   docker compose up -d --build
   ```

6. Wait for the application container to become healthy:

   ```sh
   docker compose ps
   ```

7. Open `http://HOST:8000` or the port selected by `MEDIA_MANAGER_PORT`.
8. Complete the First Run administrator setup. The one-time setup token is
   printed only as an operator retrieval instruction; read it from the
   persistent config volume:

   ```sh
   docker compose exec media-manager cat /config/initial-setup-token
   ```

   Choose an administrator username and a password of at least 12 characters.
9. Follow the first-run checklist. Every integration step is optional and the first library scan is always explicit.

## Persistent data

- `/config/medialogue.db`: authoritative SQLite library/application state plus integration runtime health.
- `/config/medialogue.json`: file-backed Plex, TMDB, qBittorrent-client, and indexer settings without secrets.
- `/config/secrets.enc`: AES-GCM encrypted integration credentials. It can only be decrypted with the same `MEDIALOGUE_SECRET_KEY`.
- `/config/application-secret`: generated production secret used when `MEDIALOGUE_SECRET_KEY` is not explicitly supplied. Back it up with `/config`.
- `/config/initial-setup-token`: one-time first-run administrator token; it is removed after successful setup.
- `/config`: durable application storage containing the SQLite database, setup marker, configuration, secrets, the Google Drive connection, and backup ZIPs while an upload is being read. Mount it from local storage, not SMB/NFS.
- `/torrent-archive`: permanent archived `.torrent` files and versioned recovery manifests.
- Media mounts: user-owned data. Medialogue does not reorganize them.

## Container permissions

The image starts briefly as root only to prepare `/config` and `/torrent-archive`, then drops to the `PUID`/`PGID` configured in `.env`. Media mounts are never chowned by the container.

The defaults are:

```text
PUID=10001
PGID=10001
```

Read-only scanning works as long as that identity can read the media paths. Duplicate cleanup is performed outside Medialogue with the administrator's normal filesystem tools.

UID 0 is rejected by default. Set `MEDIALOGUE_ALLOW_ROOT=true` only when a
NAS compatibility deployment deliberately requires the process to remain root;
the normal Compose and TrueNAS examples use non-root identities.

## Dockge

Dockge can deploy the supplied Compose project from the extracted Medialogue source directory. Keep the build context pointed at that directory so Dockge can see `Dockerfile`, `backend/`, `frontend/`, and `docker/`. Add your media bind mounts in the Compose editor before deployment. Run only one Medialogue container against a given `/config/medialogue.db`.
