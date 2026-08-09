"""vue v_tri_pertinence : la versionner, recablee sur articles.media

Cette vue existait en production SANS etre dans les migrations : creee a la main
dans un client SQL, elle est restee hors du depot. Consequence concrete, constatee
le 2026-08-09 en appliquant `0010` a la vraie base : « cannot drop column
media_urls because other objects depend on it », alors que la migration passait sur
un cluster jetable -- ou la vue n'existe pas. **Un objet de schema hors migrations
est invisible a toute verification prealable.** D'ou ce fichier : la vue devient un
objet versionne, et le prochain changement sur `articles` la verra.

`0010` la depose ; celle-ci la recree sur `articles.media`. Le decoupage est
delibere : recreee dans `0010`, elle survivrait a tous les downgrades jusqu'a
`0001`, qui ne pourrait alors plus supprimer `articles` -- et le job CI `schema`
execute `downgrade base`. En naissant ici, elle meurt dans le downgrade d'ici,
avant que les tables tombent.

Semantique preservee : `a.media IS NOT NULL AS a_du_media` repond a la meme
question que l'ancien `a.media_urls IS NOT NULL`, « cet article a-t-il un media ».

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-09
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Definition relevee sur la base de production avant toute modification
# (`pg_get_viewdef`), a la colonne pres.
_VUE = """
CREATE VIEW v_tri_pertinence AS
 SELECT a.relevance_score AS pertinence,
    a.hotness_score AS hot,
    a.category AS categorie,
    a.title AS titre,
    a.summary AS resume,
    s.name AS source,
    a.imprint,
    a.published_at AS publie,
    a.media IS NOT NULL AS a_du_media,
    a.status,
    a.url
   FROM articles a
     JOIN sources s ON s.id = a.source_id
  WHERE a.relevance_score IS NOT NULL
  ORDER BY a.relevance_score DESC, a.hotness_score DESC
"""


def upgrade() -> None:
    # IF EXISTS avant creation : rejouable, et sans surprise si une vue du meme
    # nom subsiste d'une tentative precedente.
    op.execute("DROP VIEW IF EXISTS v_tri_pertinence")
    op.execute(_VUE)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_tri_pertinence")
