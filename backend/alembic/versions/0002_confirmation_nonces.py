"""Persist single-use destructive confirmation token IDs.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "confirmation_nonces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("jti", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_confirmation_nonces_expires_at",
        "confirmation_nonces",
        ["expires_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_confirmation_nonces_expires_at", table_name="confirmation_nonces", if_exists=True)
    op.drop_table("confirmation_nonces", if_exists=True)
