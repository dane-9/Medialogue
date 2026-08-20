# v7 Runtime Stability Fixes

This maintenance build addresses failures observed during the first real TrueNAS SCALE deployment.

## Storage scans and Job cancellation

Storage-root scans are now real application runtime tasks instead of request-scoped FastAPI background work.

Changes:

- a scan Job is committed before work starts;
- `queued -> running` is committed immediately;
- progress is committed after every reconciled directory;
- a second Scan click for the same root returns the already-active Job instead of creating another queued scan;
- Cancel commits `cancelled` immediately and cancels the live asyncio task;
- queued/running jobs left by an application restart become `interrupted` on startup;
- authenticated GET requests no longer update `AuthSession.last_seen_at`, removing needless database writes and lock contention;
- job SSE status is published after durable commits so a browser cannot receive `running` and then immediately read stale `queued` state.

Filesystem enumeration itself runs in a worker thread. Cancellation stops further Medialogue reconciliation/database work immediately; an already-running directory enumeration may finish its read-only walk before the worker thread exits.

## qBittorrent health and poll isolation

qBittorrent connectivity health is now separate from Medialogue processing health.

After qBittorrent successfully returns its torrent list, Medialogue commits:

- health status;
- last successful connection time;
- health-check time;
- latency;
- connection error state.

A parser, reconciliation, archive, or other application-side error no longer makes a reachable qBittorrent server appear offline.

Each torrent is processed inside a database SAVEPOINT. One malformed or unexpected torrent can fail without preventing later torrents from the same qBittorrent instance from being processed in that poll.

The qBittorrent Settings UI now exposes connection diagnostics instead of only a generic healthy/unhealthy label.

Database migration `0011_download_client_health_diagnostics` adds the required diagnostic columns.

## Custom Formats on plain LAN HTTP

The Custom Formats page previously used `crypto.randomUUID()` while rendering. That API is not guaranteed to be available in an insecure browser context such as `http://192.168.x.x:8010`, causing React to crash the page.

Condition IDs now use a browser-safe ID helper that falls back when `crypto.randomUUID()` is unavailable.

A page-level React Error Boundary has also been added so an unexpected page exception shows a recoverable error panel instead of blanking the entire Medialogue UI.

## Problems request/log spam

The sidebar no longer downloads the full Problems collection merely to display the open count.

New endpoint:

```text
GET /api/v1/problems/count?status=open
```

The frontend:

- loads the count once;
- adjusts it directly from `problem.created` / `problem.resolved` SSE events;
- performs only a five-minute REST fallback refresh.

Uvicorn access logging is disabled by default in the production container to keep routine polling traffic out of the terminal. Set:

```text
MEDIALOGUE_ACCESS_LOG=1
```

if request access logs are needed for debugging.

Routine `httpx` / `httpcore` request logging is also reduced to warnings/errors.

## Validation

The backend currently collects 164 tests. In this build:

- 162 non-PostgreSQL tests pass in local grouped runs;
- 2 PostgreSQL-only tests are intentionally skipped locally when `MEDIALOGUE_TEST_POSTGRES_URL` is unavailable and remain enabled in the manual GitHub CI PostgreSQL 16 job;
- Python compilation passes;
- frontend TypeScript typechecking passes;
- frontend source transpilation passes.

The manual GitHub workflows remain manual-only. Uploading/pushing source does not automatically run CI or publish a GHCR image.
