# Changelog

## Unreleased: first release

The first public release of Medialogue.

- **Library:** Movies and Shows read from existing media folders, matched with TMDB and verified against Plex. Media is never moved, renamed or deleted by scanning.
- **New Media:** review newly found folders, confirm their matches and Quality Profiles, and add them.
- **Upgrade Media** and **Replace Media:** find better releases, or new sources for torrents removed by their trackers, compare them by quality and Custom Format score, and hand them to qBittorrent with validation and cleanup. Titles trumped on one tracker while a cross-seed still seeds are listed in Replace Media as **Trumped**.
- **Downloads** and **Blocklist:** live qBittorrent activity, and failed releases kept out of future searches.
- **Problems:** one queue for decisions Medialogue cannot make alone: duplicate copies, Plex disagreements, unclear replacement targets, and downloads whose files cannot be found.
- **Backups:** every torrent archived with where it downloaded to, copied to Google Drive, and restorable on a new server from the uploaded backup. Database copies are kept daily, weekly and monthly, and each keeps the torrents it lists on Drive.
- **Custom Formats**, **Quality Profiles**, **Tasks**, **Events** and **Settings** for Media folders, qBittorrent, TMDB, Plex, Torznab indexers and security.
- One SQLite database in `/config`, with migrations applied on start, shipped as a Docker image. Media mounts are writable; a media folder set to Read only is never changed.
