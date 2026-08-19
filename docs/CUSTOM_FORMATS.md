# Custom Formats

Custom Formats use a Radarr-like matching model but are Medialogue-owned definitions.

A Custom Format contains conditions. It does **not** contain a score. Scores belong to Quality Profiles.

Supported condition types include release title/group, quality, source, resolution, edition, language, indexer, WEB provider, codecs, channels, HDR type, and release attributes.

Condition controls include:

- Required
- Negate
- regex for Release Title and Release Group
- case-insensitive regex by default

The editor includes Test Release and full parser/condition evidence. Search-time matches and scores are snapshotted so later rule edits do not rewrite historical download evidence.

Medialogue supports its own JSON import/export format. Radarr JSON import/export compatibility is intentionally not provided.
