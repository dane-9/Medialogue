# Replace Media

**Replace Media** finds a new source for media whose torrents were removed by their trackers, such as an "unregistered torrent" or "torrent deleted" message. It shares candidate comparison and download handoff with Upgrade Media, but an equivalent release qualifies even when it is no upgrade.

## When a title appears here

Every recently observed qBittorrent client must report only confirmed terminal tracker errors for the torrent, and both the tracker evidence and the client observations must be from the last two hours. Timeouts, authentication failures, a client that was not inspected, and archived-only torrents never count. Connected peers may still transfer data; losing the tracker is the loss of that discovery source, not proof the download is impossible.

Cross-seeded releases are judged as one payload: a title appears only when **every** torrent of the release has lost its source. One tracker dropping a cross-seed while another still works does not list it.

A title with a lost source belongs to Replace Media, not Upgrade Media: its search accepts anything equal or better, so a better release arrives as the replacement. Upgrade Media hides it unless an upgrade is already running for it. Ignoring it in Replace Media hands it back to Upgrade Media.

## The page

Rows are grouped by media folder, under four tabs:

- **All**: every title with a lost source that is not ignored;
- **In progress**: replacements downloading or being validated, with progress, speed and ETA;
- **Needs attention**: replacements waiting for a decision, and failed attempts with their reason;
- **Ignored**: titles you set aside. **Return to queue** brings one back.

Each row shows why it is here: a red **Indexer** pill naming the tracker, the tracker's message, and when it last worked. Hover the pill to see every tracker of the torrent with its state. Beside it are the current release and the best replacement found, with their Custom Format scores (hover a score for the breakdown under the title's Quality Profile), size, size change and seeders.

**History** lists finished replacements: what was replaced, by what, and how it ended.

## Finding a replacement

Use **Search** on a row, or **Search all**. **Search for Media Replacements** in **Tasks** can search on a schedule; ignored titles are left out. Searches never start downloads.

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
