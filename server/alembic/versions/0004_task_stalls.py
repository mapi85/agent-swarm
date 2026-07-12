"""Ajoute tasks.consecutive_stalls (détection des chaînes brisées / stalls).

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("consecutive_stalls", sa.Integer(),
                                     nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("tasks", "consecutive_stalls")
