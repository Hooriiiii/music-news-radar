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
    # des covers. Verifie a la main sur la base de l'utilisateur (lecture seule,
    # 2026-08-09) : il y EN A trois, a la revision 0009 -- l'ancienne version de
    # ce commentaire affirmait le contraire, a tort. `CoverIn.title` et
    # `.subtitle` sont requis cote schema, donc une slide legacy qui ne recoit
    # QUE `kind` echoue la validation de `GET /posts` (PostOut valide chaque
    # ligne) -- pas une degradation d'une carte, un 500 sur la file entiere. On
    # les backfille donc aussi depuis `headline`, que l'ancien `SlideIn`
    # validait deja pour contenir un segment surligne (cf. `validate_headline`) :
    # une slide legacy a donc toujours `title == subtitle == headline` apres
    # cette migration. Look bizarre pour un reel, correct pour le POST qu'elle
    # etait -- et ces posts n'ont pas de `variants`, donc rien a re-choisir.
    # Lisible plutot que joli.
    #
    # Alias `entree` (pas `slide`, pas `url`) : `0010` a deja montre qu'un alias
    # homonyme d'une colonne reelle se resout silencieusement sur la colonne
    # plutot que l'alias -- verifie a la main sur cluster jetable.
    op.execute(
        """
        UPDATE posts
        SET slides = (
            SELECT jsonb_agg(
                entree
                || '{"kind": "cover"}'::jsonb
                || jsonb_build_object(
                    'title', COALESCE(entree->>'title', entree->>'headline'),
                    'subtitle', COALESCE(entree->>'subtitle', entree->>'headline')
                )
            )
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
