"""Pourquoi les creneaux de publication sont manques, avec des effectifs.

    uv run python -m scripts.diagnostiquer_creneaux [--jours N] [--detail]

Lecture seule. Aucune ecriture, aucun appel reseau, aucun appel paye.

POURQUOI. Le taux de creneaux manques est regarde regulierement, et il a deja
fallu refaire l'enquete a la main deux fois (2026-09-30, 2026-10-02). La
question qui compte n'est jamais « combien » mais « POURQUOI », et surtout :
est-ce une PANNE (du code, du reseau, d'un outil) ou une ABSENCE (le Mac etait
ferme) ? Les deux appellent des reponses opposees -- corriger du code, ou
accepter que la machine dorme -- et rien dans la base ne les distingue.

CE QU'IL CROISE. La base dit l'etat et la derniere erreur de chaque creneau ;
les logs disent si l'agent est PASSE pendant la fenetre ou le creneau pouvait
encore partir. Un creneau manque sans aucun passage de l'agent dans cette
fenetre est une absence de la machine, pas un defaut. Trois logs servent
d'horloge : `publication.log` (toutes les 10 min, mais SILENCIEUX quand il n'y
a rien a faire), plus `pipeline.log` (horaire) et `veille.log` (5 min) pour
corroborer que la machine etait eveillee.

LE PIEGE QU'IL DESAMORCE. `publication.log` est horodate en UTC alors que les
heures de creneaux y sont affichees en heure de Paris : confondre les deux
decale l'analyse de deux heures et fait conclure a un creneau publie « 2 h
avant son heure ». Tout est ramene en UTC ici, et l'affichage seul repasse en
heure locale.

CE QU'IL NE VOIT PAS. Un passage de l'agent qui n'avait rien a faire n'ecrit
rien : pour un creneau qui n'etait pas encore gele, « aucun passage » peut donc
etre un silence legitime. La colonne « corrobore » dit si pipeline/veille ont
vu la machine eveillee pendant la meme fenetre -- c'est la nuance a lire avant
de conclure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, attendre_base
from app.models import Creneau, EtatCreneau
from app.schedule.store import utc

DOSSIER_LOGS = Path.home() / "Library" / "Logs" / "music-news-radar"
# L'en-tete d'un passage : « [02/10 16:04] publication (reel) : ... ». L'annee
# n'y figure pas -- les logs ne couvrent que quelques semaines, on prend celle
# du jour et on recule d'un an si la date tombe dans le futur.
EN_TETE = re.compile(r"^\[(\d{2})/(\d{2}) (\d{2}):(\d{2})\]")
# « publication de 30/09 08:38 reportee : aucun tunnel joignable ... » : la
# seule trace qui relie NOMMEMENT un echec de tunnel a un creneau.
REPORT_TUNNEL = re.compile(r"publication de (\d{2}/\d{2} \d{2}:\d{2}) reportee : aucun tunnel")
BASE_INJOIGNABLE = "base injoignable"

ABSENCE = "ABSENCE : aucun passage de l'agent dans la fenetre"
TUNNEL = "TUNNEL cloudflared injoignable"
YTDLP = "YT-DLP : video Instagram indisponible"
INCONNUE_GELE = "GELE, l'agent est passe, cause inconnue"
SANS_POST = "aucun post compose a l'heure dite"
SANS_GEL = "post compose mais jamais gele"
AUTRE = "autre erreur enregistree"

ABOUTIS = (EtatCreneau.PUBLIE, EtatCreneau.APERCU, EtatCreneau.MANQUE, EtatCreneau.ECHEC)


class Journaux:
    """Les horodatages des passages, lus une fois.

    `passages` = les passages de l'agent de publication. `machine` = toute
    trace d'un autre agent (pipeline, veille), qui prouve seulement que le Mac
    etait eveille. `tunnels` compte, par heure LOCALE de creneau citee, les
    reports pour tunnel injoignable -- c'est la seule facon de rattacher un
    echec de tunnel a un creneau precis apres coup.
    """

    def __init__(self, dossier: Path = DOSSIER_LOGS, aujourdhui: dt.date | None = None) -> None:
        self.aujourdhui = aujourdhui or dt.date.today()
        self.passages: list[dt.datetime] = []
        self.machine: list[dt.datetime] = []
        self.tunnels: Counter = Counter()
        self.reveils_sans_base = 0
        self._lire_publication(dossier / "publication.log")
        for nom in ("pipeline.log", "veille.log"):
            self.machine += self._horodatages(dossier / nom)

    def _moment(self, mois: int, jour: int, heure: int, minute: int) -> dt.datetime:
        annee = self.aujourdhui.year
        moment = dt.datetime(annee, mois, jour, heure, minute, tzinfo=dt.timezone.utc)
        if moment.date() > self.aujourdhui:
            moment = moment.replace(year=annee - 1)
        return moment

    def _lignes(self, chemin: Path) -> list[str]:
        if not chemin.exists():
            return []
        return chemin.read_text(errors="replace").splitlines()

    def _horodatages(self, chemin: Path) -> list[dt.datetime]:
        moments = []
        for ligne in self._lignes(chemin):
            trouve = EN_TETE.match(ligne)
            if trouve:
                jour, mois, heure, minute = (int(g) for g in trouve.groups())
                moments.append(self._moment(mois, jour, heure, minute))
        return moments

    def _lire_publication(self, chemin: Path) -> None:
        courant: dt.datetime | None = None
        for ligne in self._lignes(chemin):
            trouve = EN_TETE.match(ligne)
            if trouve:
                jour, mois, heure, minute = (int(g) for g in trouve.groups())
                courant = self._moment(mois, jour, heure, minute)
                self.passages.append(courant)
                continue
            if BASE_INJOIGNABLE in ligne:
                # Un passage QUI A EU LIEU mais n'a pas atteint Neon : le Mac
                # venait de se reveiller et le DNS ne repondait pas encore. Ces
                # lignes n'ont pas d'en-tete (le script sort avant de
                # l'ecrire), donc elles ne comptent pas comme des passages --
                # mais elles bordent toujours un trou de veille.
                self.reveils_sans_base += 1
                continue
            report = REPORT_TUNNEL.search(ligne)
            if report:
                self.tunnels[report.group(1)] += 1

    def dans(self, moments: list[dt.datetime], debut: dt.datetime, fin: dt.datetime) -> int:
        return sum(1 for moment in moments if debut <= moment <= fin)


def fenetre(creneau: Creneau) -> tuple[dt.datetime, dt.datetime]:
    """Les bornes pendant lesquelles ce creneau pouvait encore partir : de la
    reclamation anticipee (`publication_avance_s`) a la limite de retard."""
    heure = utc(creneau.publier_a)
    return (
        heure - dt.timedelta(seconds=settings.publication_avance_s),
        heure + dt.timedelta(minutes=settings.publication_retard_max_minutes),
    )


def classer(creneau: Creneau, journaux: Journaux, fuseau: ZoneInfo) -> tuple[str, int, int]:
    """La cause d'un manque, le nombre de passages de l'agent et de traces machine.

    L'ordre des tests est la regle : une cause ETABLIE (le log nomme le
    creneau, ou la base porte une erreur explicite) prime toujours sur une
    deduction par absence de trace.
    """
    debut, fin = fenetre(creneau)
    passages = journaux.dans(journaux.passages, debut, fin)
    traces = journaux.dans(journaux.machine, debut, fin)
    cle = utc(creneau.publier_a).astimezone(fuseau).strftime("%d/%m %H:%M")
    erreur = creneau.erreur or ""
    if journaux.tunnels.get(cle) or "hebergement indisponible" in erreur:
        return TUNNEL, passages, traces
    if "yt-dlp" in erreur:
        return YTDLP, passages, traces
    if not passages:
        return ABSENCE, passages, traces
    if creneau.gele_le is not None:
        return INCONNUE_GELE, passages, traces
    if creneau.post_id is None:
        return SANS_POST, passages, traces
    if "creneau manque" in erreur or not erreur:
        return SANS_GEL, passages, traces
    return AUTRE, passages, traces


def examiner(db, jours: int | None) -> dict:
    fuseau = ZoneInfo(settings.publication_fuseau)
    journaux = Journaux()
    requete = select(Creneau).where(Creneau.etat.in_(ABOUTIS)).order_by(Creneau.publier_a)
    if jours:
        depuis = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=jours)
        requete = requete.where(Creneau.publier_a >= depuis)
    creneaux = list(db.scalars(requete))
    lignes = []
    for creneau in creneaux:
        debut, fin = fenetre(creneau)
        cause, passages, traces = (
            classer(creneau, journaux, fuseau)
            if creneau.etat == EtatCreneau.MANQUE
            else (None, journaux.dans(journaux.passages, debut, fin), journaux.dans(journaux.machine, debut, fin))
        )
        lignes.append(
            {
                "id": creneau.id,
                "heure": utc(creneau.publier_a).astimezone(fuseau),
                "format": creneau.format.value,
                "etat": creneau.etat.value,
                "gele": creneau.gele_le is not None,
                "post_id": creneau.post_id,
                "cause": cause,
                "passages": passages,
                "traces": traces,
                "erreur": creneau.erreur,
            }
        )
    posts = Counter(ligne["post_id"] for ligne in lignes if ligne["post_id"])
    return {
        "lignes": lignes,
        "journaux": journaux,
        "repetes": {post: n for post, n in posts.items() if n > 1},
    }


def _par_heure(lignes: list[dict]) -> list[tuple[int, int, int, int]]:
    """Par heure locale de creneau : total, publies, vus par l'agent."""
    seaux: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])
    for ligne in lignes:
        seau = seaux[ligne["heure"].hour]
        seau[0] += 1
        if ligne["etat"] == EtatCreneau.PUBLIE.value:
            seau[1] += 1
        if ligne["passages"]:
            seau[2] += 1
    return [(heure, *seaux[heure]) for heure in sorted(seaux)]


