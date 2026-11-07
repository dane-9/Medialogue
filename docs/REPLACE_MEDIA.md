# Replace Media

**Replace Media** lists every title that could not be restored from the torrent backup if your media drives were lost. A title can come back only if one of its torrents is known, still registered with its tracker, and seeded by someone else. Replace Media shares candidate comparison and download handoff with Upgrade Media, but an equivalent release qualifies even when it is no upgrade.

## When a title appears here

Each title is listed with one reason:

| Reason | When |
|---|---|
| **Not in qBittorrent** | Its torrent left qBittorrent more than 24 hours ago, or no client has it on record, so nothing can check its tracker or swarm. With an archived copy, the **Re-add Missing Torrents** task normally puts it back first (see below), so a title is listed here when it has no archived copy (the row says *not archived* and is searched like any lost source), when the task could not re-add it, or when it was removed again after the task re-added it. **Re-add** on the row puts it back by hand. |
| **Files missing** | The task re-added the torrent, but qBittorrent's check found the files on disk incomplete, so it was left stopped rather than downloaded again. Put the files back and recheck it in qBittorrent, or search for a new torrent. |
| **No other seeders** | For 14 days the swarm has reported no seeder besides this client. Unknown counts never start or stop the clock, and a paused torrent's own copy is not counted as another seeder. |
| **Tracker not responding** | Every tracker of the torrent has answered with an error for 7 days. A tracker that was never contacted, as a paused torrent's is, does not count. |
| **Removed from tracker** | Every tracker confirmed, twice in a row, that the torrent is gone ("unregistered", "deleted", "not registered", "not found", "trumped", "dupe of", or a message starting "Removed"). The verdict is kept after the torrent is deleted from qBittorrent. |
| **No torrent** | The files were found on disk and never linked to a torrent. These are listed but not counted in the sidebar badge. |

Evidence from a live client must be recent: a client that has stopped reporting, or an offline qBittorrent, never lists anything. A title with an upgrade under way is left to Upgrade Media, and so is a title with a new download for it in qBittorrent, such as a new version added there by hand after removing the old torrent: it is neither listed nor re-added until that download finishes and replaces the old release, or leaves qBittorrent.

Cross-seeded releases are judged as one payload: a release is fine while **any** of its torrents is fine. When a title's releases fail in different ways, the cheapest fix is shown first, in the order of the table.

Titles whose torrent is beyond saving (**Removed from tracker**, **Tracker not responding**, **No other seeders**) belong to Replace Media alone: their search accepts anything equal or better, so Upgrade Media hides them unless an upgrade is already running. Ignoring one hands it back. **Not in qBittorrent**, **Files missing** and **No torrent** titles stay in Upgrade Media too.

Ignoring is tied to the reason: if the same title later fails for a different reason, it comes back.

## Re-adding missing torrents automatically

The **Re-add Missing Torrents** task (Tasks, hourly, on by default) puts every torrent listed as **Not in qBittorrent** back from the archive when its .torrent is archived:

1. It is added to the client and save path it had, **stopped**, with its saved file selection, and qBittorrent checks the files already on disk.
2. On a later run, once the check has finished: complete files are started again (unless the torrent was stopped before it left), and the usual tracker and swarm checks resume. Incomplete files leave it stopped and list the title as **Files missing**. Nothing is ever downloaded again.
3. Each torrent is re-added **once**. Removing it from qBittorrent again is taken as deliberate: the title is listed as **Not in qBittorrent** and left to you.

Turning the task off in Tasks stops all of this; the **Re-add** button still works.

## The page

Rows are grouped by media folder, under four tabs:

- **All**: every title that could not be restored and is not ignored;
- **In progress**: replacements downloading or being validated, with progress, speed and ETA;
- **Needs attention**: replacements waiting for a decision, and failed attempts with their reason;
- **Ignored**: titles you set aside. **Return to queue** brings one back.

The **Reason** rail on the left lists each reason with titles in the current tab, with its count. Click one to switch it off or back on, in any combination; **All** turns every reason back on. The folder counts and **Select all** follow the filter, and this browser remembers the choice. Replacements already under way have no reason and always show.

Each row shows why it is here. For **Removed from tracker**, a red **Indexer** pill names the tracker with its message and when it last worked; hover it for every tracker of the torrent. Otherwise a **Why** pill names the reason and when it started; hover it for what that means. Beside it are the current release and the best replacement found, with their Custom Format scores (hover a score for the breakdown under the title's Quality Profile), size, size change and seeders.

**History** lists finished replacements: what was replaced, by what, and how it ended.

## Finding a replacement

Each title is searched automatically when it first appears here: **Search New Replace Media Titles** in **Tasks** runs every 15 minutes (on by default) and searches every title that has never been searched, keeping the best equal-or-better release in the Replacement column. To search again, use **Search** on a row, **Search all**, or turn on **Search for Media Replacements**, which re-searches every title on a schedule. Ignored titles, and titles only missing from qBittorrent with an archived copy, are left out. Searches never start downloads.

A candidate must match the title, the movie's year and edition or the show's season, meet the Quality Profile's quality and Custom Format minimums, keep or improve the current quality, and score at least as high as every release it replaces under the current profile. Results known to have no seeders are left out. Torrents already in the library are excluded by hash, checked again after the torrent file is fetched.

Show replacements use season packs and keep the aired-season and episode-coverage checks.

## Downloading

Select rows and choose **Download**. Pick the qBittorrent client for movies and for shows, then confirm. Each replacement is submitted on its own: one that is rejected is reported by title and does not stop the rest. Eligibility and the profile are checked again before submission, and another running upgrade or replacement for the same release blocks a second one.

## Finishing

The old torrent, its archive and the media stay untouched while the replacement downloads and is validated. The replacement must become the title's validated release and have its torrent archived. For shows, episode coverage is checked.

Once validated, **Resolve** offers:

- **Retire old torrents**: remove only the old qBittorrent entries, keeping their files;
- **Delete old files**: preview and confirm deleting the old media. This needs the old torrent entries removed first, a writable media folder, and checks that no other release uses the files;
- **Keep and finish**: close it and keep the old media, torrents and backups.

A failed attempt appears under **Needs attention** with its reason, and can be started again.
