"""post variants : les quatre propositions de cover, et le type de chaque slide

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("variants", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Les slides existantes etaient toutes des rendus de texte : elles deviennent
    # des covers. Il n'y en a aucune en production au moment d'ecrire ceci, mais un
    # post cree entre-temps ne doit pas se retrouver avec des slides sans `kind`,
    # que le renderer ignorerait en silence.
    op.execute(
        """
        UPDATE posts
        SET slides = (
            SELECT jsonb_agg(entree || '{"kind": "cover"}'::jsonb)
            FROM jsonb_array_elements(slides) AS entree
        )
        WHERE slides IS NOT NULL
          AND jsonb_array_length(slides) > 0
          AND NOT (slides->0 ? 'kind')
        """
    )


def downgrade() -> None:
    # Asymetrique et volontaire : on ne retire pas `kind` des slides restantes.
    # Une cle en trop dans un blob JSON ne casse rien, alors qu'un UPDATE sur
    # toutes les lignes pour la retirer est un risque pour aucun gain.
    op.drop_column("posts", "variants")
