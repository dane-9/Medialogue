# System Status

## Purpose

Medialogue should have a dedicated **Status** page for application-wide health. It belongs in the System navigation beside Tasks, Events, and Settings.

The page answers three questions:

1. Is Medialogue able to perform its core work?
2. If not, what is affected and where should the administrator fix it?
3. When was the condition last checked, and has it recovered?

Status is not another media queue. Problems remains responsible for issues tied to a particular movie, show, episode, file, release, or torrent. Status is responsible for integrations, storage, configuration, scheduled work, security, and the Medialogue runtime itself.

## Navigation and terminology

Add **Status** to the System navigation:

1. Status
2. Tasks
3. Events
4. Settings

Use **Status**, not **System**, as the page label. The surrounding navigation already establishes that it is a system page, and Status describes what the user will find there.

The Status navigation item may show a small severity dot when an active warning or error exists. It should not show a numeric badge. Tasks should continue to have no running-task badge.

The existing Plex, qBittorrent, Storage, and Indexers rows above the administrator account remain the quick integration overview. Clicking those rows should continue to open the relevant settings page. Their tooltips should continue to show the configured instances and their current health. They do not need to become links to Status.

## Status versus Problems

| Condition | Destination | Reason |
| --- | --- | --- |
| A configured media folder is unavailable | Status | The whole application capability is affected. |
| One completed torrent cannot be resolved to a local path | Problems | The condition belongs to a specific torrent or media item. |
| A remote path mapping points to a missing local directory | Status | The mapping configuration is globally invalid. |
| Plex identifies a particular title differently | Problems | The conflict belongs to that title. |
| Plex is enabled but cannot be reached | Status | Plex verification is unavailable application-wide. |
| Every movie indexer is unavailable | Status | Movie searching is unavailable globally. |
| A duplicate movie or episode file exists | Problems | The user must resolve a specific media conflict. |
| A scheduled task repeatedly fails | Status | Background application behavior is unhealthy. |

`ROOT_UNREACHABLE` currently behaves like a system condition even though it is represented as a Problem. Once Status owns storage availability, new root outages should be raised there and existing open root-outage Problems should be resolved or retired during migration. `TORRENT_PATH_NOT_FOUND` should remain a Problem because it refers to a specific observed torrent.

## Page structure

### Summary

The top of the page should show:

- Overall state: Healthy, Attention needed, or Unhealthy.
- Number of active errors and warnings.
- Time of the most recent completed status evaluation.
- A **Check now** action that queues the existing integration-health sweep extended with system checks.

Do not show a progress bar after a check reaches a terminal state. While a check is active, show a compact Running badge without changing the width of the state column.

### Active notices

Active notices are the primary content and should appear first. Errors precede warnings, followed by informational notices. Within a severity, older conditions appear first.

Each notice should contain:

- Severity icon and color.
- Plain-language title.
- One-sentence consequence.
- Affected component or configured instance.
- First detected time.
- Last checked time.
- A direct action such as **Open qBittorrent settings**, **Open media folders**, or **View task**.
- An optional **Check again** action when that individual check can run safely.

Notices should not be dismissible initially. A health condition should clear because the underlying condition recovered, not because it was hidden. Muting can be considered later if genuinely noisy checks emerge.

### Components

Below active notices, show the current state of the major components:

- Metadata: TMDB.
- Download clients: each qBittorrent instance.
- Search: each indexer.
- Storage: each media folder.
- Optional verification: Plex, only when enabled.
- Background work: scheduler and recent task health.
- Data protection: database, application-data directory, and torrent archive.

Healthy components should be visually quieter than active notices. This section is evidence that a check ran successfully, not a dashboard competing for attention.

### System information

The bottom section should contain diagnostic facts rather than warnings:

- Medialogue version.
- Uptime and start time.
- Operating system and whether Medialogue is running in a container.
- Database backend and SQLite version where applicable.
- Current migration revision.
- Application-data path and torrent-archive path.
- Server timezone and current server time.

Never display passwords, API keys, tokens, cookie values, or connection strings. URLs should be redacted when they contain user information or query credentials.

## States and severity

The page-level state is derived from active notices:

| State | Rule |
| --- | --- |
| Healthy | No active warnings or errors. Informational notices may exist. |
| Attention needed | One or more warnings, but no errors. |
| Unhealthy | One or more active errors. |

Individual checks use three visible severities:

- **Error:** a core configured capability is unavailable or data safety is at risk.
- **Warning:** functionality is degraded, partially unavailable, or likely to fail.
- **Info:** an intentional configuration limits automation but does not make the system unhealthy.

An optional integration that is disabled is not unhealthy. For example, disabled Plex should produce no Plex notice and should remain absent from the sidebar health rows.

## Initial health checks

### Storage and paths

