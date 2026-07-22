"""add artists + artist_mentions : la memoire A&R de Kinda

Les articles sont purges a 14 jours ; les artistes et leurs citations, jamais.
C'est ce qui permet de suivre un artiste dans la duree (depuis quand, a quelle
frequence, est-ce que ca accelere).

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mentions_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_emergence", sa.Integer(), nullable=True),
        sa.Column("tracked", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artists")),
    )
    # unique=True + index=True côté modèle -> un index UNIQUE, pas une contrainte
    # unique doublée d'un index simple (sinon `alembic check` diverge)
    op.create_index(op.f("ix_artists_slug"), "artists", ["slug"], unique=True)
    op.create_index(op.f("ix_artists_last_seen_at"), "artists", ["last_seen_at"])
    op.create_index(op.f("ix_artists_max_emergence"), "artists", ["max_emergence"])

    op.create_table(
        "artist_mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("mentioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("relevance_score", sa.Integer(), nullable=True),
        sa.Column("emergence_score", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artist_mentions")),
        sa.ForeignKeyConstraint(
            ["artist_id"], ["artists.id"], name=op.f("fk_artist_mentions_artist_id_artists")
        ),
        # SET NULL et pas CASCADE : la purge des articles ne doit jamais effacer
        # l'historique de l'artiste, seulement le lien vers l'article disparu
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name=op.f("fk_artist_mentions_article_id_articles"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name=op.f("fk_artist_mentions_source_id_sources")
        ),
    )
    op.create_index(op.f("ix_artist_mentions_artist_id"), "artist_mentions", ["artist_id"])
    op.create_index(op.f("ix_artist_mentions_article_id"), "artist_mentions", ["article_id"])
    op.create_index(op.f("ix_artist_mentions_mentioned_at"), "artist_mentions", ["mentioned_at"])


def downgrade() -> None:
    op.drop_table("artist_mentions")
    op.drop_table("artists")
