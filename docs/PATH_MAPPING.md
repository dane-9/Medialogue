# Remote Path Mapping

Use a remote path mapping only when qBittorrent reports paths that differ from the paths visible inside the Medialogue container.

Example:

```text
qBittorrent reports: /downloads/movies/Inception 2010/...
Medialogue sees:     /media/movies/Inception 2010/...
```

Configure:

```text
Remote prefix: /downloads/movies
Local prefix:  /media/movies
```

Mappings can be scoped to a particular qBittorrent client and storage root.

Medialogue never guesses a path translation. A failed translation becomes `PATH_MAPPING_FAILED` and remains a visible Problem until corrected.

A mapping is translation only. It never moves, copies, renames, imports, or hardlinks media.
