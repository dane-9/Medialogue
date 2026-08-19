# Schedules and Polling

Medialogue separates cheap observation from full library discovery.

## qBittorrent polling

Each qBittorrent client has its own polling interval. Settings → Schedules exposes the same real interval used by the background qBittorrent observer. The supplied presets range from seconds to minutes.

Polling observes torrent state and feeds reconciliation. It does not move, rename, copy, hardlink, or delete media.

The observer only performs active qBittorrent reconciliation while **Active Operations** is enabled. A fresh application process always starts with Active Operations off.

## Full storage-root scans

Full recursive Movie/Show discovery remains manual in v0.1.0. Use **Scan now** on a Storage Root when you explicitly want discovery/reconciliation of that root.

The Settings → Schedules page deliberately does not present a cron control for full scans because no hidden scheduled root-scan job is enabled in this release.

## Future scheduled jobs

The database contains a generic schedule model so additional opt-in interval/cron jobs can be added without changing the core leave-in-place model. A future scheduled scan feature must remain explicit, configurable, non-overlapping, and subject to Active Operations rather than silently enabling itself after upgrade.
