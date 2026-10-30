"""Les traces d'une publication dont on ne sait pas si elle est partie.

Une feuille sans dependance applicative : `store` les lit pour ecarter l'article,
`runner` pour les ecrire, et `runner` importe `store` -- les constantes ne peuvent
donc pas vivre dans `runner` sans import circulaire.

Deux traces, ecrites APRES la reclamation, donc apres le dernier instant ou l'on
pouvait affirmer que rien n'etait parti :

- la reclamation restee telle quelle : la machine a coupe, peut-etre juste apres
  `media_publish` ;
- un echec de publication : un delai depasse peut cacher une publication reussie.

Une panne d'hebergement n'en fait PAS partie : le conteneur n'est cree qu'une fois
le tunnel debout, donc rien n'a pu partir, et `runner.rendre` repasse le creneau en
GELE. Voir docs/reference/publication.md, « Exclusion ».
"""

from sqlalchemy import ColumnElement, or_
from sqlalchemy.orm import InstrumentedAttribute

# Erreur posee a la reclamation. Recente, elle signifie « publication en cours »
# (conteneur, attente de l'heure) : le dashboard l'affiche ainsi et refuse les
# editions de contenu (voir api/posts.py).
RAISON_RECLAMATION = "publication interrompue avant confirmation"
PREFIXE_ECHEC_PUBLICATION = "echec de publication : "


def publication_incertaine(raison: str | None) -> bool:
    """Cette raison (d'exclusion ou d'erreur de creneau) laisse-t-elle ouverte la
    possibilite que le post soit en ligne ?"""
    return raison == RAISON_RECLAMATION or (raison or "").startswith(PREFIXE_ECHEC_PUBLICATION)


def erreur_incertaine(colonne: InstrumentedAttribute) -> ColumnElement[bool]:
    """`publication_incertaine`, en SQL."""
    return or_(colonne == RAISON_RECLAMATION, colonne.startswith(PREFIXE_ECHEC_PUBLICATION, autoescape=True))
