# Medialogue

Medialogue is a dark, desktop-first movie/show inventory and download-management application built around one rule: **media stays exactly where it was downloaded**. It is designed for Docker Compose, uses TMDB as the primary metadata identity source, and treats Plex as secondary verification rather than the source of truth.

## Documentation

Detailed deployment and administration guides are under [`docs/`](docs/README.md): Installation, Upgrade, Backup/Restore, Path Mapping, qBittorrent, Plex, Prowlarr/Torznab, Custom Formats, and Troubleshooting.


## TrueNAS SCALE + Dockge

For TrueNAS SCALE with Dockge installed as an App, use the GHCR deployment path rather than building source inside Dockge. **Nothing publishes automatically:** both CI and GHCR publishing are manual GitHub Actions workflows that run only when you click **Run workflow**. Secrets stay in Dockge's local `.env`, not in the GitHub repository. Follow [`TRUENAS_DOCKGE_GHCR.md`](TRUENAS_DOCKGE_GHCR.md), then paste [`compose.truenas-dockge.yml`](compose.truenas-dockge.yml) into Dockge.

## Quick start

Extract this source release (or clone the repository) into one directory; the supplied Compose file builds the production image from the included Dockerfile, backend, and frontend.

1. Copy `.env.example` to `.env` and set a strong `POSTGRES_PASSWORD` and `SECRET_KEY`.
2. Review `PUID`/`PGID` and the persistent directory paths. The container prepares `/config` and `/torrent-archive` before dropping privileges; media roots are never chowned.
3. Add only the media bind mounts Medialogue should be allowed to inspect to `docker-compose.yml`. The example mounts `/media` read-only.
4. Start the stack:

   ```sh
   docker compose up -d --build
   ```

5. Open `http://localhost:8000` and sign in with `admin` / `adminadmin`. The first-run checklist appears automatically.
6. Configure only the integrations you use. TMDB is recommended before the first scan because it establishes external identity for newly discovered titles.
7. Finish or skip optional setup items, enable Active Operations when ready, then explicitly start the first storage-root scan. Setup never starts it automatically.

The top-bar **Active operations** control is SAFE/OFF on a fresh install. Health checks may still run while it is off, but scans, searches, download submission, and destructive operations remain explicit user actions.

## Local frontend development

```sh
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://localhost:8000`. Run `npm run build` to create the production frontend bundle.

## Storage and persistence

Medialogue only scans storage roots explicitly configured in Settings.

- PostgreSQL is the authoritative persistent store for application/library state and history.
- `/config` stores non-database application configuration/cache/log material and temporary Recovery Bundle ZIPs under `/config/recovery-exports`.
- `/torrent-archive` stores archived `.torrent` files and versioned recovery manifests independently from live qBittorrent state.
- Media roots are user-owned filesystem state. Medialogue never renames, moves, copies, hardlinks, reorganizes, or creates metadata sidecars beside media.

The example Compose file mounts `/media` as **read-only**, which is sufficient for discovery, matching, Plex verification, and Missing detection. If you want Medialogue's explicit **Delete Media** workflow to delete a configured root's directories, mount that specific root read/write and configure it as `read_write` in Medialogue. Read/write access is never required merely to scan or track media.

## Torrent archive and recovery manifests

Every in-scope torrent observed through a configured qBittorrent client is backed up to the dedicated `/torrent-archive` mount while qBittorrent can still export its `.torrent` metadata. Archive failures are non-destructive and are retried on later observations; they are also visible in the Torrent Archive UI.

Archive files are keyed by info hash and sharded to keep directories manageable:

```text
/torrent-archive/
├── torrents/<prefix>/<info-hash>.torrent
└── manifests/<prefix>/<info-hash>.json
```

Recovery manifests are versioned (`schema_version: 1`) and retain the torrent identity, Movie/Show identity, TMDB/TVDB IDs, release name, quality, edition, release group, parser snapshot, historical paths, qBittorrent-client observations, timestamps, and a SHA-256 digest of the archived `.torrent` file. The manifest is deliberately stored in the archive, never beside media.

Torrent backups are retained independently from the logical Movie/Show record. `Remove From App Only`, a replaced release, or a torrent being removed from qBittorrent does not delete the archived `.torrent` or its recovery manifest. When the database association disappears, later manifest refreshes preserve the last known media identity/path evidence already recorded on disk.

The **Torrent Archive** page is the global expert view. A Movie detail page also shows its associated torrent history. Archived torrents can be submitted back to an explicitly selected qBittorrent client. Restore requires an explicit save path and Medialogue verifies that the resolved destination is inside an enabled storage root matching that client's Movie/Show scope before sending anything to qBittorrent.

