"""A movie release's identity confidence in its own column.

Revision ID: 0003
Revises: 0002

The library list needs this one number per release, and reading it out of the
parse snapshot was the slowest query behind that list. 0001 creates the schema
from the current models, so only a database created before this has to gain the
column; offline SQL always starts from 0001, which already includes it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not op.get_context().as_sql:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("movie_releases")}
        if "identity_confidence" not in columns:
            op.add_column("movie_releases", sa.Column("identity_confidence", sa.Float(), nullable=True))
    op.execute(
        "UPDATE movie_releases "
        "SET identity_confidence = json_extract(parse_snapshot, '$.identity_confidence') "
        "WHERE json_type(parse_snapshot, '$.identity_confidence') IN ('real', 'integer')"
    )


def downgrade() -> None:
    with op.batch_alter_table("movie_releases") as batch:
        batch.drop_column("identity_confidence")
