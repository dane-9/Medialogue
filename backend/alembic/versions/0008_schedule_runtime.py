"""Persist scheduled task execution state.

Revision ID: 0008
Revises: 0007
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("schedules")}
    additions = {
        "last_started_at": sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        "last_finished_at": sa.Column("last_finished_at", sa.DateTime(timezone=True), nullable=True),
        "next_run_at": sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        "last_status": sa.Column("last_status", sa.String(length=32), nullable=True),
        "last_duration_ms": sa.Column("last_duration_ms", sa.Integer(), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("schedules", column)


def downgrade() -> None:
    with op.batch_alter_table("schedules") as batch:
        batch.drop_column("last_duration_ms")
        batch.drop_column("last_status")
        batch.drop_column("next_run_at")
        batch.drop_column("last_finished_at")
        batch.drop_column("last_started_at")
