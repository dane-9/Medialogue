# v6 — Large torrent size PostgreSQL fix

This update fixes a real-world PostgreSQL failure discovered while polling qBittorrent.

## Symptom

qBittorrent torrents larger than 2,147,483,647 bytes caused polling to fail with:

```text
asyncpg.exceptions.DataError: value out of int32 range
```

## Cause

`torrents.total_size` was created as PostgreSQL `INTEGER` (32-bit), even though qBittorrent reports byte sizes and normal 4K movies / season packs can be tens or hundreds of gigabytes.

## Fix

- ORM column is now explicitly `BIGINT`.
- Alembic migration `0010_torrent_size_bigint` upgrades existing databases in place.
- No database wipe is required.
- PostgreSQL CI now includes a >96 GB torrent-size round-trip regression test.

After publishing the updated image, pull and recreate the Medialogue container. The container entrypoint automatically runs `alembic upgrade head` before starting the API.
