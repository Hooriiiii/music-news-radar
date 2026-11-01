"""Mesurer la notation Haiku 4.5 contre Haiku 5.5, sur des articles encore non notes.

    uv run python -m scripts.diagnostic.mesurer_modele_notation [--taille N] [--graine G]
        [--sortie DOSSIER] [--apply]

Sans `--apply` : le plan et le cout estime, aucun appel. Avec : appelle Claude, et
n'ecrit JAMAIS en base. Aucun commit, session en lecture seule, fermee avant le premier
appel (Neon tue une session tenue pendant l'attente). Ne touche pas a `credits_anthropic`.

POURQUOI DES ARTICLES NON NOTES. Une note ecrase le resume brut : un article deja note
ne peut plus etre re-note a entrees egales. Les memes filtres que `score_pending`, moins
les posts quasi vides, que Claude ne note jamais.

POURQUOI DEUX PASSES 4.5. La seconde mesure le BRUIT du modele contre lui-meme : c'est
lui qui permet de juger l'ecart avec 5.5 sans seuil a l'oeil. Le script affiche les
chiffres et ne rend pas de verdict.

Le tirage prend chaque strate a tour de role : les petites strates sont surrepresentees
par rapport au flux, c'est voulu pour qu'elles soient mesurees. Les moyennes globales ne
decrivent donc pas le flux reel : lire aussi le detail par categorie.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import random
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import anthropic
from sqlalchemy import or_, select, text
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionLocal, attendre_base
from app.models import Article
from app.pipeline.credits_anthropic import est_refus_de_credits
from app.pipeline.retrait_humain import RAISONS_HUMAINES
from app.pipeline.scoring import (
    MAX_TOKENS_NOTATION,
    SYSTEM_PROMPT,
    ArticleScore,
    ReponseSansNote,
    build_user_prompt,
    make_claude_scorer,
    post_quasi_vide,
)
from scripts.diagnostic.consommation_api import CHAMPS_TOKENS, Agregat, cout_ligne

logger = logging.getLogger(__name__)

POSTE = "mesure_notation"
HAIKU_4_5 = "claude-haiku-4-5"
HAIKU_5_5 = "claude-haiku-5-5"
PASSES = (("a", HAIKU_4_5), ("b", HAIKU_4_5), ("c", HAIKU_5_5))
COMPARAISONS = {"bruit_4_5_contre_4_5": ("a", "b"), "modele_4_5_contre_5_5": ("a", "c")}
AXES = ("relevance", "hotness", "emergence")
# Les bornes du feed et de l'A&R citees par la decision, pas des reglages.
SEUILS = {"relevance": 60, "emergence": 50}

# Hypotheses du plan, aucune n'est une mesure : la mesure sert justement a les remplacer.
CARACTERES_PAR_TOKEN = 3.0
SORTIE_ESTIMEE_TOKENS = 200
SURCOUT_TOKENS_5_5 = 1.3  # guide de migration : 30 % de tokens en plus


def charger_eligibles(usine) -> list[Article]:
    """Les articles que `score_pending` enverrait a Claude, detaches de la session."""
    with usine() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
        articles = db.scalars(
            select(Article)
            .options(selectinload(Article.source))
            .where(Article.relevance_score.is_(None))
            .where(Article.duplicate_of_id.is_(None))
            .where(
                or_(
                    Article.file_exclu_raison.is_(None),
                    Article.file_exclu_raison.not_in(RAISONS_HUMAINES),
                )
            )
        ).all()
        retenus = [a for a in articles if not post_quasi_vide(a)]
        # Dans cet ordre : un rollback expirerait les objets encore attaches.
        db.expunge_all()
        db.rollback()
    return retenus


def categorie(article: Article) -> str:
    valeur = article.source.categorie if article.source is not None else None
    return valeur.value if valeur is not None else "inconnu"


def strate(article: Article) -> str:
    """`sources.genre` porte « pop » ou « Pop » pour les sources pop et K-pop."""
    genre = (article.source.genre or "") if article.source is not None else ""
    return f"{categorie(article)}/{'pop' if genre.strip().lower() == 'pop' else 'autre'}"


def tirer(articles: list[Article], taille: int, graine: int) -> list[Article]:
    """Reproductible quel que soit l'ordre d'entree : chaque strate a tour de role."""
    alea = random.Random(graine)
    strates: dict[str, list[Article]] = defaultdict(list)
    for article in sorted(articles, key=lambda a: a.id):
        strates[strate(article)].append(article)
    for nom in sorted(strates):
        alea.shuffle(strates[nom])
    tirage: list[Article] = []
    while len(tirage) < taille and any(strates.values()):
        for nom in sorted(strates):
            if strates[nom] and len(tirage) < taille:
                tirage.append(strates[nom].pop())
    return tirage


def _tokens_entree(article: Article) -> int:
    schema = json.dumps(ArticleScore.model_json_schema())
    caracteres = len(SYSTEM_PROMPT) + len(schema) + len(build_user_prompt(article))
    return round(caracteres / CARACTERES_PAR_TOKEN)


def _cout_estime(tirage: list[Article], sortie_tokens: int) -> float:
    total = 0.0
    for article in tirage:
        entree = _tokens_entree(article)
        for _, modele in PASSES:
            facteur = SURCOUT_TOKENS_5_5 if modele == HAIKU_5_5 else 1.0
            total += cout_ligne(
                {
                    "modele": modele,
                    "input_tokens": round(entree * facteur),
                    "output_tokens": round(sortie_tokens * facteur),
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                }
            )
    return total


def plan(eligibles: list[Article], tirage: list[Article], graine: int) -> str:
    population, tires = Counter(map(strate, eligibles)), Counter(map(strate, tirage))
    lignes = [
        f"{len(eligibles)} article(s) eligible(s), {len(tirage)} tire(s) (graine {graine}), "
        f"{len(tirage) * len(PASSES)} appels : {HAIKU_4_5} deux fois, {HAIKU_5_5} une fois.",
        "",
        f"{'strate':<24} {'eligibles':>9} {'tires':>6}",
    ]
    lignes += [f"{nom:<24} {population[nom]:>9} {tires[nom]:>6}" for nom in sorted(population)]
    lignes += [
        "",
        f"Cout estime : ~${_cout_estime(tirage, SORTIE_ESTIMEE_TOKENS):.2f}, plafond "
        f"~${_cout_estime(tirage, MAX_TOKENS_NOTATION):.2f} si chaque appel atteint max_tokens.",
        f"Hypotheses, pas des mesures : {CARACTERES_PAR_TOKEN:g} caracteres par token, sortie de "
        f"{SORTIE_ESTIMEE_TOKENS} tokens, +30 % sur 5.5, aucun cache. Reflexion de 5.5 inconnue.",
    ]
    return "\n".join(lignes)


class _Espion:
    """Garde la derniere reponse : le scorer ne rend que la note, la mesure veut l'usage."""

    def __init__(self, client):
        self._client = client
        self.messages = self
        self.derniere = None

    def parse(self, **kwargs):
        self.derniere = None
        self.derniere = self._client.messages.parse(**kwargs)
        return self.derniere


