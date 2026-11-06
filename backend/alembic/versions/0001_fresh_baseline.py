"""Fresh Medialogue schema baseline.

Revision ID: 0001
Revises: None

The whole schema, created from the current SQLAlchemy models. Later schema
changes are added as revisions on top of this one.
"""

from alembic import op

from app.db.base import Base
from app.models import *  # noqa: F401,F403

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
