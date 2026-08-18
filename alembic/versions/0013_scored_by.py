"""scored_by : quel etage de la cascade a note l'article

Le scoring passe a deux etages : un modele local ecarte les articles sous le
seuil sans jamais appeler Claude. Sans cette colonne, impossible de savoir a
posteriori lesquels n'ont eu que le traitement au rabais -- or c'est exactement
ce qu'on voudra relire dans quelques semaines pour verifier que le pre-filtre
n'a rien laisse filer.

Backfill : tous les articles deja notes l'ont ete par Claude, puisque le
pre-filtre n'existait pas. C'est la verite historique, pas une valeur par
defaut de confort -- d'ou un UPDATE explicite plutot qu'un server_default.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# Convention du projet : enums stockes en VARCHAR + CHECK (`native_enum=False`),
# jamais d'enum natif Postgres. Le nom de la contrainte passe par `op.f()` sinon
# la naming convention de `app/db.py` redouble le prefixe `ck_articles_`.
VALEURS = ("claude", "local")


def upgrade() -> None:
    op.add_column("articles", sa.Column("scored_by", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        op.f("ck_articles_scored_by"),
        "articles",
        sa.column("scored_by").in_(VALEURS) | sa.column("scored_by").is_(None),
    )
    # Un article est "note" des lors qu'il porte une relevance : c'est le meme
    # critere que celui qui fait entrer un article dans `score_pending`.
    op.execute("UPDATE articles SET scored_by = 'claude' WHERE relevance_score IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_articles_scored_by"), "articles", type_="check")
    op.drop_column("articles", "scored_by")
