"""Un article ne peut pas etre son PROPRE doublon -- et c'est arrive.

Mesure en base de PRODUCTION le 2026-10-02 : sur 662 articles, 59 portaient un
lien de doublon et **3 pointaient sur leur propre id**. Le journal
(`pipeline.err.log`) donne la mecanique exacte, deux lignes consecutives du
MEME passage :

    doublon frais tranche par Claude : « Sia sells 50% ... » (21627)
        rattache a « Sia has sold a 50% stake ... » (21942)
    doublon frais tranche par Claude : « Sia has sold a 50% stake ... » (21942)
        rattache a « Sia sells 50% ... » (21627)

Les deux sens du MEME lien ont ete poses dans le meme passage. Le second
`lier()` a alors fait ce que son aplatissement anti-chaine lui demande --
« repointer vers la cible finale tout article qui pointait deja vers l'article
qu'on vient de lier » -- sauf que cette cible finale ETAIT l'article repointe :
21627 s'est retrouve son propre doublon.

Le degat est SILENCIEUX et couteux : un article reflexif se presente comme un
doublon, donc `score_pending` (qui filtre `duplicate_of_id IS NULL`) ne le note
jamais, `store._articles_publiables` ne le propose jamais, et rien ne le
signale. L'article 21599 (Billboard) etait dans cet etat depuis 28 h, sans
note, et n'en aurait jamais eu.

Comment les DEUX sens peuvent etre poses dans un seul passage : un article
DIFFERE en passe 1 (sa paire est deja en echec, sous le plafond de tentatives)
est ajoute a `representatives` pour rester un representant POSSIBLE -- et il
est RE-TRAITE en passe 2. Deux articles differes qui se repondent se lient donc
l'un a l'autre.

`db_session.autoflush = False` : le reglage de PRODUCTION (`app/db.py`).
"""

import datetime as dt

from app.models import Article, Source, SourceType
from app.pipeline.cluster import cibles_a_repointer, link_duplicates
from app.pipeline.cluster_arbitrage import Cache, cle_paire, ecrire_cache

UTC = dt.timezone.utc

# Trois titres calibres sur les jetons significatifs, pour tomber dans la bande
# fraiche (0.30 - 0.65) exactement ou il faut :
#   AMORCE / MILIEU  -> 2 mots partages sur 6 = 0.33  (bande douteuse)
#   MILIEU / FIN     -> 2 mots partages sur 6 = 0.33  (bande douteuse)
#   AMORCE / FIN     -> 0 mot partage              = 0.00 (rien, aucun appel)
AMORCE = "Nina Kraviz programme sa residence estivale ibizenque"
MILIEU = "Nina Kraviz signe un remix pour Boris Brejcha"
FIN = "Boris Brejcha devoile trois dates supplementaires"


def _source(db, nom):
    source = Source(name=nom, type=SourceType.RSS, url=f"https://{nom}/feed")
    db.add(source)
    db.commit()
    return source


def _article(db, source, titre, heures):
    """Date sur l'heure REELLE : `link_duplicates` lit `dt.datetime.now`, une
    fenetre calee sur une constante ne serait jamais examinee."""
    article = Article(
        source_id=source.id,
        url=f"https://{source.name}/{abs(hash(titre)) % 10**6}",
        title=titre,
        raw_hash=f"{source.id}-{titre[:12]}",
        published_at=dt.datetime.now(UTC) - dt.timedelta(hours=heures),
    )
    db.add(article)
    db.commit()
    return article


class _ArbitreParPaire:
    """Rend un verdict different selon la paire -- c'est ce qui permet de
    DIFFERER un article sur une paire et de le lier sur une autre."""

    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.appels = []

    def __call__(self, gauche, droite):
        cle = cle_paire(gauche.id, droite.id)
        self.appels.append(cle)
        return self.verdicts.get(cle)


