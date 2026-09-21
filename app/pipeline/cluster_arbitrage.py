"""Arbitrage Claude des reprises douteuses contre l'archive (Task 7, 2026-09-21).

`archive_similarity_from_tokens` (bande "certaine", seuil `cluster_archive_seuil`
= 0.8) et `title_similarity_from_tokens` (le recouvrement, seuil 0.65) se
contredisent sur une bande entiere : 15 reprises plausibles mesurees en
re-revue, 11 y passent entre les mailles (aucun seuil ne peut a la fois
attraper les reprises et epargner les actus neuves -- voir
`cluster.archive_similarity_from_tokens`). C'est cette bande, et elle seule,
que Claude tranche : `cluster.link_duplicates` n'appelle ce module que pour un
article dont le score contre un representant d'archive est sous le seuil
certain mais au-dessus du seuil de recouvrement.

Le verdict est memorise par PAIRE (les deux ids d'article, ordre indifferent)
dans un fichier JSON a cote du jeton -- meme mecanique que
`illustration_cache_path` / `visuels_web_cache_path` : ecriture atomique
(fichier temporaire + `os.replace`), jamais de migration. Une panne (cle
absente, credits epuises, reseau mort, reponse hors schema) n'est JAMAIS
memorisee : seule une reponse effectivement rendue par Claude, conforme au
schema, l'est, sinon une panne passagere figerait "different" pour toujours et
enterrerait une actu neuve sans retour possible.

Un verdict MANUEL (pose par `scripts.marquer_doublon_manuel`) prime sur tout
rapprochement automatique ulterieur, y compris sur la bande certaine : c'est ce
qui rend enfin reelle la procedure de reparation d'un faux positif d'archive
(l'ancienne procedure de CLAUDE.md -- effacer `duplicate_of_id` et
`file_exclu_raison` -- ne tenait pas, le passage suivant relie l'article tant
qu'il reste dans la fenetre de 48 h de sa vie publiable).
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from app.config import settings
from app.models import Article

logger = logging.getLogger(__name__)

CACHE_VERSION = 1


class VerdictArbitrage(BaseModel):
    """Sortie JSON stricte de l'arbitrage -- meme motif que `ArticleScore`
    (`app/pipeline/scoring.py`) : schema impose par `output_format`, revalide
    par Pydantic."""

    meme_actu: bool
    raison: str = Field(max_length=400)


SYSTEM_PROMPT = """\
Tu arbitres une decision de deduplication editoriale pour KINDA, un compte \
Instagram d'actualite musicale.

On te presente DEUX articles : un DEJA PUBLIE par Kinda, et un NOUVEAU qui \
vient d'etre ingere. Un algorithme de similarite de titres est incertain sur \
ces deux-la -- ni assez proches pour etre surs, ni assez differents pour etre \
ecartes sans arbitrage.

Ta question : ces deux articles racontent-ils LA MEME ACTUALITE, ou deux \
actualites DIFFERENTES a propos du meme sujet (meme artiste, meme festival, \
meme label) ?

Regles :
- « Meme actu » veut dire : le meme evenement, la meme annonce, la meme \
sortie -- couvert deux fois, peut-etre dans une autre langue, avec un titre \
different, un angle different, ou des details supplementaires (date, feat., \
nom du label).
- « Actu differente » veut dire : deux evenements distincts a propos du meme \
sujet -- un nouvel EP puis une tournee, une sortie puis une autre, une \
annonce puis son concert. Un artiste peut faire l'actualite plusieurs fois \
dans la meme semaine sans que ce soit deux fois la meme information.
- Si l'un des deux resumes ne permet pas de trancher avec certitude, penche \
vers « actu differente » : rater un lien coute un post de moins, mais un lien \
a tort enterre une actualite neuve pour de bon.

Rends UNIQUEMENT le verdict structure demande. La raison tient en UNE phrase \
courte (15 mots maximum) : elle est stockee comme trace de supervision, pas \
comme une explication detaillee."""


def build_user_prompt(publie: Article, nouveau: Article) -> str:
    def _bloc(article: Article) -> str:
        resume = article.summary or "(pas de resume)"
        return f"Titre : {article.title}\nResume : {resume}"

    return (
        f"ARTICLE DEJA PUBLIE :\n{_bloc(publie)}\n\n"
        f"ARTICLE NOUVEAU :\n{_bloc(nouveau)}"
    )


def cle_paire(id_a: int, id_b: int) -> str:
    """Independante de l'ordre : (12, 40) et (40, 12) sont la MEME paire."""
    lo, hi = sorted((id_a, id_b))
    return f"{lo}:{hi}"


