"""Le manifeste du gel : ce qui part, dans l'ordre, et ce qui a ete ecarte.

POURQUOI IL EXISTE. La liste publiee etait RECALCULEE, jamais ecrite.
`posts.slides` est le brouillon ; `preparation.medias_du_carrousel` y ajoute au
gel jusqu'a neuf medias de l'actu ; `creneaux.fichiers` ne garde que des NOMS de
fichiers. Le post 164 (Tomorrowland) affichait donc 1 slide dans la fiche quand
Instagram en recevait 3 -- dont un logo Wikipedia recadre au point que les
lettres etaient coupees, repere APRES publication.

Mesure du 2026-10-04 sur la base vivante : 86 creneaux portent des fichiers
figes ; parmi les 48 carrousels dont les candidats sont encore recalculables,
27 (56 %) ne retombent pas sur le nombre de fichiers reellement figes. Le
recalcul ne dit donc pas ce qui est parti, et ne le dira jamais : l'article est
purge a deux jours (creneau 3 : 5 fichiers figes, 1 seul candidat recalculable
aujourd'hui). Tant que cette liste reste calculee, aucun controle editorial n'est
possible.

CE QUE LE MANIFESTE AJOUTE, et qui n'existait nulle part : les elements ECARTES
au gel figurent AVEC LEUR RAISON. Une image trop petite ou trop eloignee du 4:5
(`images.ImageEcartee`), une video inutilisable retombee sur sa vignette, un
carrousel plein a dix : ces decisions etaient seulement JOURNALISEES, donc
invisibles de l'interface -- c'est precisement ce que l'utilisateur veut voir.

UNE SEULE REGLE A NE PAS PERDRE DE VUE : ce module ne DECIDE rien. Il
ENREGISTRE ce que le gel vient de faire. Si une ligne du manifeste contredisait
les fichiers figes, ce serait exactement l'ecart que tout ce chantier existe pour
supprimer -- d'ou `Figement`, qui porte les deux ensemble et qu'un preparateur
rend d'un seul tenant.
"""

from dataclasses import dataclass, field

# Ce qu'un element EST, sur la place qu'il occupe chez Meta.
COVER = "cover"
MEDIA = "media"


def element(
    *,
    kind: str,
    type: str | None = None,
    origine: str | None = None,
    source_url: str | None = None,
    fichier: str | None = None,
    compte: str | None = None,
    raison: str | None = None,
) -> dict:
    """Une entree du manifeste, en dict simple -- c'est une colonne JSON.

    `fichier` renseigne = l'element PART (c'est le nom du fichier fige) ;
    `fichier` absent = il a ete ECARTE, et `raison` dit pourquoi. Un element qui
    part peut tout de meme porter une `raison` : une video retombee sur sa
    vignette part bien, mais pas sous la forme prevue, et le dire est tout
    l'interet de la liste.
    """
    return {
        "kind": kind,
        "type": type,
        "origine": origine,
        "source_url": source_url,
        "fichier": fichier,
        "compte": compte,
        "raison": raison,
        "ecarte": fichier is None,
    }


@dataclass(frozen=True, slots=True)
class Figement:
    """Ce qu'un preparateur rend : les fichiers figes ET ce qu'ils sont.

    Les deux ensemble et non deux appels : le manifeste ne peut etre juste que
    s'il est construit au moment ou les fichiers s'ecrivent. Un preparateur
    injecte (tests, DI) qui ne rend qu'une liste de noms reste accepte
    (`normaliser`) -- son manifeste vaut alors INCONNU, jamais « vide ».
    """

    noms: list[str] = field(default_factory=list)
    elements: list[dict] | None = None


def normaliser(resultat) -> Figement:
    """Un `Figement`, ou la liste de noms d'un preparateur qui n'en rend pas.

    Le second cas n'est pas une complaisance : `scripts.tester_publication` et
    les preparateurs injectes dans les tests rendent une liste, et un manifeste
    inconnu est une reponse honnete -- la relecture affiche « inconnu », jamais
    une liste inventee.
    """
    if isinstance(resultat, Figement):
        return resultat
    return Figement(noms=list(resultat or []), elements=None)


def nombre_publies(elements: list[dict] | None) -> int | None:
    """Combien d'elements partent reellement, ou None si on ne sait pas."""
    if elements is None:
        return None
    return sum(1 for e in elements if not e.get("ecarte"))


__all__ = ["COVER", "MEDIA", "Figement", "element", "nombre_publies", "normaliser"]
