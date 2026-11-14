"""Libraries: named groups of media folders, each its own sidebar entry.

Revision ID: 0008
Revises: 0007

Every existing media folder and title goes into "Movies" or "Shows" by its
media type, so nothing changes until a library is added. 0001 creates the
schema from the current models, and creating the libraries table there already
adds those two; only a database created before this has to gain the table, the
columns and the two libraries. On such a database the titles' library_id stays
nullable at the database level (SQLite cannot add a NOT NULL reference without
rebuilding the table); the ORM always sets it.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if op.get_context().as_sql:
        # Offline SQL always starts from 0001, which has the table and columns
        # but cannot add rows there.
        for index, (name, kind) in enumerate((("Movies", "MOVIES"), ("Shows", "SHOWS"))):
            op.execute(
                "INSERT INTO libraries (id, name, kind, sort_order, created_at, updated_at) "
                f"VALUES ('{uuid.uuid4().hex}', '{name}', '{kind}', {index}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        return

    bind = op.get_bind()
    if "libraries" not in sa.inspect(bind).get_table_names():
        op.execute(
            "CREATE TABLE libraries ("
            "id CHAR(32) NOT NULL, "
            "name VARCHAR(256) NOT NULL, "
            "kind VARCHAR(6) NOT NULL, "
            "sort_order INTEGER NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL, "
            "PRIMARY KEY (id), "
            "CONSTRAINT uq_libraries_name UNIQUE (name))"
        )
        libraries = sa.table(
            "libraries",
            sa.column("id", sa.Uuid()),
            sa.column("name", sa.String()),
            sa.column("kind", sa.String()),
            sa.column("sort_order", sa.Integer()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        now = datetime.now(timezone.utc)
        op.bulk_insert(
            libraries,
            [
                {"id": uuid.uuid4(), "name": "Movies", "kind": "MOVIES", "sort_order": 0, "created_at": now, "updated_at": now},
                {"id": uuid.uuid4(), "name": "Shows", "kind": "SHOWS", "sort_order": 1, "created_at": now, "updated_at": now},
            ],
        )

    reference = "CHAR(32) REFERENCES libraries (id) ON DELETE RESTRICT"
    if "library_id" not in _columns("storage_roots"):
        op.execute(f"ALTER TABLE storage_roots ADD COLUMN library_id {reference}")
    if "category" not in _columns("storage_roots"):
        op.execute("ALTER TABLE storage_roots ADD COLUMN category VARCHAR(256)")
    for table in ("movies", "shows"):
        if "library_id" not in _columns(table):
            op.execute(f"ALTER TABLE {table} ADD COLUMN library_id {reference}")

    op.execute("CREATE INDEX IF NOT EXISTS ix_storage_roots_library ON storage_roots (library_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_movies_library ON movies (library_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_shows_library ON shows (library_id)")

    first = (
        "(SELECT id FROM libraries WHERE kind = '{kind}' ORDER BY sort_order, name LIMIT 1)"
    )
    for kind in ("MOVIES", "SHOWS"):
        op.execute(
            f"UPDATE storage_roots SET library_id = {first.format(kind=kind)} "
            f"WHERE library_id IS NULL AND media_type = '{kind}'"
        )
    op.execute(f"UPDATE movies SET library_id = {first.format(kind='MOVIES')} WHERE library_id IS NULL")
    op.execute(f"UPDATE shows SET library_id = {first.format(kind='SHOWS')} WHERE library_id IS NULL")


def downgrade() -> None:
    for table in ("movies", "shows"):
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_library")
        with op.batch_alter_table(table) as batch:
            batch.drop_column("library_id")
    op.execute("DROP INDEX IF EXISTS ix_storage_roots_library")
    with op.batch_alter_table("storage_roots") as batch:
        batch.drop_column("category")
        batch.drop_column("library_id")
    op.execute("DROP TABLE IF EXISTS libraries")
