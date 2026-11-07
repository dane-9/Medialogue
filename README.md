# Medialogue

Medialogue is a dark, desktop-first movie/show inventory and download-management application built around one rule: **media stays exactly where it was downloaded**. It is designed for Docker Compose, uses TMDB as the primary metadata identity source, and treats Plex as secondary verification rather than the source of truth.

## Documentation

Detailed deployment and administration guides are under [`docs/`](docs/README.md): Installation, Upgrade, Backup/Restore, Path Mapping, qBittorrent, Failed Downloads and Blocklist, Plex, Prowlarr/Torznab, Custom Formats, and Troubleshooting.


## TrueNAS SCALE + Dockge

For TrueNAS SCALE with Dockge installed as an App, use the GHCR deployment path rather than building source inside Dockge. GHCR publishing remains an explicit manual workflow; CI runs automatically on pull requests and pushes to `main`. Secrets stay in Dockge's local `.env`, not in the GitHub repository. Follow [`TRUENAS_DOCKGE_GHCR.md`](TRUENAS_DOCKGE_GHCR.md), then paste [`compose.truenas-dockge.yml`](compose.truenas-dockge.yml) into Dockge.

## Quick start

Extract this source release (or clone the repository) into one directory; the supplied Compose file builds the production image from the included Dockerfile, backend, and frontend.

1. Copy `.env.example` to `.env`. Medialogue generates and persists
   `/config/application-secret`; set `MEDIALOGUE_SECRET_KEY` only when an
   operator-managed override is desired.
2. Review `PUID`/`PGID` and the persistent directory paths. The container prepares `/config` and `/torrent-archive` before dropping privileges; media roots are never chowned.
3. Add only the media bind mounts Medialogue should be allowed to inspect to `docker-compose.yml`. The example mounts `/media` read-only.
4. Start the stack:

   ```sh
   docker compose up -d --build
   ```

5. Open `http://localhost:8000`, read the one-time token with
   `docker compose exec media-manager cat /config/initial-setup-token`, and
   complete the First Run administrator setup. The first-run checklist appears
   automatically afterward.
6. Configure only the integrations you use. TMDB is recommended before the first scan because it establishes external identity for newly discovered titles.
7. Finish or skip optional setup items, then explicitly start the first media-folder scan. A newly added folder remains uninitialized and is ignored by automatic file checks until that first scan succeeds.

There is no global Active Operations switch. Integration observations can run automatically, while folder discovery remains opt-in: every newly added media folder requires one explicit successful scan before automatic file checks can use it.

## Local frontend development

```sh
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://localhost:8000`. Run `npm run build` to create the production frontend bundle.

## Storage and persistence

Medialogue only scans media folders explicitly configured in Settings.

- `/config/medialogue.db` is the authoritative SQLite store for library state, history, jobs, Problems, observations, and integration **runtime health**.
- `/config/medialogue.json` is the source of truth for Plex, TMDB, qBittorrent-client, and indexer configuration. It contains no passwords/tokens/API keys.
- `/config/secrets.enc` stores those integration secrets encrypted with AES-GCM using a key derived from `MEDIALOGUE_SECRET_KEY`. Keep the same secret key with the config volume; changing it makes the encrypted secrets unreadable.
- `/config` also stores the setup marker, the Google Drive connection (`backup-settings.enc`), and backup ZIPs while an upload is being read (`/config/uploads`).
- `/torrent-archive` stores archived `.torrent` files and versioned recovery manifests independently from live qBittorrent state.
- Media roots are user-owned filesystem state. Medialogue never renames, moves, copies, hardlinks, reorganizes, or creates metadata sidecars beside media.

The example Compose file mounts `/media` as **read-only**, which is sufficient for discovery, matching, Plex verification, Missing detection, and Problem rechecks. The Problems workflow does not delete duplicate media; remove an unwanted copy outside Medialogue and recheck the Problem.

## Torrent archive and recovery manifests

Every in-scope torrent observed through a configured qBittorrent client is backed up to the dedicated `/torrent-archive` mount while qBittorrent can still export its `.torrent` metadata. Archive failures are non-destructive and are retried on later observations; they are also visible on **Backups → Torrents**.

Archive files are keyed by info hash and sharded to keep directories manageable:

```text
/torrent-archive/
├── torrents/<prefix>/<info-hash>.torrent
└── manifests/<prefix>/<info-hash>.json
```