The `/torrent-archive` mount should therefore be durable and writable. It is recovery data, not disposable cache.

## Recovery Bundle export

**Settings → Backup / Recovery** can create a complete disaster-recovery ZIP. The export runs as a persistent background Job, so its state remains visible across navigation and browser refresh. A completed bundle is available through a temporary authenticated download endpoint for the configured retention period (24 hours by default).

The bundle contains:

```text
medialogue-recovery-<job-id>.zip
├── backup-metadata.json
├── database/
│   └── physical-base-backup/
├── torrent-archive/
│   └── torrents/...
├── manifests/...
├── config/
│   └── application-config-export.json
└── inventory/
    ├── library-inventory.json
    └── torrent-archive-inventory.json
```

The database backup is a **PostgreSQL physical base backup created with `pg_basebackup`**. Medialogue does not recursively copy a live PostgreSQL data directory. The production image includes PostgreSQL 16 `pg_basebackup`, matching the PostgreSQL 16 service in the supplied Compose stack. Before export, Medialogue verifies the database backend, client/server major-version compatibility, the torrent-archive mount, the export directory, and rejects custom PostgreSQL tablespaces until explicit tablespace mapping is implemented.

`backup-metadata.json` records the application version, PostgreSQL version/major, Alembic migration revision, torrent-manifest schema version, export timestamp, and temporary-download expiry. The human-readable configuration export includes integration credentials plus the runtime database URL and application secret key because the goal is disaster recovery. **The Recovery Bundle is therefore highly sensitive and should be protected like a database/password backup.**

The library inventory is deliberately readable without PostgreSQL and records Movies, Shows, releases, Episodes, paths, media files, torrent associations, Plex observations, parse evidence, and Problems. It is secondary evidence; the physical PostgreSQL backup remains the authoritative application-state backup.

### Restoring a Recovery Bundle

Medialogue does not perform an automatic in-place database restore from the web UI. Restore is intentionally an administrator operation:

1. Stop Medialogue and the target PostgreSQL instance.
2. Read `backup-metadata.json` and use the same PostgreSQL **major** version recorded by the bundle.
3. Restore `database/physical-base-backup/` into an empty PostgreSQL data directory/volume, preserving the files and assigning them to the PostgreSQL service account. Do not merge the backup into a running or non-empty cluster.
4. Recreate/adapt deployment environment values from `config/application-config-export.json`. Hostnames/paths may need to change on the new host.
5. Restore archived torrents to the configured archive mount by copying `torrent-archive/torrents/` into `/torrent-archive/torrents/` and `manifests/` into `/torrent-archive/manifests/`.
6. Start PostgreSQL, verify it is healthy, then start Medialogue. During the current clean-baseline development phase, restore only a database created by the same compatible Medialogue schema; older schemas are not upgraded in place.
7. Recreate the media bind mounts and remote-path mappings appropriate to the new host before scanning. Medialogue will not relocate media automatically.

For a Docker volume restore, use a temporary container or other controlled administrative method to populate the empty PostgreSQL volume while the database service is stopped. Exact commands depend on the deployment host and volume driver.

## Prowlarr-backed indexers and Interactive Search

Medialogue does not import Prowlarr configuration wholesale. Add each indexer explicitly under **Settings → Indexers** using the Torznab endpoint Prowlarr exposes for that indexer, its API key, a `Movies`, `Shows`, or `Movies + Shows` scope, and an optional per-indexer timeout.

Interactive Search is deliberately manual:

1. Open **Interactive Search** and select a Movie from the Medialogue library.
2. Medialogue creates a persistent search Job and fans the query out concurrently to every enabled `Movies`/`Both` indexer.
3. Results appear progressively as indexers respond. A failed or timed-out indexer is shown explicitly and does not cancel successful results from the others.
4. Release names are run through the same shared parser used by scanning and qBittorrent reconciliation. Every enabled, scope-eligible Custom Format is evaluated immediately. The target title's effective Quality Profile, per-title overrides, signed score contributions, and minimum-quality warning state are frozen with the result as immutable search-time evidence.
5. Choosing **Download** submits only that result. If exactly one eligible Movie qBittorrent client exists it is used immediately; with multiple eligible clients the UI asks which one to use.

Searching itself is read-only and is allowed while Active Operations is off. Actual qBittorrent submission requires **Active Operations: ON**.

Medialogue does **not** pass a save path when a normal Interactive Search result is submitted. The selected qBittorrent instance, its category/tags, and qBittorrent's own configuration decide where the torrent downloads. Medialogue later observes and reconciles the completed data in place; it never imports or relocates it.

