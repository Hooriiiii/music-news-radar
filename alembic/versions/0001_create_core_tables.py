"""create sources, articles and digests tables

Revision ID: 0001
Revises:
Create Date: 2026-07-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("genre", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.CheckConstraint(
            "type IN ('rss', 'reddit_rss', 'bluesky', 'x')", name=op.f("ck_sources_source_type")
        ),
    )
    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("relevance_score", sa.Integer(), nullable=True),
        sa.Column("hotness_score", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("imprint", sa.String(length=200), nullable=True),
        sa.Column("genre", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="new", nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("media_urls", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_articles"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_articles_sources_source_id"
        ),
        sa.UniqueConstraint("raw_hash", name="uq_articles_raw_hash"),
        sa.CheckConstraint(
            "status IN ('new', 'reviewed', 'used')", name=op.f("ck_articles_article_status")
        ),
    )
    op.create_index("ix_articles_published_at", "articles", ["published_at"])
    op.create_index("ix_articles_status", "articles", ["status"])
    op.create_table(
        "digests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "article_ids", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_digests"),
        sa.UniqueConstraint("date", name="uq_digests_date"),
    )


def downgrade() -> None:
    op.drop_table("digests")
    op.drop_index("ix_articles_status", table_name="articles")
    op.drop_index("ix_articles_published_at", table_name="articles")
    op.drop_table("articles")
    op.drop_table("sources")
