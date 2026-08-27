"""Persist movie edition choices made before Review import.

Revision ID: 0006
Revises: 0005
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("media_discoveries")}
    if "manual_edition_override" not in columns:
        op.add_column("media_discoveries", sa.Column("manual_edition_override", sa.String(length=128)))
    if "edition_override_active" not in columns:
        op.add_column(
            "media_discoveries",
            sa.Column("edition_override_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    with op.batch_alter_table("media_discoveries") as batch:
        batch.drop_column("edition_override_active")
        batch.drop_column("manual_edition_override")
