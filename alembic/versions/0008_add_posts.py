"""add posts : la file editoriale des carrousels Instagram

Les articles sont purges a 14 jours ; un post publie est une archive permanente.
D'ou la FK en SET NULL et les champs denormalises (titre, url, source).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("article_title", sa.Text(), nullable=False),
        sa.Column("article_url", sa.String(length=2000), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column(
            "template", sa.String(length=100), server_default="kinda_post", nullable=False
        ),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("hashtags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("slides", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_posts")),
        # SET NULL et pas CASCADE : la purge des articles ne doit jamais effacer
        # l'archive editoriale, seulement le lien vers l'article disparu
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name=op.f("fk_posts_articles_article_id"), ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'published')", name=op.f("ck_posts_post_status")
        ),
    )
    op.create_index(op.f("ix_posts_article_id"), "posts", ["article_id"])
    op.create_index(op.f("ix_posts_status"), "posts", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_posts_status"), table_name="posts")
    op.drop_index(op.f("ix_posts_article_id"), table_name="posts")
    op.drop_table("posts")