@dataclass(frozen=True)
class EntreeArbitrage:
    meme_actu: bool
    raison: str
    # Pose par l'utilisateur (`scripts.marquer_doublon_manuel`), jamais par
    # `link_duplicates` -- c'est ce champ qui lui fait primer sur tout.
    manuel: bool = False


@dataclass(frozen=True)
class Cache:
    entrees: dict[str, EntreeArbitrage] = field(default_factory=dict)


def _chemin_cache() -> Path:
    return settings.cluster_arbitrage_cache_path


def lire_cache(chemin: Path | None = None) -> Cache:
    """Les verdicts connus. Jamais d'exception : un fichier absent ou corrompu
    vaut un cache vide, pas un run casse. Pas de TTL : un verdict sur une paire
    de titres/resumes figes ne « perime » pas comme la lecture d'une image."""
    chemin = chemin or _chemin_cache()
    try:
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        if brut.get("version") != CACHE_VERSION:
            return Cache({})
        entrees = {
            cle: EntreeArbitrage(
                meme_actu=bool(ligne["meme_actu"]),
                raison=str(ligne["raison"]),
                manuel=bool(ligne.get("manuel", False)),
            )
            for cle, ligne in (brut.get("verdicts") or {}).items()
        }
        return Cache(entrees)
    except Exception as exc:  # noqa: BLE001 -- un cache illisible n'est qu'un cache vide
        logger.info("cache d'arbitrage de doublons illisible (%s)", type(exc).__name__)
        return Cache({})


def ecrire_cache(cache: Cache, chemin: Path | None = None) -> None:
    """Ecriture ATOMIQUE (fichier temporaire + `os.replace`), comme les autres
    caches du projet : un passage interrompu ne laisse pas un JSON tronque."""
    chemin = chemin or _chemin_cache()
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        contenu = {
            "version": CACHE_VERSION,
            "verdicts": {
                cle: {
                    "meme_actu": entree.meme_actu,
                    "raison": entree.raison,
                    "manuel": entree.manuel,
                }
                for cle, entree in cache.entrees.items()
            },
        }
        temporaire = chemin.with_suffix(chemin.suffix + ".tmp")
        temporaire.write_text(json.dumps(contenu, ensure_ascii=False), encoding="utf-8")
        os.replace(temporaire, chemin)
    except Exception as exc:  # noqa: BLE001 -- ne jamais faire echouer un run pour ca
        logger.warning(
            "verdict d'arbitrage de doublons non memorise (%s) : le prochain passage redemandera",
            type(exc).__name__,
        )


def marquer_manuel(
    id_a: int,
    id_b: int,
    meme_actu: bool,
    raison: str,
    chemin: Path | None = None,
) -> None:
    """Enregistre un verdict humain sur une paire -- reparation d'un faux
    positif d'archive, ou confirmation d'un lien qu'on veut garder quoi qu'il
    arrive. Prime sur tout rapprochement automatique ulterieur, y compris la
    bande "certaine" de `link_duplicates`. Utilise par
    `scripts.marquer_doublon_manuel`."""
    cache = lire_cache(chemin)
    cache.entrees[cle_paire(id_a, id_b)] = EntreeArbitrage(meme_actu, raison, manuel=True)
    ecrire_cache(cache, chemin)


def make_claude_arbitre(client: anthropic.Anthropic | None = None):
    """Un arbitre par defaut : un appel API par paire douteuse, sortie
    structuree validee -- meme motif que `scoring.make_claude_scorer`.

    Rend `(meme_actu, raison)` sur une reponse conforme au schema, `None` sur
    TOUTE panne (cle absente, credits epuises, reseau mort, JSON hors schema) :
    le doute favorise la publication, jamais un lien par defaut, et une panne
    n'est jamais memorisee par l'appelant.
    """
    if client is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def arbitre(publie: Article, nouveau: Article) -> tuple[bool, str] | None:
        try:
            response = client.messages.parse(
                model=settings.scoring_model,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_prompt(publie, nouveau)}],
                output_format=VerdictArbitrage,
            )
            sortie = response.parsed_output
            if sortie is None:
                logger.warning(
                    "arbitrage de doublon sans sortie structuree (%s, %s)",
                    publie.id, nouveau.id,
                )
                return None
            return sortie.meme_actu, sortie.raison
        except Exception as exc:  # noqa: BLE001 -- une panne n'est jamais un verdict
            logger.warning(
                "arbitrage de doublon en echec pour (%s, %s) : %s (%s)",
                publie.id, nouveau.id, exc, type(exc).__name__,
            )
            return None

    return arbitre