def _scenario(db, tmp_path):
    """Les trois articles de la reproduction, et le cache qui fait DIFFERER les
    deux derniers en passe 1 (une tentative deja en echec par paire)."""
    db.autoflush = False
    amorce = _article(db, _source(db, "media-a"), AMORCE, 9)
    milieu = _article(db, _source(db, "media-b"), MILIEU, 6)
    fin = _article(db, _source(db, "media-c"), FIN, 3)

    chemin = tmp_path / "arbitrage.json"
    ecrire_cache(
        Cache(
            entrees={},
            tentatives={
                cle_paire(amorce.id, milieu.id): 1,
                cle_paire(milieu.id, fin.id): 1,
            },
        ),
        chemin,
    )
    arbitre = _ArbitreParPaire(
        {
            # Ce verdict-la ne lie rien : il sert seulement a faire DIFFERER
            # `milieu` en passe 1, donc a le laisser dans `representatives`.
            cle_paire(amorce.id, milieu.id): (False, "deux annonces distinctes"),
            cle_paire(milieu.id, fin.id): (True, "meme serie de dates"),
        }
    )
    return amorce, milieu, fin, arbitre, chemin


def test_deux_articles_qui_se_repondent_ne_se_lient_jamais_l_un_a_l_autre(
    db_session, tmp_path
):
    """LA reproduction du defaut de production, et la preuve par mutation.

    Retirer la garde de `resoudre` (« un candidat qui vient d'etre lie n'est
    plus un representant ») fait reapparaitre exactement l'etat mesure :
    `milieu.duplicate_of_id == milieu.id`.
    """
    amorce, milieu, fin, arbitre, chemin = _scenario(db_session, tmp_path)

    stats = link_duplicates(
        db_session,
        arbitre=arbitre,
        cache_chemin=chemin,
        arbitrage_frais_min=0.30,
    )

    for article in (amorce, milieu, fin):
        db_session.refresh(article)
        assert article.duplicate_of_id != article.id, (
            f"article {article.id} est son propre doublon"
        )
    # Un seul lien pour la paire, jamais deux sens : le second sens est
    # precisement ce qui fabriquait le lien reflexif.
    assert stats.frais == 1
    assert stats.frais_arbitres == 1
    assert milieu.duplicate_of_id == fin.id
    assert fin.duplicate_of_id is None
    assert amorce.duplicate_of_id is None


def test_aucun_cycle_entre_deux_articles_du_meme_groupe(db_session, tmp_path):
    """La forme generale du meme defaut : deux articles qui se pointent l'un
    l'autre. Sans lien reflexif, un cycle a deux resterait une corruption --
    chacun se presente comme un doublon, donc AUCUN des deux n'est jamais
    note ni publiable, et l'actu disparait tout entiere."""
    amorce, milieu, fin, arbitre, chemin = _scenario(db_session, tmp_path)

    link_duplicates(
        db_session,
        arbitre=arbitre,
        cache_chemin=chemin,
        arbitrage_frais_min=0.30,
    )

    for article in (amorce, milieu, fin):
        db_session.refresh(article)
    lies = {a.id: a.duplicate_of_id for a in (amorce, milieu, fin)}
    for identifiant, representant in lies.items():
        if representant is None:
            continue
        assert lies.get(representant) is None, (
            f"cycle : {identifiant} -> {representant} -> {lies.get(representant)}"
        )


# -- la ceinture au POINT D'ECRITURE, isolee pour etre mutable ---------------


def test_le_representant_n_est_jamais_repointe_vers_lui_meme():
    """`cibles_a_repointer` est la ceinture de `lier` : meme si un etat
    mutuel arrivait par un chemin non prevu, l'aplatissement anti-chaine ne
    doit JAMAIS ecrire `x.duplicate_of_id = x.id`. Retirer le filtre fait
    echouer ce test.

    Les objets sont des sosies minimaux : la fonction ne lit que `id`, et un
    test qui monterait une session pour ca ne prouverait rien de plus."""

    class _Sosie:
        def __init__(self, identifiant):
            self.id = identifiant

    representant = _Sosie(1)
    autre = _Sosie(2)
    assert cibles_a_repointer([representant, autre], representant) == [autre]
    assert cibles_a_repointer([], representant) == []
