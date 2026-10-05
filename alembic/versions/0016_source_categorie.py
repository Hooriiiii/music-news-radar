"""source_categorie : qui produit le contenu d'une source

Le scoring distingue desormais un post d'ARTISTE (actu primaire, prioritaire)
d'un post de MEDIA (deja un repackaging : le reposter serait du plagiat, il ne
sert que de filet de securite pour les tres grosses actus). Cette information
depend du compte, pas du format : `sources.type` ne la porte pas.

Nullable, sans backfill : NULL veut dire « inconnu », et le classement des
sources existantes est une decision editoriale portee par
`scripts/sources/categoriser_sources.py`, pas par la migration.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# Convention du projet : VARCHAR + CHECK, jamais d'enum natif. `op.f()` evite que
# la naming convention redouble le prefixe -- et reste dans les fonctions (un
# appel au niveau module leve a l'import, cf. 0015).
VALEURS = ("artiste", "media", "festival", "label", "club", "autre")


def upgrade() -> None:
    op.add_column("sources", sa.Column("categorie", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        op.f("ck_sources_source_categorie"),
        "sources",
        sa.column("categorie").in_(VALEURS) | sa.column("categorie").is_(None),
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_sources_source_categorie"), "sources", type_="check")
    op.drop_column("sources", "categorie")
