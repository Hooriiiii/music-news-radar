"""add articles.mentioned_artists for the home-made radar

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("mentioned_artists", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("articles", "mentioned_artists")
