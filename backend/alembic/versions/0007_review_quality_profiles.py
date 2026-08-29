"""Let a Quality Profile be chosen before Review import.

Assigning the profile only after import meant every newly added title spent
its first moments scored by nothing, and a queue of thirty had to be corrected
one title at a time afterwards.

Revision ID: 0007
Revises: 0006
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("media_discoveries")}
    if "quality_profile_id" not in columns:
        # No foreign key: SQLite cannot add a constrained column in place, and a
        # deleted profile has to leave the discovery importable rather than
        # blocking it. The API resolves the id and treats an unknown one as unset.
        op.add_column("media_discoveries", sa.Column("quality_profile_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_discoveries") as batch:
        batch.drop_column("quality_profile_id")
