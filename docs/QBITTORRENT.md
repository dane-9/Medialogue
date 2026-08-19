# qBittorrent Setup

Add each qBittorrent instance independently under Settings → qBittorrent.

Configure:

- display name;
- URL;
- username/password when required;
- scope: Movies or Shows;
- optional qBittorrent category/tags;
- polling interval.

Medialogue supports multiple instances. If exactly one client is eligible for a selected search result it is used immediately. If several are eligible, the UI asks which one to use.

## Leave-in-place behavior

Medialogue does not send a normal search download through an import/move pipeline. qBittorrent chooses the save location from its own configuration/category. Medialogue observes that location and attaches the completed media where it exists.

Unrelated torrents outside configured/accessible media roots are ignored.

## Removal

When Medialogue removes an associated torrent as part of an explicitly confirmed workflow, qBittorrent is called without deleting data. Filesystem deletion, if selected, is separately previewed and committed by Medialogue.
