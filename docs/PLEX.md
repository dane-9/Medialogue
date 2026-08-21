# Plex Setup

Configure one Plex server under Settings → Plex.

Medialogue uses Plex read-only as secondary presence/path evidence. TMDB/manual matching owns Movie identity; Plex titles and years are never treated as authoritative. Medialogue does not trigger Plex scans and it does not edit Plex metadata.

Verification states include:

- Plex Verified — Plex was checked and the physical media path was found. Plex's display title/year do not need to match Medialogue.
- Plex Not in Plex — Plex was checked successfully, but no unique matching media was found.
- Plex Pending — the media has not been checked yet, or no eligible local media is available to verify.
- Plex Multiple versions — title/year fallback found more than one Plex version and cannot choose one safely.
- Plex Conflict — Shows only: Plex matched the same physical file but reports different season/episode numbers. Movie title/year differences never create a conflict.
- Plex Unavailable — the Plex server could not be queried.

## Docker path handling

Medialogue first tries an exact absolute path match. When Plex and Medialogue see the same files through different container mount points, it also compares the path relative to the configured Medialogue storage root against Plex's library snapshot.

For example, these can verify as the same Movie even though the container prefixes differ:

- Medialogue: `/movies/Inception (2010)/Inception (2010).mkv`
- Plex: `/plex-media/movies/Inception (2010)/Inception (2010).mkv`

The relative-path fallback is accepted only when it identifies one unique Plex path. Ambiguous path matches are not guessed. Movie verification can still use the existing title/year fallback when path evidence is unavailable.

A full Plex sync loads the Plex library once and reuses that snapshot for all titles, avoiding one full Plex library request per Movie or Show. Storage and Plex paths remain read-only throughout verification.

## Live UI updates

Movies, Shows, and their detail pages subscribe to Medialogue's server-sent event stream. Scan, media-presence, Plex, and problem events invalidate the visible library data automatically. A 15-second visibility-aware polling fallback is also used so the UI catches up if the event stream is temporarily interrupted.
