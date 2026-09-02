# Development build replacement

Medialogue currently has no cross-build data migration contract. When the current development schema or persisted formats change, start the new build with fresh application data instead of carrying database/config state forward.

## Recommended procedure

1. Stop Medialogue.
2. Remove the prior development database and file-backed integration configuration.
3. Pull/rebuild and start the target image. The entrypoint creates/validates the single current SQLite schema baseline.
4. Sign in and configure integrations again through the UI.
5. Add Storage Roots. Every newly added root remains inactive until one explicit successful scan initializes it.

## SQLite database

The database is `/config/medialogue.db`. Keep `/config` on a local filesystem and run only one Medialogue container against it.

## `MEDIALOGUE_SECRET_KEY`

`MEDIALOGUE_SECRET_KEY` protects the current build's encrypted secrets file. Do not copy `secrets.enc` between development builds.