Unselected search results expire after 24 hours. Once a result is submitted, its parser/search/indexer/client evidence is retained as an immutable selection snapshot so later release history can preserve what was chosen at download time. Raw Torznab download URLs/API credentials are never returned to the browser.


## Custom Formats

Custom Formats are persistent, explainable release-matching definitions. A Custom Format itself **does not own a score**. It only answers whether a release matches; signed scores are assigned by Quality Profiles.

The editor supports these condition types:

```text
Release Title       Release Group
Quality             Quality Modifier
Resolution          Source
Edition             Language
Indexer             WEB Provider
Video Codec         Audio Codec
Audio Channels      HDR Type
Release Attribute
```

Release Title and Release Group conditions use regular expressions. Regex matching is case-insensitive by default, with an optional case-sensitive setting. Structured types use the shared parser fields rather than regexing the raw release name whenever structured evidence exists.

Condition behavior follows the intended Radarr-style mental model: required conditions are mandatory, optional conditions of the same type form an OR group, distinct groups combine with AND semantics, and any condition can be negated. Explicit group names are available for advanced cases where two groups of the same condition type must remain independent.

The full-page Custom Format editor includes **Test this format** and **Test all saved** tools. Test output shows every condition's pass/fail result, the evidence used, the expected value, grouping/negation state, and the full parser result. This is the same parser/evaluator used when Interactive Search results are stored.

Custom Formats can be imported/exported as Medialogue-owned versioned JSON (`schema_version: 1`). This is intentionally **not** Radarr JSON compatibility; formats from Radarr should be recreated manually so Medialogue's parser/condition semantics remain explicit.

Interactive Search snapshots matching and scoring evidence at discovery time. Editing a Custom Format, Quality Profile, or per-title override later does not rewrite an older search result's stored evidence. Search results with no configured score still evaluate normally and contribute `0`.


## Quality Profiles and per-title overrides

Quality Profiles turn Custom Format matches into explainable signed search scores. A profile contains only the policy Medialogue needs for manual selection:

```text
name
minimum acceptable quality (optional)
Custom Format -> signed score
```

Quality Definitions are application-owned and hardcoded. They are exposed read-only to the UI; users do not create or edit quality classes. The minimum-quality selection is a **warning floor only**. Results below it remain visible and downloadable, and it never creates an automatic upgrade/cutoff engine.

Custom Format scores can be positive, zero, or negative. A matching zero-score format is still shown in the result evidence. Scores never hide or reject a result.

Movies and Shows can have per-title profile settings. A title can select a base Quality Profile and optionally override its minimum quality and individual Custom Format scores. A per-title Custom Format override **replaces** the profile score for that format; it is not added on top. The title UI shows only these differences from the selected profile.

Interactive Search freezes the effective policy when a search begins so every indexer result from that job is scored under the same rules. Each result stores:

- profile identity/revision;
- title-assignment revision;
- minimum-quality rule and whether the candidate meets it;
- every eligible Custom Format match;
- profile score, title override, effective score, and contribution;
- final signed score.

The snapshot is immutable. Later profile/Custom Format edits do not rewrite what a result scored when it was discovered or selected.

The Custom Format **Test All** tool can optionally evaluate a release against a selected Quality Profile, showing each matching format's profile score/contribution and the resulting total. Testing a single Custom Format remains match-only so a Custom Format never appears to own an intrinsic score.

When a selected result becomes an attached release, Medialogue preserves the selection snapshot and its **download-time score** on release history. It also stores a separate **current score**, re-evaluated under the rules that exist now. Profile changes, title overrides, and Custom Format edits refresh current scores without altering historical selection evidence.

## Shows, Seasons, and Episodes

Show libraries are tracked as a real **Show → Season → Episode** hierarchy. TMDB is the primary metadata source and TMDB's external IDs are used to retain TVDB IDs as supporting identity evidence where available.

A Show can be added directly from TMDB or discovered by explicitly scanning a configured **Shows** storage root. Files remain exactly where they already exist; Medialogue never renames, moves, copies, imports, or hardlinks them. Ordinary single-episode names such as `S01E01` are mapped to individual Episode records and each episode keeps its own Present/Missing and monitored state.

Missing detection is episode-aware. If one episode file disappears while the Show directory remains available, only that mapped episode is affected after the configured missing-grace checks. A whole-root outage is still handled as a root-health failure rather than mass-marking episodes Missing.

