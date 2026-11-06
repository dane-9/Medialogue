# Failed Downloads and Blocklist

Medialogue handles terminal failures for incomplete torrents that it submitted
from Interactive Search or Upgrade Media. A stalled download, a temporary
tracker warning, or a torrent that Medialogue did not submit is not treated as
a failed download.

qBittorrent also uses `error` and `missingFiles` for transient conditions such
as a full disk or a storage mount that briefly disappeared. Medialogue therefore
only acts once an incomplete submitted torrent has stayed in one of those
states for 15 minutes; if it recovers sooner, nothing is removed or blocked.
Once the state has persisted, Medialogue:

1. records an immutable `download.failed` Event;
2. adds the release to the Blocklist for that specific Movie, Season, or
   Episode;
3. removes the failed torrent through qBittorrent; and
4. deletes its incomplete payload through qBittorrent when that payload is not
   shared by another observed torrent.

If another present torrent reports the exact same resolved payload path,
Medialogue treats it as a cross-seed. The failed torrent is removed with
`delete_files=false`, leaving the shared files and surviving torrent intact.
Otherwise it is removed with `delete_files=true`, allowing qBittorrent to clean
up the partial download. A failed removal is retried by later polls without
creating duplicate Blocklist entries or failure Events.

This path never removes completed media. Completed-media retirement remains
part of the separately validated Upgrade Media or Replace Media workflow.

## Reviewing and unblocking

Open **Blocklist** from the main navigation to see the affected media, failed
release, indexer, quality, reason, and failure time. Entries can be unblocked
individually, in a selected batch, or all at once. Unblocking makes a release
eligible for future searches again; it does not delete its Event history.

Interactive Search and Upgrade Media still display a matching blocked release
with its reason, but prevent selecting it, and upgrade searches never propose a
blocked release as the upgrade candidate. A failed upgrade download is moved to
attention with the failure reason, and the current media is kept. Use **Search alternatives** from a Movie blocklist
entry to return to the normal review flow. Medialogue does not automatically
grab a replacement.
