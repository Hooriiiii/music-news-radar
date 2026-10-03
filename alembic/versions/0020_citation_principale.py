"""artist_mentions.principal : la citation porte-t-elle l'actu, ou n'est-elle
qu'un nom de passage ?

`record_mentions` recopiait la note d'emergence de l'ARTICLE sur CHAQUE artiste
cite. Un article a 75 sur un jeune talent parraine faisait donc monter le
parrain deja celebre exactement comme l'inconnu qu'il parraine (mesure du
2026-10-03 : « Dance Up-And-Comer of the Month: Tatyana Jane ... Gotten Lessons
From Skrillex » a 85 sur Skrillex). C'est un defaut d'ATTRIBUTION.

L'information qui tranche -- le RANG de l'artiste dans `mentioned_artists` --
n'existe que sur l'article, et la purge a 2 jours l'efface. Elle doit donc etre
copiee au moment de l'enregistrement, comme le titre et les notes le sont deja.

NULL = inconnu (les 13 310 citations anterieures), traite comme PRINCIPAL par
tout lecteur : la regle du doute du depot -- ne pas savoir vaut mieux que
degrader a tort une vraie decouverte.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artist_mentions", sa.Column("principal", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("artist_mentions", "principal")
