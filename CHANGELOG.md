# Changelog

## Runtime stability fixes (v7)

- Fixed storage-root scans remaining visibly queued and made Job cancellation durable/live.
- Removed authenticated GET session writes that caused database contention.
- Separated qBittorrent connectivity health from Medialogue torrent-processing failures.
- Added per-torrent database SAVEPOINT isolation so one bad torrent does not stop the rest of a client poll.
- Added persistent qBittorrent health diagnostics and migration `0011_download_client_health_diagnostics`.
- Fixed Custom Formats crashing on plain LAN HTTP when `crypto.randomUUID()` is unavailable.
- Added a page-level React error boundary.
- Replaced high-frequency full Problems-list refreshes with a lightweight count endpoint plus SSE count updates.
- Disabled production Uvicorn access logs by default; opt in with `MEDIALOGUE_ACCESS_LOG=1`.

## 0.1.0

Initial integrated Medialogue build covering the v1 roadmap through packaging/release readiness:

- leave-in-place Movie and Show inventory;
- TMDB identity and Plex secondary verification;
- multiple qBittorrent clients and torrent recovery archive;
- replacement/duplicate reconciliation;
- interactive Prowlarr/Torznab search;
- Custom Formats and Quality Profiles;
- season packs and multi-episode mapping;
- Problems, Jobs, SSE, Events, Recovery Bundle export;
- Movie tags and bulk administration;
- first-run setup checklist and Docker release packaging.

## Deployment fix

- Force `/usr/local/bin/medialogue-entrypoint` to mode `0755` during Docker build so Git/ZIP/Windows file-mode loss cannot make the container fail with `permission denied`.
- Force shell scripts to LF line endings in Git.
- CI now verifies the built container entrypoint is executable before booting the smoke-test stack.
## Deployment fix v4

- Restrict setuptools package discovery to `app*` only. The `backend/alembic/` migration directory and `tests/` are no longer installed into Python site-packages.
- Prevent a stale `backend/alembic/__init__.py` from colliding with the real Alembic dependency.
- Docker builds now fail immediately if the real Alembic import/CLI is broken.
- Manual GHCR publishing now builds and validates the image locally before any tag is pushed.

