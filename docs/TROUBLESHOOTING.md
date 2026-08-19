# Troubleshooting

## Container is not healthy

Check:

```sh
docker compose ps
docker compose logs media-manager
docker compose logs postgres
```

`/healthz` is process liveness. `/readyz` also verifies the database and is the container healthcheck target.

## Permission denied under /config or /torrent-archive

Confirm `PUID`/`PGID` in `.env`. The startup entrypoint prepares these two application-owned mounts automatically. If the host filesystem prevents ownership changes, pre-create the directories with permissions appropriate for that identity.

## Media scans work but Delete Media fails

The root must be both:

1. mounted read/write in Docker; and
2. configured as `read_write` in Medialogue.

The runtime PUID/PGID must have host filesystem delete permission. Medialogue never changes media-root ownership itself.

## Root suddenly shows offline

Do not remove/re-add all titles. A whole-root outage is tracked separately and does not mass-convert known media to Missing. Restore the mount/network path, then recheck/scan the root.

## qBittorrent path mapping failed

Compare the path reported by qBittorrent with the container-visible media path and create a Remote Path Mapping under Settings → Storage Roots. Medialogue intentionally refuses to guess.

## Plex is red

Plex Unavailable is different from Plex Conflict. Local media remains attached when Plex is merely unavailable.

## Search returns partial results

Check per-indexer status in the search job. Indexers time out independently; successful results are retained.

## Recovery export is unsupported

Open Settings → Backup / Recovery and inspect the capability reasons. Physical export requires PostgreSQL, matching `pg_basebackup` major version, a readable torrent archive, a writable export directory, and no unsupported custom tablespaces.
