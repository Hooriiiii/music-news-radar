"""add articles.emergence_score : l'axe A&R (jeune artiste a suivre)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("emergence_score", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_articles_emergence_score"), "articles", ["emergence_score"])


def downgrade() -> None:
    op.drop_index(op.f("ix_articles_emergence_score"), table_name="articles")
    op.drop_column("articles", "emergence_score")
