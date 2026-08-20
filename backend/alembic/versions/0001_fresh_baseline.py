"""Fresh Medialogue schema baseline.

Revision ID: 0001
Revises: None

Medialogue currently targets clean-install development.  The user explicitly
prefers wiping the application database between incompatible builds, so the
migration history is intentionally squashed to the current SQLAlchemy model.
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
