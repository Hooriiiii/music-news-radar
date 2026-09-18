"""Quelle TEINTE porte une cover : le choix, pas les valeurs.

`layout.py` dit ce que vaut une palette (les couleurs, leurs opacites) ; ce
module dit LAQUELLE s'applique. La separation n'est pas cosmetique : les valeurs
sont de la mise en page (testables sans base, sans reseau), le choix est une
regle EDITORIALE qui lit l'article.

La regle, demandee par l'utilisateur le 2026-09-18 : « pour les actus en lien
avec l'ecologie, je veux que la couleur soit verte ». Le signal est la categorie
de scoring `ecology` (cf. `app/pipeline/scoring.py`), et non une detection de
mots-cles dans le titre : la categorie est deja produite par le modele, qui a lu
l'article entier, alors qu'un mot-cle teindrait en vert le premier festival dont
le nom contient « Green ».

Le nom de palette est FIGE dans `posts.variants["palette"]` a la composition.
Deux raisons : l'article est purge a 14 jours et le post doit rester rendable
apres (meme motif que les champs denormalises de `Post`), et le champ est
editable -- le dashboard pourra forcer une teinte sans re-scorer l'article.
"""

from app.content.layout import PALETTE_ROSE, PALETTES, Palette, palette
from app.pipeline.scoring import Category

NOM_PAR_DEFAUT = PALETTE_ROSE.nom

# La seule categorie teintee a ce jour. Table et non `if` : ajouter une couleur
# par categorie (un rouge pour `rip` ?) ne demanderait alors aucune logique.
TEINTE_PAR_CATEGORIE: dict[str, str] = {
    Category.ECOLOGY.value: "verte",
}


def teinte_de_article(article) -> str:
    """Le nom de palette que merite cet article. `getattr` : des appelants
    passent des articles partiels (SimpleNamespace sans categorie)."""
    categorie = getattr(article, "category", None)
    return TEINTE_PAR_CATEGORIE.get(categorie or "", NOM_PAR_DEFAUT)


def teinte_du_post(post) -> str:
    """Le nom de palette fige sur le post, la charte a defaut.

    Tolere tout : `variants` NULL (post anterieur a la regle), `variants` sans
    cle `palette`, nom inconnu pose a la main. Un rendu ne doit jamais echouer
    faute de teinte -- il retombe sur le rose.
    """
    variants = getattr(post, "variants", None) or {}
    nom = variants.get("palette") if isinstance(variants, dict) else None
    return nom if nom in PALETTES else NOM_PAR_DEFAUT


def palette_du_post(post) -> Palette:
    return palette(teinte_du_post(post))
