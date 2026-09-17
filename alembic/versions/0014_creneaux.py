"""creneaux : la file de publication, et l'exclusion d'un article de cette file

Un creneau porte un article avant qu'un post soit compose -- d'ou une table et
non des colonnes sur `posts`. `articles.file_exclu_raison` empeche un article en
defaut de revenir a chaque passage de l'agent de publication.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

ETATS = ("libre", "programme", "gele", "publie", "apercu", "echec", "manque")


def upgrade() -> None:
    op.add_column("articles", sa.Column("file_exclu_raison", sa.Text(), nullable=True))
    op.create_table(
        "creneaux",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publier_a", sa.DateTime(timezone=True), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("post_id", sa.Integer(), nullable=True),
        sa.Column("etat", sa.String(length=20), server_default="libre", nullable=False),
        sa.Column("epingle", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("fichiers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ig_media_id", sa.String(length=100), nullable=True),
        sa.Column("erreur", sa.Text(), nullable=True),
        sa.Column("gele_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publie_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_creneaux")),
        # SET NULL : la purge d'un article ou la suppression d'un brouillon ne
        # doit jamais effacer l'historique des publications.
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name=op.f("fk_creneaux_articles_article_id"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["post_id"], ["posts.id"],
            name=op.f("fk_creneaux_posts_post_id"), ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "etat IN ('" + "', '".join(ETATS) + "')", name=op.f("ck_creneaux_etat_creneau")
        ),
    )
    op.create_index(op.f("ix_creneaux_publier_a"), "creneaux", ["publier_a"], unique=True)
    op.create_index(op.f("ix_creneaux_article_id"), "creneaux", ["article_id"])
    op.create_index(op.f("ix_creneaux_etat"), "creneaux", ["etat"])


def downgrade() -> None:
    op.drop_index(op.f("ix_creneaux_etat"), table_name="creneaux")
    op.drop_index(op.f("ix_creneaux_article_id"), table_name="creneaux")
    op.drop_index(op.f("ix_creneaux_publier_a"), table_name="creneaux")
    op.drop_table("creneaux")
    op.drop_column("articles", "file_exclu_raison")
