"""creneaux.elements : la liste ordonnee de ce qui part REELLEMENT, ecrite au gel.

LE DEFAUT MESURE. `creneaux.fichiers` ne porte que des NOMS de fichiers, et la
liste publiee etait pour tout le reste RECALCULEE : `posts.slides` est le
brouillon, `preparation.medias_du_carrousel` y ajoute au gel jusqu'a neuf medias
de l'article, et rien ne persistait le resultat. Le post 164 (Tomorrowland)
affichait donc 1 slide dans la fiche quand Instagram en recevait 3 -- dont un
logo Wikipedia recadre, repere APRES publication.

Mesure du 2026-10-04 sur la base vivante : 86 creneaux portent des fichiers
figes ; sur les 48 carrousels dont on peut encore recalculer les candidats,
27 (56 %) ne retombent PAS sur le nombre de fichiers reellement figes. Le
recalcul ne dit donc pas ce qui est parti -- et il ne le dira jamais, puisque
l'article est purge a deux jours (creneau 3 : 5 fichiers figes, 1 candidat
recalculable aujourd'hui).

Un element ECARTE au gel (image trop petite, trop loin du 4:5, video retombee
sur sa vignette, carrousel plein) y figure AVEC SA RAISON : c'est precisement ce
que personne ne voyait, ces decisions n'etant que journalisees.

Denormalisee comme le reste du post : la colonne ne reference aucun article, donc
elle survit a la purge (`retention_days` = 2 jours) et au creneau devenu autonome
de son article (`store.porte_un_post_orphelin`).

NULL = inconnu, jamais « vide » : les 86 creneaux deja geles n'ont pas de
manifeste, et la relecture dit « inconnu » plutot que d'inventer une liste.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creneaux",
        sa.Column(
            "elements",
            sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("creneaux", "elements")
