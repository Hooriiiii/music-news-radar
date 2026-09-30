"""S'assure que le pre-filtre local est debout : serveur LM Studio demarre et
modele charge avec un contexte suffisant.

    uv run python -m scripts.assurer_prefiltre [--verbeux]

POURQUOI CE SCRIPT EXISTE. Le pre-filtre local (`app/pipeline/prefilter.py`)
ecarte le bruit evident SANS payer Claude. Quand il est muet, rien ne casse --
l'article part chez Claude et la note reste bonne -- mais on paie exactement ce
qu'il existe pour ne pas payer. Or il etait muet le 2026-09-24 et personne ne
l'a su : il ne produit que des WARNING par article. D'ou, dans le meme geste,
le compteur `ScoringStats.prefiltre_muet` (qui rend la panne VISIBLE) et ce
script (qui la rend RARE).

DEUX PANNES DISTINCTES, ET LA SECONDE EST LA PLUS SOURNOISE.
1. LM Studio eteint : le pre-filtre recoit un `ConnectError`. Visible au premier
   coup d'oeil dans le log.
2. Modele charge avec un contexte TROP COURT : LM Studio repond `400` sur les
   seuls articles longs -- c'est-a-dire qu'il echoue precisement sur ceux qui
   coutent le plus cher chez Claude, et reussit sur les autres. Le pre-filtre
   a alors l'air de fonctionner. Mesure le 2026-09-24 : le defaut (4096) ne
   suffit pas au SYSTEM_PROMPT (~180 lignes) plus un article long, et ce
   contexte n'est PAS persistant -- un rechargement a la main depuis
   l'interface le ramene a 4096 et ramene la panne avec lui.

Le script est IDEMPOTENT et SILENCIEUX quand tout va bien : il ne parle que
s'il a agi ou echoue, pour qu'un agent qui tourne toutes les demi-heures ne
remplisse pas son log d'une ligne « rien a faire ».

Il ne fait RIEN quand `LOCAL_SCORING_ENABLED` est faux : ne pas vouloir de
pre-filtre est un choix, pas une panne, et on ne lance pas une application de
2 Go pour quelqu'un qui n'en veut pas.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from app.config import settings

# launchd ne fournit qu'un PATH minimal, et `lms` vit dans le dossier de
# l'utilisateur, jamais dans /usr/local/bin -- meme piege que ffmpeg et
# cloudflared (`app/content/video.py::EMPLACEMENTS_CONNUS`). Sans cette
# recherche explicite, l'agent echoue avec un « command not found » que
# personne ne relie au pre-filtre.
EMPLACEMENTS_CONNUS = (
    Path.home() / ".lmstudio" / "bin" / "lms",
    Path.home() / ".cache" / "lm-studio" / "bin" / "lms",
    Path("/usr/local/bin/lms"),
    Path("/opt/homebrew/bin/lms"),
)

# Le chargement d'un modele de 2 Go prend ~6-10 s mesurees ; le demarrage du
# serveur lance l'application. Large, mais borne : un agent qui pend bloquerait
# le suivant (launchd ne chevauche pas `StartInterval`).
DELAI_COMMANDE_S = 180


def trouver_lms() -> Path | None:
    """Le binaire `lms`, cherche HORS du PATH de launchd."""
    sur_le_path = shutil.which("lms")
    if sur_le_path:
        return Path(sur_le_path)
    return next((chemin for chemin in EMPLACEMENTS_CONNUS if chemin.exists()), None)


def _lancer(lms: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(lms), *arguments],
        capture_output=True,
        text=True,
        timeout=DELAI_COMMANDE_S,
        check=False,
    )


def contexte_charge(lms: Path, modele: str) -> int | None:
    """Le contexte avec lequel `modele` est charge.

    TROIS reponses distinctes, et les confondre coute cher :
    `None`  -- on n'a PAS PU savoir (serveur eteint, sortie illisible). « Je ne
               sais pas » n'est pas « ce n'est pas pret » : confondre les deux
               ferait decharger un modele sain des que `lms ps` echoue.
    `0`     -- le modele n'est pas charge. Rien a decharger avant de charger.
    `N > 0` -- charge avec N de contexte. Sous la cible, il faut le DECHARGER
               avant de recharger : `load` ne change pas le contexte d'un
               modele deja en memoire, et on croirait avoir repare pendant que
               les 400 continuent.
    """
    resultat = _lancer(lms, "ps", "--json")
    if resultat.returncode != 0:
        return None
    try:
        charges = json.loads(resultat.stdout or "[]")
    except json.JSONDecodeError:
        return None
    for entree in charges:
        if not isinstance(entree, dict):
            continue
        # `identifier` est ce que l'API attend, `modelKey` ce que `lms load`
        # accepte : les deux valent le nom configure selon les versions.
        noms = {entree.get("identifier"), entree.get("modelKey"), entree.get("path")}
        if modele in noms:
            return int(entree.get("contextLength") or 0)
    return 0


def autres_modeles(lms: Path, modele: str) -> list[str]:
    """Les modeles charges qui ne sont PAS le notre.

    Sur une machine contrainte, deux modeles en memoire est le probleme, pas le
    poids de l'un d'eux : mesure le 2026-09-30, un 4B et un 1.7B cohabitaient a
    4,12 Go sur 16 Go de RAM deja en swap.

    On les SIGNALE sans les decharger. LM Studio est aussi l'outil de
    l'utilisateur : un agent qui tourne toutes les demi-heures et decharge ce
    qu'il n'a pas charge se battrait contre lui. Le rapport rend le cout
    visible, la decision reste humaine.
    """
    resultat = _lancer(lms, "ps", "--json")
    if resultat.returncode != 0:
        return []
    try:
        charges = json.loads(resultat.stdout or "[]")
    except json.JSONDecodeError:
        return []
    autres = []
    for e in charges:
        if not isinstance(e, dict):
            continue
        cle = str(e.get("modelKey") or e.get("path") or "")
        if cle and cle != modele:
            autres.append(f"{e.get('identifier') or cle} ({int(e.get('sizeBytes') or 0) / 1e9:.2f} Go)")
    return autres


def instances_chargees(lms: Path, modele: str) -> int:
    """Combien d'instances de `modele` sont en memoire.

    `lms load` n'REMPLACE pas, il EMPILE : deux appels chargent deux copies
    (la seconde prend un identifiant suffixe `:2`) et donc deux fois la
    memoire. Constate le 2026-09-30 sur la machine de l'utilisateur, ou cet
    empilement a contribue a saturer 16 Go de RAM. Rend 0 si on n'a pas pu
    savoir -- on ne decharge jamais sur une incertitude.
    """
    resultat = _lancer(lms, "ps", "--json")
    if resultat.returncode != 0:
        return 0
    try:
        charges = json.loads(resultat.stdout or "[]")
    except json.JSONDecodeError:
        return 0
    return sum(
        1
        for e in charges
        if isinstance(e, dict)
        and str(e.get("modelKey") or e.get("path") or "") == modele
    )


def _signaler_autres(lms: Path, modele: str) -> None:
    autres = autres_modeles(lms, modele)
    if autres:
        print(
            "⚠ d'autres modeles occupent la memoire et ne servent pas au "
            f"pre-filtre : {', '.join(autres)} — `lms unload <modele>` les retire"
        )


def assurer(lms: Path, modele: str, contexte: int, dire, paralleles: int = 1) -> bool:
    """Demarre le serveur et charge le modele si besoin. Rend True si tout est
    pret a la sortie."""
    charge = contexte_charge(lms, modele)
    if charge is not None and charge >= contexte:
        # Pret, mais peut-etre EMPILE : deux instances tiennent deux fois la
        # memoire alors qu'une seule sert. On repart d'une seule copie plutot
        # que de laisser la machine payer le doublon.
        if instances_chargees(lms, modele) > 1:
            print(f"{modele} etait charge en plusieurs instances : on repart d'une seule")
            _lancer(lms, "unload", "--all")
        else:
            dire(f"pre-filtre deja pret ({modele}, contexte {charge})")
            _signaler_autres(lms, modele)
            return True

    if charge is None:
        # Serveur injoignable : le demarrer lance aussi l'application, qui
        # heberge le serveur (verifie : c'est le processus de l'app qui ecoute).
        demarrage = _lancer(lms, "server", "start")
        if demarrage.returncode != 0:
            print(f"[ERREUR] demarrage du serveur LM Studio : {demarrage.stderr.strip()[:300]}")
            return False
        print("serveur LM Studio demarre")
        charge = contexte_charge(lms, modele)
        if charge is not None and charge >= contexte:
            return True

    if charge:
        # Charge, mais trop court : il FAUT decharger d'abord (voir le
        # docstring de `contexte_charge`). Un `charge` a 0 ou None ne demande
        # aucun dechargement -- et surtout aucune requete de plus.
        _lancer(lms, "unload", modele)

    chargement = _lancer(
        lms, "load", modele,
        "--context-length", str(contexte),
        # UN SEUL emplacement : le pre-filtre note article par article. Le
        # defaut de LM Studio est 4, et le cache d'attention se paie PAR
        # emplacement -- c'est ce reglage, plus que le poids du modele, qui a
        # fait planter un poste de 16 Go le 2026-09-30.
        "--parallel", str(paralleles),
    )
    if chargement.returncode != 0:
        print(f"[ERREUR] chargement de {modele} : {chargement.stderr.strip()[:300]}")
        return False
    print(f"modele {modele} charge (contexte {contexte})")
    _signaler_autres(lms, modele)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbeux",
        action="store_true",
        help="dire aussi quand il n'y avait rien a faire",
    )
    options = parser.parse_args()
    dire = print if options.verbeux else (lambda *_: None)

    if not settings.local_scoring_enabled:
        dire("LOCAL_SCORING_ENABLED est faux : rien a assurer")
        return

    lms = trouver_lms()
    if lms is None:
        print(
            "[ERREUR] binaire `lms` introuvable — LM Studio installe ? "
            f"cherche dans : {', '.join(str(c) for c in EMPLACEMENTS_CONNUS)}"
        )
        raise SystemExit(1)

    try:
        pret = assurer(
            lms,
            settings.local_scoring_model,
            settings.local_scoring_context,
            dire,
            settings.local_scoring_parallel,
        )
    except subprocess.TimeoutExpired:
        print(f"[ERREUR] `lms` n'a pas repondu en {DELAI_COMMANDE_S} s")
        raise SystemExit(1) from None
    if not pret:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
