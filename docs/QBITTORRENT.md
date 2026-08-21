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

## Removal

When Medialogue removes an associated torrent as part of an explicitly confirmed workflow, qBittorrent is called without deleting data. Filesystem deletion, if selected, is separately previewed and committed by Medialogue.
