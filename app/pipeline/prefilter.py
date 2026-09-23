"""Pré-filtre local : un petit modèle gratuit dit si l'article mérite un appel Claude.

Ce module a UNE responsabilité : demander une note au modèle local et la rendre.
Il ne connaît NI la base NI Claude — c'est `scoring.score_pending` qui décide quoi
faire de la note.

Pourquoi un pré-filtre et pas un remplaçant : mesuré le 2026-08 sur 36 articles
réels, le modèle local (Qwen3-4B via LM Studio) n'a JAMAIS noté sous 60 un article
que Haiku gardait au-dessus de 60. Toutes ses erreurs sont des faux positifs — il
laisse passer trop, il n'écarte jamais à tort. Mauvais juge, bon videur.

Le prompt système et le schéma de sortie sont ceux du scoring (`scoring.py`) :
surtout PAS une copie, sinon les deux étages jugeraient sur des règles différentes
et la comparaison mesurée ne voudrait plus rien dire. Une seule consigne s'y
ajoute (`CONSIGNE_PREFILTRE`) : elle empêche le 4B, plus brutal, d'écarter ce que
Claude garderait.
"""

import json
import logging

import httpx
from pydantic import ValidationError

from app.config import settings
from app.models import Article
from app.pipeline.scoring import SYSTEM_PROMPT, ArticleScore, build_user_prompt

logger = logging.getLogger(__name__)

# Les modèles Qwen3 raisonnent à voix haute par défaut ; ce monologue arrive
# devant le JSON et le rend illisible. Deux ceintures, aucune n'étant fiable
# seule selon la version du template chargée dans LM Studio :
#   - `chat_template_kwargs: {"enable_thinking": False}` côté requête,
#   - `/no_think` à la fin du message utilisateur.
SUFFIXE_SANS_REFLEXION = "\n\n/no_think"

# La SEULE addition au prompt du scoring. Le pré-filtre n'a qu'un rôle : ne jamais
# écarter ce que Claude garderait à 60+. Appliquée avec la brutalité d'un 4B, la
# règle 6 (posts sans information) ferait passer sous le seuil (45) des contenus
# qui portent un fait. On la réserve donc aux posts réellement vides.
#
# Ajout du 2026-09-23 (resserrement de la règle 2, pop = territoire adjacent
# conditionnel) : depuis que le pilier A et la règle 1 du prompt ne citent plus
# la pop comme cœur de ligne inconditionnel, un 4B brutal peut enchaîner
# « pop ≠ cœur de ligne, pop ∉ axes prioritaires -> hors ligne » et faire
# tomber sous 45 un article que Claude aurait gardé à 75 -- sans jamais l'y
# envoyer, donc sans que rien ne le rattrape. La pop n'est PAS une actu hors
# ligne : elle reste dans la grille normale, et la question « remplit-elle une
# condition de la règle 2 ? » est un jugement fin réservé au juge d'après.
CONSIGNE_PREFILTRE = """

CONSIGNE PROPRE AU PRÉ-FILTRE. Tu fais un premier tri avant un juge plus fin. \
Ne descends sous 45 que pour le bruit évident, l'actu hors ligne qui ne relève \
d'aucun axe prioritaire et les posts réellement vides (règle 6 : légende absente, \
emojis ou hashtags seuls). Un contenu qui porte un fait (nom connu, date, sortie, \
événement, chiffre) vaut 45 ou plus, quel que soit le type de compte. La POP et la \
K-POP (règle 2) ne sont PAS une actu hors ligne : ne les range jamais sous 45 pour \
ce seul motif. Elles restent dans la grille normale -- juger si une actu pop \
remplit une des conditions du territoire adjacent est un jugement fin, réservé au \
juge d'après, pas à ce premier tri.\
"""


def _extraire_json(contenu: str) -> str:
    """Isole le JSON d'une réponse, même si un <think> a survécu aux garde-fous."""
    if "</think>" in contenu:
        contenu = contenu.split("</think>", 1)[1]
    return contenu.strip()


def make_local_scorer(
    client: httpx.Client | None = None,
    url: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
):
    """Renvoie un appelable `article -> ArticleScore | None`.

    `None` signifie « le pré-filtre n'a rien à dire » : réseau coupé, LM Studio
    éteint, timeout, HTTP en erreur, JSON illisible ou hors schéma. AUCUNE
    exception ne sort d'ici — l'appelant doit alors passer par Claude. C'est la
    décision de repli assumée : un outil local optionnel ne bloque jamais le
    pipeline.
    """
    url = url or settings.local_scoring_url
    model = model or settings.local_scoring_model
    timeout = timeout if timeout is not None else settings.local_scoring_timeout
    client = client or httpx.Client(timeout=timeout)

    def noter(article: Article) -> ArticleScore | None:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + CONSIGNE_PREFILTRE},
                {
                    "role": "user",
                    "content": build_user_prompt(article) + SUFFIXE_SANS_REFLEXION,
                },
            ],
            # Basse, pas nulle : on veut de la reproductibilité, pas de l'inspiration
            "temperature": 0.2,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "article_score",
                    "strict": True,
                    "schema": ArticleScore.model_json_schema(),
                },
            },
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            reponse = client.post(url, json=payload, timeout=timeout)
            reponse.raise_for_status()
            contenu = reponse.json()["choices"][0]["message"]["content"]
            return ArticleScore.model_validate_json(_extraire_json(contenu))
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            logger.warning(
                "Pré-filtre local muet pour l'article %s (%s) : %s",
                article.id,
                type(exc).__name__,
                exc,
            )
            return None

    return noter
