"""Make scheduled execution creation atomic and index job history.

Revision ID: 0009
Revises: 0008
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    indexes = {index["name"] for index in inspector.get_indexes("jobs")}
    if "schedule_id" not in columns:
        op.add_column(
            "jobs",
            sa.Column("schedule_id", sa.Uuid(), nullable=True),
        )
    if "ix_jobs_created" not in indexes:
        op.create_index("ix_jobs_created", "jobs", ["created_at"])
    if "uq_jobs_active_schedule" not in indexes:
        op.create_index(
            "uq_jobs_active_schedule",
            "jobs",
            ["schedule_id"],
            unique=True,
            sqlite_where=sa.text("schedule_id IS NOT NULL AND status IN ('QUEUED', 'RUNNING')"),
        )


def downgrade() -> None:
    op.drop_index("uq_jobs_active_schedule", table_name="jobs")
    op.drop_index("ix_jobs_created", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("schedule_id")
