"""Distinguish automatic movie editions from explicit Standard overrides.

Revision ID: 0005
Revises: 0004
"""

from alembic import context, op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fresh installs get the current model from 0001. Only an older database
    # reaching this revision needs the additive compatibility change.
    if context.is_offline_mode():
        return
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("movie_releases")}
    if "edition_override_active" not in columns:
        op.add_column(
            "movie_releases",
            sa.Column("edition_override_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.execute(
        sa.text(
            "UPDATE movie_releases SET edition_override_active = 1 "
            "WHERE manual_edition_override IS NOT NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("movie_releases") as batch:
        batch.drop_column("edition_override_active")
