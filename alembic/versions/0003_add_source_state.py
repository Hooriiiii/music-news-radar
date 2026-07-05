"""add sources.state for adapter-specific state (X/Apify since + last run)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("state", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "state")
