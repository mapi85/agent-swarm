"""Ajoute agents.heartbeat_minutes (cadence de veille des agents récurrents).

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("heartbeat_minutes", sa.Integer(),
                                      nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("agents", "heartbeat_minutes")