Recovery manifests are versioned (`schema_version: 1`) and retain the torrent identity, Movie/Show identity, TMDB/TVDB IDs, release name, quality, edition, release group, parser snapshot, historical paths, qBittorrent-client observations, timestamps, and a SHA-256 digest of the archived `.torrent` file. The manifest is deliberately stored in the archive, never beside media.

Torrent backups are retained independently from the logical Movie/Show record. `Remove From App Only`, a replaced release, or a torrent being removed from qBittorrent does not delete the archived `.torrent` or its recovery manifest. When the database association disappears, later manifest refreshes preserve the last known media identity/path evidence already recorded on disk.

**Backups → Torrents** lists every archived torrent and every library release that has none, with whether each is on Google Drive. A Movie detail page also shows its associated torrent history.

The `/torrent-archive` mount should therefore be durable and writable. It is recovery data, not disposable cache.

## Backups

The **Backups** page copies the torrent archive to a **Medialogue Backups** folder in Google Drive within minutes of each change, and the database and configuration on their own schedule. After losing the server, download that folder from Drive, upload it on **Backups → Restore**, and re-add the torrents: each goes back to its original save path, category, tags and file selection, and downloads again.

Files in Drive are not encrypted. Torrent files carry tracker passkeys, and the database copies carry the application secret with the encrypted integration credentials, so keep the folder private.

See [Backup / Restore](docs/BACKUP_RESTORE.md) for setup, what is copied, and the restore steps.

Never replace the SQLite file while Medialogue is running, and do not copy only its `-wal` or `-shm` sidecar files.

## Prowlarr-backed indexers and Interactive Search

Medialogue does not import Prowlarr configuration wholesale. Add each indexer explicitly under **Settings → Indexers** using the Torznab endpoint Prowlarr exposes for that indexer, its API key, a `Movies`, `Shows`, or `Movies + Shows` scope, and an optional per-indexer timeout.

Interactive Search is deliberately manual:

1. Open **Interactive Search** and select a Movie from the Medialogue library.
2. Medialogue creates a persistent search Job and fans the query out concurrently to every enabled `Movies`/`Both` indexer.
3. Results appear progressively as indexers respond. A failed or timed-out indexer is shown explicitly and does not cancel successful results from the others.
4. Release names are run through the same shared parser used by scanning and qBittorrent file matching. Every enabled, scope-eligible Custom Format is evaluated immediately. The target title's effective Quality Profile, per-title overrides, signed score contributions, and minimum-quality warning state are frozen with the result as immutable search-time evidence.
5. Choosing **Download** submits only that result. If exactly one eligible Movie qBittorrent client exists it is used immediately; with multiple eligible clients the UI asks which one to use.

Searching is read-only. Actual qBittorrent submission remains an explicit user action.

Medialogue does **not** pass a save path when a normal Interactive Search result is submitted. The selected qBittorrent instance, its category/tags, and qBittorrent's own configuration decide where the torrent downloads. Medialogue later observes and matches the completed data in place; it never imports or relocates it.

Unselected search results expire after 24 hours. Once a result is submitted, its parser/search/indexer/client evidence is retained as an immutable selection snapshot so later release history can preserve what was chosen at download time. Raw Torznab download URLs/API credentials are never returned to the browser.


## Custom Formats

Custom Formats are persistent, explainable release-matching definitions. A Quality Profile owns each format's base score. Individual match rules may add a relative score offset—for example, one Repack rule detects the numeric revision so REPACK contributes `+1`, REPACK2 contributes `+2`, and so on without creating separate formats.

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

User-defined Custom Format conditions are regular expressions against the full raw release title, case-insensitive by default with an optional case-sensitive setting. Parser-backed structured conditions are reserved for Medialogue's built-in formats and are not part of the user-defined format schema.

When multiple rules match, only the highest matching rule offset is applied. A regex may provide an arbitrary numbered offset with the Python-style named group `(?P<score_offset>\d+)`; otherwise the rule's configured offset is used. The final contribution is the Quality Profile score plus that match offset.

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

The Default profile scores **tiers, not formats**: any codec scores 0; lossless audio (TrueHD, DTS:X, DTS-HD MA, FLAC, PCM) sits within 10 points, below TrueHD Atmos, with small nudges (TrueHD over DTS:X, FLAC over PCM) that only break ties between results; anything with Dolby Vision ranks above anything without (DV HDR10+ › DV HDR10 › DV › HDR10+ › HDR10); SDR or no tag is 0 and HLG is avoided. A new install starts with these; an existing Default profile is moved to them once, for every score still at its original default.

