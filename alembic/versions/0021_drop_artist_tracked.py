"""artists.tracked : retirer une colonne MORTE, pas un reglage.

`tracked` existait depuis la migration 0007. Elle etait ECRITE par
`PATCH /artists/{id}` et lue par PERSONNE : aucune lecture dans `app/`, aucune
dans `scripts/`, aucune requete qui la filtre ou la trie. Ce que le pipeline
appelle « artiste suivi » est tout autre chose -- une source Instagram ACTIVE de
categorie artiste (88 en production), via `comptes_artistes.IndexComptes` : c'est
elle qui ouvre l'exception au pre-filtre (`scoring.ContexteScoring`) et la
reserve de visuels (`content/choix_visuels.py`).

Elle a deja coute une FAUSSE ALERTE : `scripts/diagnostic/etat_notion.py` la comptait pour
conclure « 0 artiste suivi sur 4 684, les chemins qui en dependent ne se
declenchent jamais » -- faux, et relaye a l'utilisateur puis ecrit dans sa
documentation. Une colonne qu'on peut confondre avec une vraie est pire qu'une
colonne absente.

Le lien UTILE, lui, reste : `artists.source_instagram_id` (migration 0018), qui
rattache une fiche a son compte suivi quand le nom ne suffit pas. C'est le seul
champ que `PATCH /artists/{id}` accepte desormais.

RETOUR ARRIERE : `downgrade` recree la colonne avec son defaut serveur
(`false`), donc toutes les fiches reviennent a `false`. Aucune information n'est
perdue au sens utile -- la valeur n'etait lue par rien -- mais un suivi pose a la
main au dashboard avant cette migration ne revient PAS. Mesure avant de
descendre : en production, `PATCH` n'a jamais ete appele depuis une interface
(le panneau Artistes n'existait pas), donc la colonne ne porte que des `false`.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("artists", "tracked")


def downgrade() -> None:
    # `server_default` ET `nullable=False` : la table est deja peuplee (4 752
    # fiches en production), une colonne NOT NULL sans defaut echouerait.
    op.add_column(
        "artists",
        sa.Column("tracked", sa.Boolean(), server_default="false", nullable=False),
    )
