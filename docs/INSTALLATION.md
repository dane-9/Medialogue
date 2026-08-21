# Installation

Medialogue is designed to run as a Docker Compose stack with one application container and PostgreSQL 16.

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

3. Set at least these two values to unique secrets:

   ```text
   POSTGRES_PASSWORD
   SECRET_KEY
   ```

   Generate a suitable application secret with:

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

6. Wait for both containers to become healthy:

   ```sh
   docker compose ps
   ```

7. Open `http://HOST:8000` or the port selected by `MEDIA_MANAGER_PORT`.
8. Sign in with the initial credentials:

   ```text
   username: admin
   password: adminadmin
   ```

9. Follow the first-run checklist. Every integration step is optional and the first library scan is always explicit.

## Persistent data

- PostgreSQL volume: authoritative library/application state plus integration runtime health. Plex/TMDB/qBittorrent/indexer connection settings and credentials are not stored there.
- `/config/medialogue.json`: file-backed Plex, TMDB, qBittorrent-client, and indexer settings without secrets.
- `/config/secrets.enc`: AES-GCM encrypted integration credentials. It can only be decrypted with the same `MEDIALOGUE_SECRET_KEY`.
- `/config`: also contains the setup marker and temporary Recovery Bundle exports.
- `/torrent-archive`: permanent archived `.torrent` files and versioned recovery manifests.
- Media mounts: user-owned data. Medialogue does not reorganize them.

## Container permissions

The image starts briefly as root only to prepare `/config` and `/torrent-archive`, then drops to the `PUID`/`PGID` configured in `.env`. Media mounts are never chowned by the container.

The defaults are:

```text
PUID=10001
PGID=10001
```

Read-only scanning works as long as that identity can read the media paths. If a particular storage root is mounted `:rw` for the explicit Delete Media workflow, the chosen `PUID`/`PGID` must also have permission to delete from that host path.

## Dockge

Dockge can deploy the supplied Compose project from the extracted Medialogue source directory. Keep the build context pointed at that directory so Dockge can see `Dockerfile`, `backend/`, `frontend/`, and `docker/`. Add your media bind mounts in the Compose editor before deployment. Keep PostgreSQL unexposed unless you specifically need host-level database access.
