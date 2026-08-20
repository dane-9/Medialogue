# v8 Alembic version-table fix

Medialogue v7 introduced the revision identifier `0011_download_client_health_diagnostics`, which is longer than Alembic's default PostgreSQL `alembic_version.version_num VARCHAR(32)` column. PostgreSQL correctly rejected the revision update and the container restarted.

v8 keeps the existing revision history and makes migration 0011 widen `alembic_version.version_num` to `VARCHAR(128)` before Alembic records the revision. A database currently at `0010_torrent_size_bigint` can therefore retry normally; no database reset or manual SQL is required.

Regression coverage now checks both the revision-ID length contract and the real PostgreSQL column width.