def _ligne_usage(modele: str, reponse) -> dict:
    usage = getattr(reponse, "usage", None)
    return {
        "poste": POSTE,
        "modele": modele,
        **{champ: getattr(usage, champ, None) for champ in CHAMPS_TOKENS},
    }


@dataclasses.dataclass
class Mesure:
    articles: list[dict] = dataclasses.field(default_factory=list)
    statuts: dict[str, Counter] = dataclasses.field(
        default_factory=lambda: {HAIKU_4_5: Counter(), HAIKU_5_5: Counter()}
    )
    consommation: dict[str, Agregat] = dataclasses.field(
        default_factory=lambda: {HAIKU_4_5: Agregat(), HAIKU_5_5: Agregat()}
    )
    arret: str | None = None


def mesurer(tirage: list[Article], client) -> Mesure:
    espion = _Espion(client)
    scorers = {
        passe: make_claude_scorer(espion, modele=modele, poste=POSTE) for passe, modele in PASSES
    }
    mesure = Mesure()
    for article in tirage:
        resultat = {
            "id": article.id,
            "titre": article.title,
            "categorie": categorie(article),
            "strate": strate(article),
            "notes": {},
        }
        mesure.articles.append(resultat)
        for passe, modele in PASSES:
            statut = "note"
            try:
                score = scorers[passe](article)
                resultat["notes"][passe] = {axe: getattr(score, axe) for axe in AXES}
            except ReponseSansNote as exc:
                statut = "refus" if exc.stop_reason == "refusal" else "vide"
            except ValueError:
                # Le SDK valide avant de rendre la reponse : un JSON coupe par max_tokens ou un
                # refus en texte arrivent ici, sans stop_reason ni usage lisibles.
                statut = "invalide"
            except Exception as exc:  # noqa: BLE001 -- compter, ne pas planter
                if est_refus_de_credits(exc):
                    mesure.arret = f"credits epuises sur {modele} : {exc}"
                    return mesure
                logger.warning("mesure en erreur sur l'article %s : %s", article.id, exc)
                statut = "erreur"
            mesure.statuts[modele][statut] += 1
            # Une erreur d'API n'a rien facture de lisible et n'a pas forcement ete facturee :
            # elle reste dans `statuts`, hors du cout. Une sortie invalide, elle, a ete payee.
            if statut != "erreur":
                mesure.consommation[modele].ajouter(_ligne_usage(modele, espion.derniere))
    return mesure