In Upgrade Media, a release with the same quality is an upgrade on Custom Formats alone only when its score gains at least **15**, and **no audio or dynamic-range group gets worse** (DV with worse audio is not an upgrade). A higher quality, a season pack, or a PROPER/REPACK of the same release is an upgrade regardless. When the current release's name carries no audio tag (a bare folder name), its audio is treated as unknown, so a candidate gets no credit for listing one.

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

A Show can be added directly from TMDB or discovered by explicitly scanning a configured **Shows** media folder. Files remain exactly where they already exist; Medialogue never renames, moves, copies, imports, or hardlinks them. Ordinary single-episode names such as `S01E01` are mapped to individual Episode records and each episode keeps its own Present/Missing and monitored state.

Missing detection is episode-aware. If one episode file disappears while the Show directory remains available, only that mapped episode is affected after the configured missing-grace checks. A whole-root outage is still handled as a root-health failure rather than mass-marking episodes Missing.

The Shows UI provides poster cards by default, an optional dense table, expandable Seasons, per-Episode monitoring, quality/path evidence, Plex state, TMDB metadata refresh, and the existing Season/Episode Interactive Search actions. Plex verification for Shows is read-only and checks exact mapped episode paths where possible.

Season packs and multi-episode files are supported without changing the media on disk. A season pack is represented by one shared `ShowRelease` that can satisfy many Episode records while every member file remains in its original qBittorrent/download directory. Files such as `S01E01E02` and `S01E01-E03` map one physical `MediaFile` to multiple logical Episodes.

Mapping is deliberately partial-safe: if a pack or multi-episode filename contains episode numbers that cannot be verified against known season metadata, Medialogue maps the episodes it can prove and leaves the unresolved portion unattached. It does not reject or relocate the rest of the pack. Multiple physical files mapped to the same Episode produce a `DUPLICATE_EPISODE_RELEASE` Problem and are left untouched.

Episode mappings can also be corrected manually from the Show page. This is a database-only logical correction: the media file is not renamed, moved, copied, hardlinked, or modified. A manual mapping is authoritative on future scans, including episode numbers deliberately removed from the parser-derived mapping.

## Problems and duplicate resolution

Medialogue has one persistent **Problems** queue for media evidence that needs attention. Problems are not generic notifications: each reason exposes only the actions that are valid for that evidence. Rechecking a Problem does not fake a resolution; it remains open until a later observation proves the underlying condition changed.

Common reasons include:

```text
PLEX_IDENTITY_MISMATCH
TORRENT_PATH_NOT_FOUND
ROOT_UNREACHABLE
DUPLICATE_PHYSICAL_RELEASE
DUPLICATE_EPISODE_RELEASE
AMBIGUOUS_REPLACEMENT_TARGET
```

The Problems page is organized by the next required action: manual fixes, identity confirmation, configuration, or recheck. It supports explicit TMDB Movie/Show matching, download-path diagnostics, direct Plex-item links, per-Problem rechecks, and rechecking the current active queue. TMDB/manual matching is authoritative for Movie identity. Plex movie titles and years are advisory metadata and never create or block a Problem; Plex only verifies physical presence/path. For Shows, an exact-file season/episode-number disagreement can still surface because it indicates a real episode-mapping issue. Identity correction changes database metadata only; it never renames or relocates media.

### Movie physical duplicates

A `DUPLICATE_PHYSICAL_RELEASE` is deliberately left untouched. Review the paths and quality evidence, delete the unwanted copy using your normal filesystem tools, then recheck the Problem. Medialogue closes it only after current filesystem evidence proves that one physical copy remains.

### Episode duplicates

`DUPLICATE_EPISODE_RELEASE` follows the same rule: delete the unwanted file outside Medialogue, then recheck. Medialogue does not choose a preferred mapping while both physical files exist.

### Path mapping problems

Remote qBittorrent path mappings can be configured under **Settings → Media Folders**. The UI stores the reported remote prefix and its container-visible local prefix, optionally scoped to one qBittorrent client and one media folder. Medialogue never guesses a path translation. After adding or correcting a mapping, recheck the affected Problem.

Media folders can be described as **Read-only** or **Read/write** in the UI, but file checks and Problem handling require only read access. Medialogue does not use Problems to delete media.

