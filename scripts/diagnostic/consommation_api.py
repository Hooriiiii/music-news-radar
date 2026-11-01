"""Ce que les appels Anthropic ont consomme, par jour et par poste.

    uv run python -m scripts.diagnostic.consommation_api [--jours N]

Lecture seule. Aucune base, aucun appel reseau, aucune ecriture : ne lit que les
`usage_anthropic-AAAA-MM.jsonl` poses par `app/pipeline/usage_anthropic.py`.

POURQUOI. Une depense qu'on ne peut pas verifier ne se pilote pas. Mais un total faux
est pire qu'aucun total : un modele hors table, ou une reponse invalide consignee a
`null`, ne valent pas 0 $. Leur cout s'affiche INCONNU, le nombre de lignes concernees
est dit, et le total annonce ce qu'il exclut. Sans fichier, on dit qu'il n'y a pas de
donnee, jamais « 0 $ ».

Le cout reste une ESTIMATION a partir des tarifs ci-dessous : la facture fait foi.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.pipeline.usage_anthropic import PREFIXE_FICHIER

# Le seul endroit des tarifs : $ par million de tokens (entree, sortie), doc Anthropic
# relevee le 2026-11-01. Haiku 5.5 passe a 0,50/2,50 au-dela de 100 000 tokens de
# prompt ; aucun poste n'en approche, ce palier n'est pas modelise.
PRIX_PAR_MILLION = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-5-5": (2.0, 10.0),
}
FACTEUR_LECTURE_CACHE = 0.1
FACTEUR_ECRITURE_CACHE_5_MIN = 1.25
PRIX_PAR_RECHERCHE_WEB = 10.0 / 1000

# Seul ce poste declare l'outil `web_search` : ailleurs, `null` veut dire qu'aucune
# recherche n'a pu avoir lieu ; ici, on ne sait pas combien ont ete facturees.
POSTES_AVEC_RECHERCHE = {"recherche_web"}

SUFFIXE_DATE = re.compile(r"-\d{8}$")
CHAMPS_TOKENS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _nombre(valeur) -> int | None:
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) else None


def cout_ligne(ligne: dict) -> float | None:
    """Le cout estime en dollars, `None` des qu'un terme n'est pas connu."""
    modele = ligne.get("modele")
    prix = PRIX_PAR_MILLION.get(SUFFIXE_DATE.sub("", modele)) if isinstance(modele, str) else None
    if prix is None:
        return None
    entree, sortie, lu, ecrit = (_nombre(ligne.get(champ)) for champ in CHAMPS_TOKENS)
    if None in (entree, sortie, lu, ecrit):
        return None
    recherches = _nombre(ligne.get("web_search_requests"))
    if recherches is None:
        if ligne.get("poste") in POSTES_AVEC_RECHERCHE:
            return None
        recherches = 0
    prix_entree, prix_sortie = prix
    return (
        entree * prix_entree
        + sortie * prix_sortie
        + lu * prix_entree * FACTEUR_LECTURE_CACHE
        + ecrit * prix_entree * FACTEUR_ECRITURE_CACHE_5_MIN
    ) / 1_000_000 + recherches * PRIX_PAR_RECHERCHE_WEB


@dataclass
class Compteur:
    somme: int = 0
    inconnues: int = 0

    def ajouter(self, valeur: int | None) -> None:
        if valeur is None:
            self.inconnues += 1
        else:
            self.somme += valeur

    def __str__(self) -> str:
        if not self.inconnues:
            return str(self.somme)
        if not self.somme:
            return "INCONNU"
        return f"{self.somme}+{self.inconnues}?"


@dataclass
class Agregat:
    appels: int = 0
    cout: float = 0.0
    sans_cout: int = 0
    tokens: dict[str, Compteur] = field(
        default_factory=lambda: {champ: Compteur() for champ in CHAMPS_TOKENS}
    )
    recherches: Compteur = field(default_factory=Compteur)

    def ajouter(self, ligne: dict) -> None:
        self.appels += 1
        for champ, compteur in self.tokens.items():
            compteur.ajouter(_nombre(ligne.get(champ)))
        recherches = _nombre(ligne.get("web_search_requests"))
        if recherches is not None or ligne.get("poste") in POSTES_AVEC_RECHERCHE:
            self.recherches.ajouter(recherches)
        cout = cout_ligne(ligne)
        if cout is None:
            self.sans_cout += 1
        else:
            self.cout += cout


def _lignes_txt(nombre: int) -> str:
    return f"{nombre} ligne" if nombre == 1 else f"{nombre} lignes"


def _cout_txt(agregat: Agregat) -> str:
    if not agregat.sans_cout:
        return f"${agregat.cout:.4f}"
    inconnu = f"INCONNU ({_lignes_txt(agregat.sans_cout)})"
    if agregat.sans_cout == agregat.appels:
        return inconnu
    return f"${agregat.cout:.4f} + {inconnu}"


def _date_de(ligne: dict) -> dt.datetime:
    moment = dt.datetime.fromisoformat(ligne["le"])  # KeyError, TypeError, ValueError
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.timezone.utc)


@dataclass
class Lecture:
    lignes: list[tuple[dt.datetime, dict]] = field(default_factory=list)
    illisibles: int = 0
    fichiers: int = 0
    fichiers_illisibles: list[str] = field(default_factory=list)


def lire(dossier: Path) -> Lecture:
    lecture = Lecture()
    for chemin in sorted(dossier.glob(f"{PREFIXE_FICHIER}*.jsonl")):
        lecture.fichiers += 1
        try:
            texte = chemin.read_text(encoding="utf-8")
        except (OSError, ValueError):
            lecture.fichiers_illisibles.append(chemin.name)
            continue
        for brute in texte.splitlines():
            try:
                ligne = json.loads(brute)
                moment = _date_de(ligne)
                if not isinstance(ligne.get("poste"), str):
                    raise ValueError("poste absent")
            except (KeyError, TypeError, ValueError, AttributeError):
                lecture.illisibles += 1
                continue
            lecture.lignes.append((moment, ligne))
    return lecture


def rapport(dossier: Path, jours: int, maintenant: dt.datetime) -> str:
    lecture = lire(dossier)
    debut = (maintenant.astimezone(dt.timezone.utc) - dt.timedelta(days=jours - 1)).date()
    retenues = [
        (moment, ligne)
        for moment, ligne in lecture.lignes
        if moment.astimezone(dt.timezone.utc).date() >= debut
    ]
    sortie = [f"Consommation Anthropic depuis le {debut} (UTC), lue dans {dossier}", ""]

    alertes = []
    if lecture.fichiers_illisibles:
        noms = ", ".join(lecture.fichiers_illisibles)
        alertes.append(f"ATTENTION : fichier illisible, non compte : {noms}")
    if lecture.illisibles:
        nombre = lecture.illisibles
        pluriel = "illisible" if nombre == 1 else "illisibles"
        alertes.append(
            f"ATTENTION : {_lignes_txt(nombre)} {pluriel}, date inconnue, "
            "exclues de tout ce qui suit"
        )

    if not retenues:
        if not alertes:
            motif = (
                "aucun fichier consigne"
                if lecture.fichiers == 0
                else f"aucun appel sur les {jours} derniers jours"
            )
            sortie.append(f"Aucune donnee : {motif}. La depense est INCONNUE, pas nulle.")
        else:
            sortie.append("Aucun appel lisible sur la periode : la depense est INCONNUE.")
        return "\n".join(sortie + alertes)

    groupes: dict[tuple[str, str], Agregat] = defaultdict(Agregat)
    total = Agregat()
    for moment, ligne in sorted(retenues, key=lambda paire: paire[0]):
        jour = moment.astimezone(dt.timezone.utc).date().isoformat()
        groupes[(jour, ligne["poste"])].ajouter(ligne)
        total.ajouter(ligne)

    gabarit = "{:<10}  {:<14} {:>6} {:>12} {:>12} {:>12} {:>12} {:>10}  {}"
    sortie.append(
        gabarit.format(
            "jour", "poste", "appels", "entree", "sortie", "cache lu", "cache ecrit", "recherches",
            "cout estime",
        )
    )

    def rangee(jour: str, poste: str, agregat: Agregat) -> str:
        return gabarit.format(
            jour, poste, agregat.appels, *(str(agregat.tokens[c]) for c in CHAMPS_TOKENS),
            str(agregat.recherches), _cout_txt(agregat),
        )

    for (jour, poste), agregat in sorted(groupes.items()):
        sortie.append(rangee(jour, poste, agregat))
    sortie.append("")
    # Sur la rangee meme : un total qu'on recopie doit emporter ce qu'il exclut.
    exclusion = " -- exclu du total" if total.sans_cout else ""
    sortie.append(rangee("TOTAL", "", total) + exclusion)
    if total.sans_cout:
        sortie.append(
            f"Le total estime EXCLUT {_lignes_txt(total.sans_cout)} au cout inconnu "
            "(modele hors table ou tokens null) : la depense reelle est superieure."
        )
    return "\n".join(sortie + alertes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jours", type=int, default=7, help="fenetre en jours (defaut : 7)")
    args = parser.parse_args(argv)
    if args.jours < 1:
        parser.error("--jours doit valoir au moins 1")
    print(
        rapport(
            settings.meta_token_path.parent,
            jours=args.jours,
            maintenant=dt.datetime.now(dt.timezone.utc),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