The Shows UI provides poster cards by default, an optional dense table, expandable Seasons, per-Episode monitoring, quality/path evidence, Plex state, TMDB metadata refresh, and the existing Season/Episode Interactive Search actions. Plex verification for Shows is read-only and checks exact mapped episode paths where possible.

Season packs and multi-episode files are supported without changing the media on disk. A season pack is represented by one shared `ShowRelease` that can satisfy many Episode records while every member file remains in its original qBittorrent/download directory. Files such as `S01E01E02` and `S01E01-E03` map one physical `MediaFile` to multiple logical Episodes.

Mapping is deliberately partial-safe: if a pack or multi-episode filename contains episode numbers that cannot be verified against known season metadata, Medialogue maps the episodes it can prove and raises an `EPISODE_MAPPING_UNRESOLVED` Problem for only the unresolved portion. It does not reject or relocate the rest of the pack. Multiple physical files mapped to the same Episode produce a `DUPLICATE_EPISODE_RELEASE` Problem and are left untouched.

Episode mappings can also be corrected manually from the Show page. This is a database-only logical correction: the media file is not renamed, moved, copied, hardlinked, or modified. A manual mapping is authoritative on future scans, including episode numbers deliberately removed from the parser-derived mapping.

## Problems and duplicate resolution

Medialogue has one persistent **Problems** queue for reconciliation evidence that needs a human decision. Problems are not generic notifications: each reason exposes only the actions that are valid for that evidence. Rechecking a Problem does not fake a resolution; it remains open until a later observation proves the underlying condition changed.

Common reasons include:

```text
PLEX_IDENTITY_MISMATCH
LOW_CONFIDENCE_MATCH
PATH_MAPPING_FAILED
TORRENT_PATH_NOT_FOUND
ROOT_UNREACHABLE
DUPLICATE_PHYSICAL_RELEASE
DUPLICATE_EPISODE_RELEASE
```

The Problems page supports filtering, explicit TMDB Movie/Show matching, episode duplicate preference, path-mapping guidance, and direct duplicate comparison. Manual identity selection is authoritative, but conflicting Plex evidence remains visible rather than being silently erased. Identity correction changes database metadata only; it never renames or relocates media.

### Movie physical duplicates

A `DUPLICATE_PHYSICAL_RELEASE` is deliberately left untouched until the administrator chooses what to do. The duplicate resolver first asks which release is preferred. Selecting a winner alone does **not** remove the warning while the other physical directory still exists.

Optional deletion follows a two-stage destructive workflow:

1. **Preview** performs a fresh recursive inventory of every losing media directory, including subtitles, artwork, text files, and other sidecars already present there.
2. The server returns the exact directories/files, qBittorrent associations, archive status, and a short-lived signed confirmation token.
3. **Commit** re-inventories the targets. If anything changed after preview, the request fails with `DELETE_PREVIEW_STALE` and a new preview is required.
4. Only a configured `read_write` storage root can be deleted. The entire explicitly selected losing media directory is removed; Medialogue does not selectively reorganize its contents.

The global **Active Operations** toggle must be on before a duplicate commit. Paths are checked again against the configured storage root and directory symlink targets are refused.

If the user also chooses to remove the losing torrent from qBittorrent, Medialogue first requires that torrent to be safely archived. qBittorrent is then called with **delete data = false** because filesystem deletion is controlled independently by the reviewed directory operation. The archived `.torrent` and manifest remain in `/torrent-archive`. A qBittorrent removal failure is preserved as its own Problem rather than discarding recovery evidence.

### Episode duplicates

For `DUPLICATE_EPISODE_RELEASE`, the user can choose the preferred physical file/mapping. That preference becomes manual authority, but both physical files remain untouched and the Problem stays open until later filesystem evidence shows that the losing copy has actually disappeared.

### Path mapping problems

Remote qBittorrent path mappings can be configured under **Settings → Storage Roots**. The UI stores the reported remote prefix and its container-visible local prefix, optionally scoped to one qBittorrent client and one storage root. Medialogue never guesses a path translation. After adding or correcting a mapping, recheck the affected Problem/reconciliation evidence.

Storage roots can now be created as either **Read-only** or **Read/write** from the UI. Read-only is the safe detection default; read/write is required only for explicit confirmed filesystem deletion.

## Tags and bulk Movie administration

Movie tags are lightweight application metadata stored in PostgreSQL. They are **Movies-only in v1** and never create sidecars or alter media paths. Tags can be created once, assigned to any number of Movies, removed independently, and clicked in the Movie UI to filter the library.

The Movies page supports selection without adding a row of permanent action buttons. Right-clicking a Movie opens its context actions; Ctrl/Cmd-click (or Shift-click) can build a multi-Movie selection before right-clicking. Safe bulk operations include:

