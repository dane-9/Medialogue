"""Add per-root media review mode and the discovery queue.

Revision ID: 0002
Revises: 0001

The original baseline builds from current SQLAlchemy metadata. Checks make
this revision safe both for databases created by an older 0001 and for fresh
databases whose baseline already contains these objects.
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    storage_columns = {column["name"] for column in inspector.get_columns("storage_roots")}
    if "discovery_mode" not in storage_columns:
        op.add_column(
            "storage_roots",
            sa.Column(
                "discovery_mode",
                sa.String(length=9),
                nullable=False,
                server_default="automatic",
            ),
        )

    if "media_discoveries" not in set(sa.inspect(bind).get_table_names()):
        # Reuse the application-owned table definition so UUID/JSON types stay
        # portable between SQLite development and PostgreSQL production.
        from app.models.domain import MediaDiscovery

        MediaDiscovery.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "media_discoveries" in set(inspector.get_table_names()):
        op.drop_table("media_discoveries")
    storage_columns = {column["name"] for column in sa.inspect(bind).get_columns("storage_roots")}
    if "discovery_mode" in storage_columns:
        op.drop_column("storage_roots", "discovery_mode")
