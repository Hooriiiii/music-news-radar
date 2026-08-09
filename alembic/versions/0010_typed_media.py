"""typed media : les medias d'un article portent leur type

L'ancienne `media_urls` etait une liste d'URL. Un tweet video y stockait sa
vignette, donc rien ne distinguait une video d'une photo et le format REELS etait
indecidable.

La transformation des lignes existantes les classe toutes en `photo`. C'est faux
pour une ancienne vignette de video -- et sans consequence : les articles sont
purges a 14 jours, et le pire cas est de ne pas proposer REELS sur un article deja
peri.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# La vue `v_tri_pertinence` existait en production SANS etre dans les migrations :
# creee a la main dans un client SQL, elle est restee hors du depot. Consequence
# constatee le 2026-08-09 en appliquant cette migration a la vraie base : elle a
# echoue sur « cannot drop column media_urls because other objects depend on it »,
# alors qu'elle passait sur un cluster jetable -- ou la vue n'existe pas. Un objet
# de schema hors migrations est invisible a toute verification prealable.
#
# Cette migration la DEPOSE seulement ; c'est `0012` qui la recree sur la nouvelle
# colonne. Le decoupage n'est pas cosmetique : une vue recreee ici survivrait a tous
# les downgrades jusqu'a `0001`, qui ne pourrait alors plus supprimer `articles`
# (« cannot drop table because other objects depend on it ») -- et le job CI
# `schema` execute justement `downgrade base`. En naissant dans `0012`, elle meurt
# dans son downgrade, avant que les tables tombent.


def upgrade() -> None:
    # IF EXISTS : la vue est presente sur la base de l'utilisateur, absente d'un
    # cluster neuf. Les deux cas doivent passer.
    op.execute("DROP VIEW IF EXISTS v_tri_pertinence")
    op.add_column(
        "articles",
        sa.Column("media", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # jsonb_array_elements_text : `media_urls` est un tableau JSON de chaines.
    # UPDATE ... FROM plutot qu'une sous-requete correlee dans SET : Postgres
    # refuse un agregat (jsonb_agg) sur une fonction ensembliste (SRF) correlee
    # a la ligne mise a jour a cet endroit-la ("aggregate functions are not
    # allowed in UPDATE"). L'alias media_url (et non `url`) evite la collision
    # silencieuse avec la vraie colonne articles.url : avec `AS url`, Postgres
    # resolvait `url` sur la colonne de la table plutot que sur l'alias --
    # decouvert en verifiant la transformation a la main sur cluster jetable.
    op.execute(
        """
        UPDATE articles a
        SET media = m.media
        FROM (
            SELECT id, jsonb_agg(jsonb_build_object('type', 'photo', 'url', media_url)) AS media
            FROM articles, jsonb_array_elements_text(media_urls) AS media_url
            WHERE media_urls IS NOT NULL
              -- jsonb_array_elements_text leve une erreur sur un scalaire (JSON
              -- `null` y compris) : l'ORM n'ecrit jamais autre chose qu'un
              -- tableau ici, mais une ceinture pour ne pas faire avorter TOUTE
              -- la migration sur une ligne hors norme coute une seule clause.
              AND jsonb_typeof(media_urls) = 'array'
            GROUP BY id
        ) AS m
        WHERE a.id = m.id
        """
    )
    op.drop_column("articles", "media_urls")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_tri_pertinence")
    op.add_column(
        "articles",
        sa.Column("media_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Meme raison que dans upgrade() : UPDATE ... FROM au lieu d'un agregat
    # correle dans SET.
    op.execute(
        """
        UPDATE articles a
        SET media_urls = m.media_urls
        FROM (
            SELECT id, jsonb_agg(entree->>'url') AS media_urls
            FROM articles, jsonb_array_elements(media) AS entree
            WHERE media IS NOT NULL AND entree->>'url' IS NOT NULL
            GROUP BY id
        ) AS m
        WHERE a.id = m.id
        """
    )
    op.drop_column("articles", "media")