## Tags and bulk Movie administration

Movie tags are lightweight application metadata stored in SQLite. They are **Movies-only in v1** and never create sidecars or alter media paths. Tags can be created once, assigned to any number of Movies, removed independently, and clicked in the Movie UI to filter the library.

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

Bulk Plex rechecks perform live read-only integration work without a global operations switch. Tag/profile/monitoring edits and parser/Custom Format re-evaluation are logical database operations and do not mutate the filesystem. No bulk action can move, rename, copy, import, hardlink, or reorganize media.

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

SSE is an update/invalidation channel, not the sole source of truth. If the browser disconnects or refreshes, current Jobs, Problems, health, and durable Events are reloaded from SQLite through the REST API.

## Hardening and manual regression coverage

Part 19 adds release-readiness hardening around the existing state engine instead of changing Medialogue's leave-in-place behavior.

API documentation is available to an authenticated administrator at:

```text
/api/docs
/api/redoc
/api/openapi.json
```

These endpoints are intentionally not public. API responses also receive conservative browser security headers, and API responses are marked `no-store` where appropriate.


The FastAPI schema is also the frontend transport contract. `openapi/openapi.json` is a checked-in snapshot generated directly from `app.openapi()`. The frontend generation pipeline uses pinned `openapi-typescript` output from that snapshot, and `openapi/contract-manifest.json` records the expected hashes of both generated contract files. Regenerate the contract after changing a request or response schema:

```text
python scripts/generate_api_contract.py
```

The generated TypeScript lives under `frontend/src/api/generated/` and should not be edited by hand. `openapi-fetch` consumes its `paths` contract so every frontend API operation is checked for the HTTP method, path parameters, query parameters, request body, and response shape. The shared transport middleware preserves Medialogue's session-cookie, CSRF, and API-error behavior; there is no parallel handwritten request transport.

The handwritten API layer is organized by subsystem under `frontend/src/api/modules/`, while DTO-to-view-model transformations live under `frontend/src/api/mappers/`. UI callers import the subsystem APIs they use directly.

Frontend view models may still differ from transport DTOs; small mapping functions are used where the UI intentionally needs different defaults or representation. Runtime validation is deliberately limited to trust boundaries instead of re-validating every normal FastAPI response in the browser. Custom Format JSON imports are validated before submission and are validated again by Pydantic on the backend; structured `job.status` SSE payloads are validated before the UI uses their fields; TMDB and qBittorrent adapters validate external JSON structure before mapping it into Medialogue models.

Run the complete verification suite manually from the repository root before a release or after a substantial refactor:

```text
./scripts/verify.sh
```

The command verifies Python compilation, both the OpenAPI snapshot and generated TypeScript contract, the backend pytest suite, frontend Vitest tests, and a strict TypeScript/production Vite build. It fails instead of silently skipping checks when dependencies are missing and prints the install command to fix the environment. Use `./scripts/verify.sh --docker` when you also want Compose validation and a production container build. Set `PYTHON=/path/to/python` if the backend environment uses a non-default interpreter. `scripts/release-check.sh` delegates to the same verification command so there is only one local quality-gate definition.

Authentication maintenance is stricter as well: expired sessions are pruned during login, and changing the administrator password revokes other active sessions while keeping the session that performed the password change alive. CSRF tokens remain bound to the session that issued them.

Destructive duplicate-resolution confirmations are HMAC-signed, short-lived, and tied to the exact reviewed targets. Tampered or expired confirmation tokens are rejected before any filesystem or qBittorrent operation occurs, and the existing fresh-inventory check still rejects a preview whose underlying directory contents changed.

The Movies and Shows card/table view preference is persisted locally in the browser. Multi-selection and right-click behavior are implemented through deterministic UI-state helpers so selecting, context-clicking, duplicate comparison, Problem filtering, search warnings, and Custom Format regex-condition behavior can be regression-tested without relying on visual/manual testing alone.

Production and tests both use SQLite through `aiosqlite`. Alembic exercises both the asynchronous production URL and synchronous SQLite URLs used for migration regression checks.

The critical state-engine regression matrix continues to cover Missing detection, root outage/recovery, qBittorrent disappearance, incoming/cancelled torrents, replacements (including edition changes and different-path reappearance), physical duplicates, Plex unavailable/presence and Show episode-number conflict behavior, torrent-archive retention, destructive confirmation safety, season packs, and multi-episode mappings.