def _comparer(articles: list[dict], gauche: str, droite: str) -> dict:
    """`delta_moyen` : droite moins gauche. Seuls comptent les articles notes des deux cotes."""
    paires = [
        (a["notes"][gauche], a["notes"][droite])
        for a in articles
        if gauche in a["notes"] and droite in a["notes"]
    ]
    axes = {}
    for axe in AXES:
        deltas = [d[axe] - g[axe] for g, d in paires]
        axes[axe] = {
            "n": len(deltas),
            "delta_moyen": sum(deltas) / len(deltas) if deltas else None,
            "delta_absolu": sum(map(abs, deltas)) / len(deltas) if deltas else None,
        }
        if axe in SEUILS:
            axes[axe]["franchissements"] = sum(
                (g[axe] >= SEUILS[axe]) != (d[axe] >= SEUILS[axe]) for g, d in paires
            )
    return axes


def comparaisons(articles: list[dict]) -> dict:
    sortie = {}
    for nom, (gauche, droite) in COMPARAISONS.items():
        par_categorie = {
            cat: _comparer([a for a in articles if a["categorie"] == cat], gauche, droite)
            for cat in sorted({a["categorie"] for a in articles})
        }
        sortie[nom] = {"global": _comparer(articles, gauche, droite), "categories": par_categorie}
    return sortie


def franchissements(articles: list[dict]) -> list[dict]:
    liste = []
    for article in articles:
        for nom, (gauche, droite) in COMPARAISONS.items():
            notes = article["notes"]
            if gauche not in notes or droite not in notes:
                continue
            for axe, seuil in SEUILS.items():
                if (notes[gauche][axe] >= seuil) != (notes[droite][axe] >= seuil):
                    liste.append(
                        {
                            "id": article["id"],
                            "titre": article["titre"],
                            "categorie": article["categorie"],
                            "comparaison": nom,
                            "axe": axe,
                            "seuil": seuil,
                            "de": notes[gauche][axe],
                            "a": notes[droite][axe],
                        }
                    )
    return liste


def _nombre(valeur: float | None) -> str:
    return "INCONNU" if valeur is None else f"{valeur:+.1f}"


def _rangees(axes: dict, prefixe: str) -> list[str]:
    lignes = []
    for axe in AXES:
        chiffres = axes[axe]
        passages = chiffres.get("franchissements")
        fin = "" if passages is None else f"  franchit {SEUILS[axe]} : {passages}"
        absolu = chiffres["delta_absolu"]
        lignes.append(
            f"{prefixe}{axe:<10} n={chiffres['n']:<4} delta moyen {_nombre(chiffres['delta_moyen'])}"
            f"  absolu {'INCONNU' if absolu is None else f'{absolu:.1f}'}{fin}"
        )
    return lignes


