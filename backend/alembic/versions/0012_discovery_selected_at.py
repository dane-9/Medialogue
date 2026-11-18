"""When a New Media match was chosen by hand.

Revision ID: 0012
Revises: 0011

A rescan kept a chosen match only when it differed from the suggestion of the
time, so confirming the suggested title and then getting another suggestion
switched the match. A match chosen by hand is now remembered as such. 0001
creates the schema from the current models, so only a database created before
this has to gain the column; offline SQL starts from 0001.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("media_discoveries")}
    if "selected_at" not in columns:
        op.add_column("media_discoveries", sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_discoveries") as batch:
        batch.drop_column("selected_at")
