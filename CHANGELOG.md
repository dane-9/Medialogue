## v9 file-backed integration configuration

- Make `/config/medialogue.json` the source of truth for Plex, TMDB, qBittorrent-client, and indexer settings.
- Store Plex tokens, TMDB/indexer API keys, and qBittorrent passwords only in `/config/secrets.enc`, encrypted with AES-GCM using a key derived from `MEDIALOGUE_SECRET_KEY`.
- Reduce the corresponding PostgreSQL tables to runtime health/reference state only; connection URLs, usernames, scopes, enabled flags, polling intervals, and credentials are no longer database columns.
- Intentionally provide **no legacy database import/migration path** for these settings. This build targets a fresh PostgreSQL database; configure integrations normally through the UI.
- Include both the file-backed configuration and encrypted live config files in Recovery Bundles.

## v9 operations/root initialization hotfix

- Remove the global **Active Operations** toggle. Existing compatibility endpoints remain hidden and always report enabled, while explicit destructive preview/confirmation safeguards remain unchanged.
- Require every newly added storage root to complete one explicit **Initialize & scan** before global reconciliation or qBittorrent filesystem reconciliation can use it.
- Use the existing durable `last_scan_at` marker, so a failed or cancelled first scan leaves the root uninitialized and inert.
- Surface uninitialized roots in Storage settings and refresh their state automatically when the initialization job finishes.

## v9 movie-detail and publishing hotfix

- Fix every Movie detail request failing with HTTP 500 because `poster_ref` was passed twice while constructing `MovieDetailResponse`.
- Remove the typed `PUBLISH` confirmation input from the manual GHCR workflow; clicking **Run workflow** remains the explicit publish action.
- Keep Show detail unchanged after regression coverage confirmed it does not share the Movie response-construction bug.

## v9 fresh baseline and library usability

- Squash development database history to one clean-install Alembic revision (`0001`); incompatible pre-v9 databases are intentionally not supported.
- Add safe storage-root removal. Removing a configured root detaches retained inventory evidence and never deletes media.
- Add individual and bulk Problem deletion plus server-side Problem pagination/filtering, fixing the UI's first-50-only behavior on large queues; deletion updates the sidebar count live.
- Add individual and bulk Event History deletion plus pagination.
- Add read-only Plex library snapshot verification, including correct Plex Episode (`type=4`) enumeration for TV libraries, an explicit full-library sync job, and automatic root-scoped verification after manual storage scans. Medialogue still never triggers Plex scans.
- Render TMDB poster artwork on Movie/Show cards, tables, and detail views; Movie list responses now retain `poster_ref`.
- Add regression coverage for fresh-schema creation, root removal, large Problem queues, history deletion, and Plex library snapshots.

## v8 runtime migration hotfix

- Widen PostgreSQL `alembic_version.version_num` from 32 to 128 characters before applying revision 0011.
- Add regression checks for descriptive Alembic revision identifiers.

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