```text
change Quality Profile
add/remove tags
monitor/unmonitor
recheck Plex
re-evaluate parser
re-evaluate Custom Formats/current score
```

Bulk Quality Profile changes preserve each title's existing minimum-quality and Custom Format score overrides. Parser re-evaluation updates current structured parser fields while preserving manual edition authority and durable prior parse evidence. Custom Format re-evaluation changes only the **current** score/evidence; immutable download-time search/selection snapshots remain untouched.

Bulk Plex rechecks require **Active Operations: ON** because they perform live integration work. Tag/profile/monitoring edits and parser/Custom Format re-evaluation are logical database operations and do not mutate the filesystem. No bulk action can move, rename, copy, import, hardlink, or reorganize media.

## Jobs, live updates, and Event History

Medialogue persists long-running operations as database-backed **Jobs** rather than treating them as browser-local tasks. Job states are:

```text
queued
running
completed
failed
cancelled
interrupted
```

Scans, interactive searches, recovery work, and other long-running operations therefore survive page navigation and browser refreshes. The global Jobs drawer reloads persisted state from the API and can cancel work that is still safe to cancel. If Medialogue itself restarts, any queued or running job that depended on the previous process is marked `interrupted` instead of being silently resumed from an uncertain point.

The browser also keeps a same-origin **Server-Sent Events (SSE)** connection open to `/api/v1/events/stream`. SSE is used for live operational state such as:

```text
download.progress
download.completed
scan.progress
scan.completed
search.result
search.indexer_status
job.status
plex.health
problem.created
problem.resolved
storage_root.health
release.replaced
```

High-frequency progress is intentionally **transient**. For example, every qBittorrent percentage update is useful while watching a download but is not stored forever as Event History. Durable Events are reserved for meaningful changes such as a completed download, a newly detected Problem, a Problem resolution, a root outage/recovery, or a release replacement.

A global **Event History** page provides filtering by severity, entity type, event type, and date. Movie and Show detail APIs also build a title-level timeline from related releases, media directories/files, torrents, seasons, and episodes rather than showing only events directly attached to the top-level title record.

SSE is an update/invalidation channel, not the sole source of truth. If the browser disconnects or refreshes, current Jobs, Problems, health, and durable Events are reloaded from PostgreSQL through the REST API.

## Hardening and automated regression coverage

Part 19 adds release-readiness hardening around the existing state engine instead of changing Medialogue's leave-in-place behavior.

API documentation is available to an authenticated administrator at:

```text
/api/docs
/api/redoc
/api/openapi.json
```

These endpoints are intentionally not public. API responses also receive conservative browser security headers, and API responses are marked `no-store` where appropriate.

Authentication maintenance is stricter as well: expired sessions are pruned during login, and changing the administrator password revokes other active sessions while keeping the session that performed the password change alive. CSRF tokens remain bound to the session that issued them.

Destructive duplicate-resolution confirmations are HMAC-signed, short-lived, and tied to the exact reviewed targets. Tampered or expired confirmation tokens are rejected before any filesystem or qBittorrent operation occurs, and the existing fresh-inventory check still rejects a preview whose underlying directory contents changed.

The Movies and Shows card/table view preference is persisted locally in the browser. Multi-selection and right-click behavior are implemented through deterministic UI-state helpers so selecting, context-clicking, duplicate comparison, Problem filtering, search warnings, and Custom Format regex-condition behavior can be regression-tested without relying on visual/manual testing alone.

### Continuous-integration quality gates

The repository includes `.github/workflows/ci.yml`. CI performs both fast application regression testing and production-relevant checks:

```text
Backend
  Python compile check
  SQLite-backed fast regression suite
  PostgreSQL 16 service
  real postgresql+asyncpg Alembic upgrade to head
  PostgreSQL transaction / uniqueness / JSONB integration test

Frontend
  npm ci
  Vitest UI-state tests
  production Vite build
```

PostgreSQL-specific CI is important because production uses PostgreSQL rather than SQLite. The Alembic environment now uses SQLAlchemy's asynchronous migration path when the configured URL uses `asyncpg`, so the supplied production dependency set does not require an undeclared synchronous PostgreSQL driver merely to run migrations. Plain SQLite migration URLs remain supported for the migration regression tests.

The critical state-engine regression matrix continues to cover Missing detection, root outage/recovery, qBittorrent disappearance, incoming/cancelled torrents, replacements (including edition changes and different-path reappearance), physical duplicates, Plex unavailable/conflict behavior, torrent-archive retention, destructive confirmation safety, season packs, and multi-episode mappings.
