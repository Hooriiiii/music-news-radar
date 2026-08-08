"""add render_report : le rapport du dernier rendu d'un post

Le rendu peut aboutir a une image debordee malgre la reduction de police. On ne
veut pas que ce cas passe en silence : le rapport porte un drapeau `overflow`
que le dashboard montre.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("render_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("posts", "render_report")
