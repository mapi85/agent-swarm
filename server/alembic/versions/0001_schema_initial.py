"""Schéma initial v2 — création de toutes les tables depuis les modèles.

La première migration s'appuie directement sur les métadonnées SQLAlchemy
(source de vérité unique) ; les migrations suivantes utiliseront
`alembic revision --autogenerate`.

Revision ID: 0001
Revises:
"""
from alembic import op

from server.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