| Check identifier | Condition | Severity | Action |
| --- | --- | --- | --- |
| `STORAGE_ROOT_UNAVAILABLE` | A configured media folder cannot be accessed. | Error | Open Media settings. |
| `NO_STORAGE_ROOTS` | Setup is complete but no media folders exist. | Warning | Add a media folder. |
| `REMOTE_MAPPING_LOCAL_MISSING` | A remote path mapping's Medialogue prefix no longer exists or is not a directory. | Error | Edit the mapping. |
| `REMOTE_MAPPING_ORPHANED_CLIENT` | A mapping refers to a qBittorrent client that no longer exists. | Warning | Edit or remove the mapping. |
| `REMOTE_MAPPING_ORPHANED_ROOT` | A mapping was scoped to a removed media folder. | Warning | Edit or remove the mapping. |
| `APP_DATA_NOT_WRITABLE` | Medialogue cannot write its application-data directory. | Error | Show the affected path and documentation link. |
| `TORRENT_ARCHIVE_UNAVAILABLE` | The configured torrent archive cannot be read or written. | Error when archive protection is required; otherwise Warning. | Open archive/recovery settings. |
| `LOW_APPLICATION_DISK_SPACE` | Free space for application data or the archive is below a safe threshold. | Warning, escalating to Error at a critical threshold. | Show the affected volume. |

Media-folder access checks must respect Medialogue's configured access mode. A read-only media folder is healthy when it can be read; it must not be reported unhealthy merely because it is not writable.

### qBittorrent

| Check identifier | Condition | Severity | Action |
| --- | --- | --- | --- |
| `NO_DOWNLOAD_CLIENT_FOR_MOVIES` | Movies are configured but no enabled Movies/Both client exists. | Warning | Open qBittorrent settings. |
| `NO_DOWNLOAD_CLIENT_FOR_SHOWS` | Shows are configured but no enabled Shows/Both client exists. | Warning | Open qBittorrent settings. |
| `DOWNLOAD_CLIENT_UNAVAILABLE` | One enabled client repeatedly fails health checks. | Warning | Open that client. |
| `ALL_DOWNLOAD_CLIENTS_UNAVAILABLE` | Every enabled client for an active media type is unavailable. | Error | Open qBittorrent settings. |

A single transient timeout should update the component's last-check result but should not immediately create a persistent warning. Raise availability notices after repeated failures or a short sustained outage. Clear them after a successful authenticated check.

### Indexers

| Check identifier | Condition | Severity | Action |
| --- | --- | --- | --- |
| `NO_SEARCH_INDEXER_FOR_MOVIES` | No enabled Movies/Both indexer supports interactive movie search. | Warning | Open Indexers settings. |
| `NO_SEARCH_INDEXER_FOR_SHOWS` | No enabled Shows/Both indexer supports interactive show search. | Warning | Open Indexers settings. |
| `INDEXER_UNAVAILABLE` | One enabled indexer repeatedly fails. | Warning | Open that indexer. |
| `ALL_INDEXERS_UNAVAILABLE` | All enabled indexers for an active media type are unavailable. | Error | Open Indexers settings. |

If RSS discovery is implemented, its availability should be evaluated separately from interactive search. An indexer with RSS disabled must not be treated as an RSS failure.

### Metadata and Plex

| Check identifier | Condition | Severity | Action |
| --- | --- | --- | --- |
| `TMDB_NOT_CONFIGURED` | New-media matching is enabled but TMDB credentials are missing. | Warning | Open TMDB settings. |
| `TMDB_UNAVAILABLE` | TMDB is enabled but repeatedly unavailable. | Error when unmatched New Media is waiting; otherwise Warning. | Open TMDB settings. |
| `PLEX_UNAVAILABLE` | Plex is enabled and configured but cannot be reached. | Warning | Open Plex settings. |
| `PLEX_CONFIGURATION_INCOMPLETE` | Plex is enabled but its URL or token is incomplete. | Warning | Open Plex settings. |

Plex notices must disappear completely when Plex is disabled. A Plex identity disagreement remains a media-specific Problem.

### Scheduler and background work

| Check identifier | Condition | Severity | Action |
| --- | --- | --- | --- |
| `SCHEDULE_OVERDUE` | An enabled schedule has not started within its expected grace period. | Warning | Open Tasks. |
| `SCHEDULE_REPEATED_FAILURE` | The same scheduled task fails repeatedly. | Warning, escalating to Error when the task provides a core capability. | Open the task and recent execution. |
| `AUTOMATIC_MEDIA_DISCOVERY_DISABLED` | Full Media Folder Scan is Manual only. | Info | Open Tasks and choose an interval. |
| `HEALTH_SWEEP_STALE` | No health sweep has completed within twice its configured interval plus grace. | Warning | Open Tasks. |

Do not raise a warning simply because Plex sync is Manual only; Plex itself is optional. A manually disabled media-folder scan is worth an informational notice because it explains why new files are not appearing automatically.

### Security and runtime

| Check identifier | Condition | Severity | Action |
| --- | --- | --- | --- |
| `DEFAULT_ADMIN_CREDENTIALS` | The administrator still uses the shipped/default credentials. | Warning | Open account/security settings. |
| `DATABASE_UNAVAILABLE` | A database health query fails. | Error | Show safe diagnostic guidance. |
| `DATABASE_MIGRATION_MISMATCH` | The database revision does not match the running application. | Error | Link to upgrade guidance. |
| `UNSUPPORTED_SQLITE_VERSION` | SQLite is below Medialogue's supported minimum. | Error | Link to installation guidance. |
| `SYSTEM_TIME_INCORRECT` | Server time differs substantially from a trusted external response. | Warning or Error depending on drift. | Explain authentication and scheduling impact. |

