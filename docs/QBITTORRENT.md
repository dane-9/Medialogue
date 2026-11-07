# qBittorrent Setup

Add each qBittorrent instance independently under Settings → qBittorrent.

Configure:

- display name;
- URL;
- username/password when required;
- scope: Movies or Shows;
- optional qBittorrent category/tags;
- polling interval.

The URL may be either the qBittorrent WebUI origin (for example
`http://qbittorrent:8080`) or a reverse-proxy base path (for example
`https://host.example/qbit`). Medialogue preserves that path when calling the
WebAPI.

When testing an existing client, unsaved URL/username/password edits are used
for the test. Leaving Password blank reuses the stored write-only secret.

Authentication failures are reported separately for rejected credentials and
qBittorrent's temporary failed-login IP ban. After a confirmed authentication
failure the scheduler stops automatic retries for that client so it does not
keep triggering qBittorrent's ban protection; use Test connection / Refresh
health after correcting the credentials or clearing the ban.

Medialogue supports multiple instances. If exactly one client is eligible for a selected search result it is used immediately. If several are eligible, the UI asks which one to use.

## Leave-in-place behavior

Medialogue does not send a normal search download through an import/move pipeline. qBittorrent chooses the save location from its own configuration/category. Medialogue observes that location and attaches the completed media where it exists.

Unrelated torrents outside configured/accessible media roots are ignored.

## Cross-seeds

Downloads treats the physical payload as the primary row. Torrents associated
with the same release, reporting the same resolved content path, or proven to
use the same selected hard-linked files are grouped into one expandable row.
The expanded view retains each torrent's tracker, info hash, client, activity,
and upload speed. Actions on the payload row apply to every visible member.

Payload health is aggregated conservatively. One unregistered or unavailable
tracker does not make a cross-seeded release unavailable while another torrent
source remains healthy, degraded, or has not yet produced conclusive terminal
failure evidence. Replace Media is opened only after every torrent associated
with that release has confirmed recent terminal tracker failure.

## Removal

When Medialogue removes an associated torrent as part of an explicitly confirmed workflow, qBittorrent is called without deleting data. Starting a normal Upgrade Media workflow authorizes Medialogue to retire every recognized old cross-seed and delete the superseded media automatically after replacement validation and safety checks. Replace Media retains its separate keep/retire/cleanup choices.

Terminal failures of incomplete torrents submitted by Medialogue follow a
different cleanup path. qBittorrent removes the partial payload when it is
exclusive, but preserves files when another observed torrent reports the exact
same resolved payload path. See [Failed Downloads and Blocklist](FAILED_DOWNLOADS.md).

## Deleting from Downloads

**Delete with files…** (right-click a download) removes the torrent from qBittorrent and deletes its media from disk. A dialog first shows exactly what goes:

- **From disk:** for a movie, the whole folder the movie is in, including files you added yourself such as subtitles. For a show, only the torrent's own season or episode content, never the show folder with its other seasons. For a torrent not in the library, its own content.
- **From qBittorrent:** the selected torrents and every cross-seed of the same files, which would otherwise be left broken.
- **Afterwards:** either **Find a replacement**, which keeps the title and lists it in Replace Media as *Missing*, or **Remove from Medialogue**. Removing a movie takes it out of the library. Removing a show forgets those releases and stops wanting the season; the show itself goes only when nothing of it is left.

Nothing is deleted when the folder is a configured media folder itself, lies outside one, is in a read-only media folder, or also holds another title's media. qBittorrent lets go of the torrents before any file is touched; if it cannot, nothing is deleted. Torrents removed this way are never re-added by **Re-add Missing Torrents**.

## Disaster recovery re-import

The torrent archive preserves qBittorrent recovery context independently of live client presence. After uploading a backup on **Backups → Restore**, Medialogue builds a read-only re-import plan before anything is submitted. See [Backup / Restore](BACKUP_RESTORE.md).

For each archived torrent, the plan verifies the target qBittorrent client and media scope, original qBittorrent save path, remote-path resolution, enabled media folder containment, category, tags, current qBittorrent presence, and captured per-file selection. Only explicitly selected Ready rows are submitted after a second confirmation.

qBittorrent still controls download placement. Medialogue supplies the preserved save path/category/tags and restores captured per-file priorities after adding the archived `.torrent`; it does not import, move, or rename recovered media.
