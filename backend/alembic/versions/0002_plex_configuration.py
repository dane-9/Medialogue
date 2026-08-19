"""Add persisted Plex server configuration.

Kept as an idempotent historical revision so databases created by the early
Medialogue development builds can continue upgrading. Fresh databases already
receive this table from the explicit 0001 schema.
"""

from alembic import context, op
import sqlalchemy as sa

revision = "0002_plex_configuration"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        # 0001 now contains this table. Do not render duplicate DDL offline.
        return
    bind = op.get_bind()
    if "plex_configurations" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "plex_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("health", sa.String(length=32), nullable=False),
        sa.Column("machine_identifier", sa.String(length=256)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("last_error", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    # Forward-only compatibility marker. The explicit 0001 baseline already
    # owns this table on fresh installations, so removing it would make a
    # downgrade to 0001 inconsistent.
    return
