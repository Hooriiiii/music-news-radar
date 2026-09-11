"""source_instagram : elargir les types de source acceptes

Le CHECK sur `sources.type` enumere les valeurs en dur (convention du projet :
VARCHAR + CHECK, jamais d'enum natif Postgres). Ajouter une valeur a l'enum Python
ne suffit donc pas -- sans cette migration, l'INSERT d'une source Instagram est
rejete par la base.

Postgres ne sait pas modifier un CHECK : on le supprime et on le recree.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# `op.f()` est indispensable : sans lui, la naming convention de `app/db.py`
# redouble le prefixe et produit `ck_sources_ck_sources_source_type`.
CONTRAINTE = op.f("ck_sources_source_type")

AVANT = ("rss", "reddit_rss", "bluesky", "x")
APRES = ("rss", "reddit_rss", "bluesky", "x", "instagram")


def _poser(valeurs):
    op.create_check_constraint(CONTRAINTE, "sources", sa.column("type").in_(valeurs))


def upgrade() -> None:
    op.drop_constraint(CONTRAINTE, "sources", type_="check")
    _poser(APRES)


def downgrade() -> None:
    # Les sources Instagram violeraient le CHECK restaure : les eteindre d'abord.
    op.execute("DELETE FROM sources WHERE type = 'instagram'")
    op.drop_constraint(CONTRAINTE, "sources", type_="check")
    _poser(AVANT)
