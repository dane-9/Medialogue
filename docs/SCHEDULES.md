# Schedules and Polling

Medialogue separates cheap observation from full library discovery.

## Tasks

System → Tasks shows available backend work and the shared execution queue.
Scheduled work is owned by the server and continues when no browser is open.
The table records the last execution, duration, next execution, and current
queued/running state. Administrators can also enqueue a task immediately from
this page without changing its normal interval. The same page contains active
and recent background executions, their progress and errors, and cancellation
controls where supported. Configurable tasks use an interval selector; choosing
**Manual only** disables automatic execution without disabling **Run now**.
There is no separate Jobs interface; jobs remain the
durable execution records behind Tasks.

The **Check Integration Health** task runs every minute by default. It performs lightweight,
read-only checks for configured Plex, TMDB, Torznab indexers, media folders, and
the torrent archive. qBittorrent's normal observation poll remains its health
probe and is deliberately not invoked by the health sweep. Saving any
integration or media-folder configuration makes the health task due
immediately; a database uniqueness guard collapses simultaneous manual,
configuration-triggered, and scheduled requests into one execution.

**Verify Known Media Files** checks only initialized roots and paths already in
the database. It never discovers a new folder. Root outages are recorded as
health failures and do not advance missing-file grace counters.

**Full Media Folder Scan** runs the existing recursive root reconciliation for
each enabled, initialized media folder. It is Manual only by default, excludes
newly configured roots until their first explicit scan, and shares the same
per-root non-overlap protection as the media-folder Scan now action.

The fixed daily **Housekeeping** task prunes high-volume scheduler history,
expired unselected search results, and expired recovery exports. Successful
scheduler executions are retained for seven days and failed/interrupted ones
for thirty days. Jobs that own other user-workflow evidence are not pruned.

Health changes are published to the UI as live events. Integration Settings do
not need to be left open, and Plex recovery does not require a manual refresh.

## qBittorrent polling

Each qBittorrent client has its own polling interval. Settings → Schedules exposes the same real interval used by the background qBittorrent observer. The supplied presets range from seconds to minutes.

Polling observes torrent state and feeds file matching. It does not move, rename, copy, hardlink, or delete media.

The observer checks qBittorrent whenever an enabled client is due. Newly added media folders are excluded until their first explicit successful scan.

Full scans remain explicit and configurable. Selecting an interval opts into
recurring scans; selecting Manual only returns them to on-demand operation.
