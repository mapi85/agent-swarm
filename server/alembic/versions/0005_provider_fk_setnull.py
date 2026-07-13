"""FK providers → SET NULL sur sessions et token_usage (suppression d'un provider).

Permet de supprimer un provider qui a de l'historique : les sessions et lignes de
comptabilité gardent leurs données (compteurs inclus), seul le lien provider_id
devient NULL. Les agents sont déjà rebasculés sur le provider par défaut par l'API.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("sessions_provider_id_fkey", "sessions", type_="foreignkey")
    op.create_foreign_key(
        "sessions_provider_id_fkey", "sessions", "providers",
        ["provider_id"], ["id"], ondelete="SET NULL",
    )
    op.drop_constraint("token_usage_provider_id_fkey", "token_usage", type_="foreignkey")
    op.create_foreign_key(
        "token_usage_provider_id_fkey", "token_usage", "providers",
        ["provider_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("token_usage_provider_id_fkey", "token_usage", type_="foreignkey")
    op.create_foreign_key("token_usage_provider_id_fkey", "token_usage", "providers",
                          ["provider_id"], ["id"])
    op.drop_constraint("sessions_provider_id_fkey", "sessions", type_="foreignkey")
    op.create_foreign_key("sessions_provider_id_fkey", "sessions", "providers",
                          ["provider_id"], ["id"])