def _consommation(agregat: Agregat) -> dict:
    tokens = {champ: agregat.tokens[champ] for champ in CHAMPS_TOKENS}
    return {
        "appels": agregat.appels,
        "cout": agregat.cout if agregat.appels > agregat.sans_cout else None,
        "appels_au_cout_inconnu": agregat.sans_cout,
        **{champ: compteur.somme for champ, compteur in tokens.items()},
    }


def donnees(mesure: Mesure, graine: int) -> dict:
    return {
        "graine": graine,
        "arret": mesure.arret,
        "comparaisons": comparaisons(mesure.articles),
        "franchissements": franchissements(mesure.articles),
        "statuts": {modele: dict(compte) for modele, compte in mesure.statuts.items()},
        "consommation": {m: _consommation(a) for m, a in mesure.consommation.items()},
        "articles": mesure.articles,
    }


def rapport(resultats: dict, mesure: Mesure) -> str:
    lignes = [f"{len(mesure.articles)} article(s) mesure(s)."]
    for nom, comparaison in resultats["comparaisons"].items():
        lignes += ["", nom, *_rangees(comparaison["global"], "  ")]
        for cat, axes in comparaison["categories"].items():
            lignes += [f"  {cat}", *_rangees(axes, "    ")]
    lignes += ["", "Sorties par modele (invalide : JSON coupe ou refus en texte, cout inconnu)"]
    for modele, compte in mesure.statuts.items():
        details = ", ".join(f"{c} {compte[c]}" for c in ("note", "refus", "vide", "invalide", "erreur"))
        lignes.append(f"  {modele} : {details}")
    lignes += ["", "Consommation reelle, depuis `usage`"]
    for modele, agregat in mesure.consommation.items():
        tokens = " ".join(f"{champ}={agregat.tokens[champ]}" for champ in CHAMPS_TOKENS)
        cout = f"${agregat.cout:.4f}"
        if agregat.sans_cout:
            cout = f"{cout} + INCONNU ({agregat.sans_cout} appel(s))"
        lignes.append(f"  {modele} : {agregat.appels} appels, {cout}, {tokens}")
    lignes += ["", f"Franchissements : {len(resultats['franchissements'])}"]
    for f in resultats["franchissements"]:
        lignes.append(
            f"  #{f['id']} [{f['categorie']}] {f['comparaison']} {f['axe']} {f['de']} -> {f['a']} "
            f"(seuil {f['seuil']}) : {f['titre']}"
        )
    return "\n".join(lignes)


def main(argv=None, usine=SessionLocal, client=None, attendre=attendre_base) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--taille", type=int, default=60, help="articles tires (defaut : 60)")
    parser.add_argument("--graine", type=int, default=20261101, help="graine du tirage")
    parser.add_argument(
        "--sortie", type=Path, default=Path(tempfile.gettempdir()),
        help="dossier du JSON (defaut : dossier temporaire du systeme)",
    )
    parser.add_argument("--apply", action="store_true", help="appelle Claude (aucune ecriture en base)")
    args = parser.parse_args(argv)
    if args.taille < 1:
        parser.error("--taille doit valoir au moins 1")

    print(
        "ARRETER pipeline et veille avant --apply, sinon la recharge sera consommee par "
        "l'arriere ; ce script ne les arrete pas."
    )
    attendre()
    eligibles = charger_eligibles(usine)
    if not eligibles:
        print("Aucun article eligible : rien a mesurer, aucun appel.")
        return 0
    tirage = tirer(eligibles, args.taille, args.graine)
    print(plan(eligibles, tirage, args.graine))
    if not args.apply:
        print("\nPlan seulement : aucun appel. Relancer avec --apply.")
        return 0

    if client is None:
        if not settings.anthropic_api_key:
            print("ANTHROPIC_API_KEY absente : aucun appel.")
            return 1
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    mesure = mesurer(tirage, client)
    resultats = donnees(mesure, args.graine)
    print("\n" + rapport(resultats, mesure))
    args.sortie.mkdir(parents=True, exist_ok=True)
    horodatage = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    chemin = args.sortie / f"mesure_notation-{horodatage}.json"
    chemin.write_text(json.dumps(resultats, ensure_ascii=False, indent=2))
    print(f"\nJSON ecrit : {chemin}")
    if mesure.arret:
        print(f"ARRET, plus aucun appel : {mesure.arret}. Resultats partiels.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