def afficher(resultat: dict, detail: bool) -> None:
    lignes = resultat["lignes"]
    journaux = resultat["journaux"]
    if not lignes:
        print("Aucun creneau abouti sur la periode.")
        return

    etats = Counter(ligne["etat"] for ligne in lignes)
    publies = etats.get(EtatCreneau.PUBLIE.value, 0)
    manques = [ligne for ligne in lignes if ligne["etat"] == EtatCreneau.MANQUE.value]
    print("=" * 78)
    print(f"{len(lignes)} creneaux aboutis : {dict(etats)}")
    print(f"  taux de publication : {publies}/{len(lignes)} ({100 * publies / len(lignes):.0f} %)")
    print(f"  logs lus : {len(journaux.passages)} passages de publication, "
          f"{len(journaux.machine)} traces pipeline/veille, "
          f"{journaux.reveils_sans_base} reveil(s) sans base joignable")

    print()
    print("=== CAUSES DES MANQUES, PAR EFFECTIF ===")
    causes = Counter(ligne["cause"] for ligne in manques)
    for cause, n in causes.most_common():
        formats = Counter(ligne["format"] for ligne in manques if ligne["cause"] == cause)
        part = f"{100 * n / len(manques):.0f} %" if manques else "-"
        print(f"  {n:3d}  ({part:>4s})  {cause}  [{dict(formats)}]")
    if not manques:
        print("  aucun creneau manque.")

    print()
    print("=== PANNE OU ABSENCE ===")
    absents = [ligne for ligne in manques if ligne["cause"] == ABSENCE]
    eveille = [ligne for ligne in absents if ligne["traces"]]
    print(f"  absence de la machine : {len(absents)}/{len(manques) or 1} manques")
    print(f"    dont {len(eveille)} ou pipeline/veille ont VU la machine eveillee dans la fenetre")
    print("      (a regarder : passage de publication saute, ou passage precedent trop long)")
    print(f"  pannes identifiees    : {len(manques) - len(absents)}")

    print()
    print("=== PAR FORMAT ===")
    for format_ in sorted({ligne["format"] for ligne in lignes}):
        tous = [ligne for ligne in lignes if ligne["format"] == format_]
        ok = sum(1 for ligne in tous if ligne["etat"] == EtatCreneau.PUBLIE.value)
        print(f"  {format_:9s} : {ok}/{len(tous)} publies ({100 * ok / len(tous):.0f} %)")

    print()
    print("=== PAR FENETRE (heure locale du creneau) ===")
    print("  Si une heure concentre les manques ET les passages manquants, c'est un")
    print("  probleme de REGLAGE des fenetres, pas de code.")
    for heure, total, ok, vus in _par_heure(lignes):
        print(f"  {heure:02d}h : {total:3d} creneaux | {ok:3d} publies ({100 * ok // total:3d} %) "
              f"| {vus:3d} vus par l'agent ({100 * vus // total:3d} %)")

    if resultat["repetes"]:
        print()
        print("=== UN MEME POST SUR PLUSIEURS CRENEAUX ===")
        print("  Un post qui echoue est recompose sur le creneau suivant : il peut")
        print("  consommer plusieurs creneaux d'affilee sans jamais partir.")
        for post, n in sorted(resultat["repetes"].items(), key=lambda p: -p[1]):
            ids = [ligne["id"] for ligne in lignes if ligne["post_id"] == post]
            etats = sorted({ligne["etat"] for ligne in lignes if ligne["post_id"] == post})
            print(f"  post {post} sur {n} creneaux {ids} -> {etats}")

    if detail:
        print()
        print("=== DETAIL DES MANQUES ===")
        for ligne in manques:
            print(f"  {ligne['id']:4d} {ligne['heure']:%d/%m %H:%M} {ligne['format']:9s} "
                  f"gele={'O' if ligne['gele'] else '-'} passages={ligne['passages']:2d} "
                  f"machine={ligne['traces']:2d} | {ligne['cause']}")
            if ligne["erreur"]:
                print(f"       base : {ligne['erreur'][:200]}")

    print()
    print("Ce script ne JUGE pas : une absence de la machine n'est pas un defaut de")
    print("code, et un tunnel injoignable est une panne reseau, pas un article fautif.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jours", type=int, default=None, help="ne regarder que les N derniers jours")
    parser.add_argument("--detail", action="store_true", help="une ligne par creneau manque")
    options = parser.parse_args()

    attendre_base()
    with SessionLocal() as db:
        resultat = examiner(db, options.jours)
        # Une session qui a LU garde sa transaction ouverte : la clore
        # explicitement plutot que de laisser « idle in transaction » chez Neon.
        db.rollback()
    afficher(resultat, options.detail)


if __name__ == "__main__":
    main()
