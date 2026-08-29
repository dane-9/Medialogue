# Schedules and Polling

Medialogue separates cheap observation from full library discovery.

## Tasks

System → Tasks shows recurring backend work and the shared execution queue.
Scheduled work is owned by the server and continues when no browser is open.
The table records the last execution, duration, next execution, and current
queued/running state. Administrators can also enqueue a task immediately from
this page without changing its normal interval. The same page contains active
and recent background executions, their progress and errors, and cancellation
controls where supported. There is no separate Jobs interface; jobs remain the
durable execution records behind Tasks.

The **Check Integration Health** task runs every minute. It performs lightweight,
read-only checks for configured Plex, TMDB, Torznab indexers, media folders, and
the torrent archive. qBittorrent's normal observation poll remains its health
probe. Saving any integration or media-folder configuration makes the health
task due immediately; duplicate requests collapse into a single execution.

Health changes are published to the UI as live events. Integration Settings do
not need to be left open, and Plex recovery does not require a manual refresh.

## qBittorrent polling

Each qBittorrent client has its own polling interval. Settings → Schedules exposes the same real interval used by the background qBittorrent observer. The supplied presets range from seconds to minutes.

Polling observes torrent state and feeds file matching. It does not move, rename, copy, hardlink, or delete media.

The observer checks qBittorrent whenever an enabled client is due. Newly added media folders are excluded until their first explicit successful scan.

## Full storage-root scans

Full recursive Movie/Show discovery remains manual in v0.1.0. Use **Scan now** on a media folder when you explicitly want to discover and match its contents.

The Settings → Schedules page deliberately does not present a cron control for full scans because no hidden scheduled root-scan job is enabled in this release.

## Future scheduled jobs

The scheduler and durable job queue can host additional opt-in interval/cron
jobs without changing the core leave-in-place model. A future scheduled scan
feature must remain explicit, configurable, and non-overlapping. It must also
continue to exclude uninitialized roots rather than silently scanning a newly
configured path.
