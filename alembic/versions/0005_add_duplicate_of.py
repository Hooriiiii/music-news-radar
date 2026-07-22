"""add articles.duplicate_of_id : regroupement des doublons entre sources

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("duplicate_of_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_articles_duplicate_of_id_articles"),
        "articles",
        "articles",
        ["duplicate_of_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_articles_duplicate_of_id"), "articles", ["duplicate_of_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_articles_duplicate_of_id"), table_name="articles")
    op.drop_constraint(
        op.f("fk_articles_duplicate_of_id_articles"), "articles", type_="foreignkey"
    )
    op.drop_column("articles", "duplicate_of_id")
