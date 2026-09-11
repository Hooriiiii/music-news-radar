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
# redouble le prefixe et produit `ck_sources_ck_sources_source_type`. Mais
# l'appel doit rester DANS une fonction : au niveau module, il s'execute a
# l'IMPORT du fichier, avant que le proxy `Operations` d'Alembic ne soit
# etabli (ce qui n'arrive qu'au moment de context.configure()) -- une commande
# qui se contente de charger les scripts sans jouer de migration (`alembic
# history`, ou tout chargement direct via `ScriptDirectory`) leve alors
# `NameError: Can't invoke function 'f' ...`.

AVANT = ("rss", "reddit_rss", "bluesky", "x")
APRES = ("rss", "reddit_rss", "bluesky", "x", "instagram")


def _poser(valeurs):
    contrainte = op.f("ck_sources_source_type")
    op.create_check_constraint(contrainte, "sources", sa.column("type").in_(valeurs))


def upgrade() -> None:
    op.drop_constraint(op.f("ck_sources_source_type"), "sources", type_="check")
    _poser(APRES)


def downgrade() -> None:
    # Les sources Instagram violeraient le CHECK restaure : les supprimer d'abord.
    # `articles.source_id` et `artist_mentions.source_id` sont des FK SANS
    # `ON DELETE` (contrairement a `artist_mentions.article_id`, en SET NULL) :
    # supprimer une source Instagram encore referencee leve ForeignKeyViolation
    # et bloque le downgrade en 0014. On supprime donc les lignes dependantes
    # AVANT la source elle-meme -- articles puis artist_mentions, dans cet
    # ordre pour ne violer aucune contrainte au passage.
    op.execute(
        "DELETE FROM articles WHERE source_id IN "
        "(SELECT id FROM sources WHERE type = 'instagram')"
    )
    op.execute(
        "DELETE FROM artist_mentions WHERE source_id IN "
        "(SELECT id FROM sources WHERE type = 'instagram')"
    )
    op.execute("DELETE FROM sources WHERE type = 'instagram'")
    op.drop_constraint(op.f("ck_sources_source_type"), "sources", type_="check")
    _poser(AVANT)
