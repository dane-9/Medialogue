"""When a release was picked by hand for an upgrade or replacement.

Revision ID: 0011
Revises: 0010

A search used to store its own best release over the one picked, so opening
"View all releases", a scheduled search or the RSS feed put the recommended
release back. Searches now keep a picked release while it is still listed.
0001 creates the schema from the current models, so only a database created
before this has to gain the column; offline SQL starts from 0001.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("upgrade_candidates")}
    if "picked_at" not in columns:
        op.add_column("upgrade_candidates", sa.Column("picked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("upgrade_candidates") as batch:
        batch.drop_column("picked_at")