System-time validation can be deferred until there is a reliable trusted time source already contacted by Medialogue. It should not introduce a new mandatory cloud dependency solely for this check.

## Checks not worth copying from Radarr

Do not add checks merely because Radarr has them. The following do not fit Medialogue's current architecture:

- Completed-download removal behavior.
- Import mechanisms, hardlinks, atomic moves, and renaming formats.
- Recycling-bin configuration.
- Import lists and collection root folders.
- Notification providers that Medialogue does not support.
- Radarr release branches, package updates, and updater permissions.
- Metadata consumers and sidecar generation that Medialogue does not perform.
- Radarr-specific deprecated settings.

These can only become relevant if Medialogue later adopts the corresponding feature.

## Evaluation and lifecycle

Use one central health evaluator with small checks grouped by concern. A check returns either healthy or one or more scoped notices. The evaluator should run:

- At application startup after migrations complete.
- During the scheduled integration-health sweep.
- After relevant settings are saved.
- After an integration health result changes.
- After a scheduled task reaches a terminal state.
- When a media folder or mapping is added, changed, or removed.

Each active notice needs a stable identity composed of:

- Check identifier.
- Component type.
- Component identifier or logical scope, such as `movies`.

This prevents duplicate notices when the same condition is detected repeatedly.

Persist at least:

- Stable notice identity.
- Severity.
- Title and explanation key.
- Structured diagnostic details.
- First detected time.
- Last detected/check time.
- Resolved time.

On each evaluation, update matching active notices, create newly detected notices, and resolve notices no longer returned by the check. Emit Events when a notice is opened, changes severity, or resolves. Do not emit an Event for every repeated failed poll.

### Flapping control

Configuration errors and missing local directories can be raised immediately. Network integrations should generally require either:

- Two or three consecutive failures, or
- A sustained failure window of approximately five minutes.

One successful authenticated check may resolve an integration outage. Preserve the original first-detected time across repeated failures so the page communicates outage duration accurately.

## API shape

The frontend should receive one aggregate status document containing:

- Overall state and severity counts.
- Last evaluation time.
- Whether an evaluation job is active.
- Active notices.
- Current component results, including healthy components.
- Redacted system information.

Recommended endpoints:

- `GET /api/v1/system/status` — aggregate current state.
- `POST /api/v1/system/status/check` — queue a complete status evaluation and return its job identifier.

History does not require a separate endpoint initially. The Events page can filter health notice opened, changed, and resolved events.

Notice actions should be structured internal destinations, not arbitrary URLs or executable backend actions. For example, an unavailable indexer may return an action targeting the Indexers settings tab and that indexer's identifier.

## Frontend behavior

- Update from the existing server-sent event stream when health notices or evaluation jobs change.
- Use periodic polling only as a fallback.
- Keep relative times live using the local one-second clock without requesting the API every second.
- Preserve table/card dimensions when a check moves between Running and a terminal state.
- Make notice titles and consequences understandable without exposing internal error codes.
- Put raw diagnostic details behind an expandable area or copy-details action.
- Make every fix action keyboard accessible.
- On narrow screens, show notices as stacked cards instead of forcing a wide table.

The Problems count and Status severity indicator are independent. A healthy Status page does not imply that the media library has no Problems, and an empty Problems queue does not imply that integrations are healthy.

## Delivery phases

### Phase 1: foundation

- Add Status navigation and page shell.
- Add the persistent notice model and aggregate API.
- Add open/change/resolve lifecycle and Events.
- Extend the existing health sweep to run system checks.
- Implement storage-root, qBittorrent, indexer, TMDB, and Plex checks.

### Phase 2: configuration and background work

- Add remote path mapping validation checks.
- Add scheduler overdue and repeated-failure checks.
- Add automatic-discovery informational notice.
- Move root-wide outage reporting from Problems to Status.

### Phase 3: runtime and security

- Add application-data, torrent-archive, database, migration, disk-space, and default-credential checks.
- Add the redacted System information section.

### Phase 4: refinement

- Tune sustained-failure and severity thresholds using real operation data.
- Add per-notice recheck where useful.
- Add diagnostic detail copying and documentation links.
- Consider optional muting only if unavoidable noisy conditions are identified.

## Acceptance criteria

The first complete release is ready when:

- An administrator can understand every active notice without reading logs.
- Every notice links to the most relevant place to fix it.
- Optional disabled integrations do not produce warnings.
- Transient network failures do not create noisy persistent notices.
- Recovered conditions clear automatically and generate one resolution Event.
- No system-wide integration or storage outage is duplicated across multiple media Problems.
- Secrets never appear in Status API responses, Events, or copied diagnostics.
- The sidebar, Status page, Tasks page, and integration settings agree on current health.
- Status checks continue to work without an open browser session.

