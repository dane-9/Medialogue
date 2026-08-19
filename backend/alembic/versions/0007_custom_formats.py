"""Finish persistent Custom Format editing support."""

from alembic import context, op
import sqlalchemy as sa

revision = "0007_custom_formats"
down_revision = "0006_interactive_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column("custom_formats", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("custom_formats")}
    if "revision" not in columns:
        op.add_column("custom_formats", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    # Forward-only development migration. Custom Format revisions protect
    # manual edits from being overwritten by stale browser state.
    return
