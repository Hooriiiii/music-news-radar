# Sources, veille rapide, illustration des articles

**Quand lire ce fichier** : avant d'ajouter ou de reclasser une source, de
changer une cadence d'ingestion, de toucher a `app/sources/`, a
`app/pipeline/veille.py` ou a `app/pipeline/illustrations.py`. Il porte la
philosophie de sourcing (on tape au niveau de la SOURCE, jamais un reposteur),
l'arithmetique du quota Graph qui dicte les deux cadences Instagram, et pourquoi
il n'y a plus aucune source Google News.

Les contraintes de transport apprises sur le terrain (429 Reddit, 403 Mixmag,
jetons Meta, URL echappees) sont dans `contraintes-terrain.md`.

## Philosophie de sourcing (source-level, pas aggregator)

Les gros comptes d'actu Insta/X (Pop Crave, Proximity, AllTime EDM, chart-trackers)
sont des REPOSTEURS en aval : ils repackagent les comptes d'artistes + la presse. Payer
pour les lire (API X), c'est payer un repost de ce que les sources primaires donnent
déjà, avec latence et doublons. La stratégie : taper AU NIVEAU DE LA SOURCE —
- primaire : comptes d'artistes/labels, organisateurs de festivals ;
- presse qui *break* l'actu : Mixmag, RA, Pitchfork, Tsugi, DJ Mag (électro),
  Music Business Worldwide + CMU + Billboard (business/pop) — flux RSS gratuits ;
- primaire aussi, depuis 2026-09-17 : comptes Instagram PROFESSIONNELS d'artistes,
  labels, festivals et médias via `business_discovery` (source `instagram`,
  gratuite) — la philosophie ne change pas : on lit le compte qui produit l'actu,
  jamais un reposteur ;
- social brut : X UGC/radar (éteint, voir plus bas), Reddit.
Corollaire coût : Billboard RSS (gratuit) rend les comptes X @billboard/@billboardcharts/
@chartdata redondants -> candidats à couper.
## Veille rapide (`app/pipeline/veille.py`, `scripts/run_veille.py`)

**Pourquoi.** Mesure du 2026-09-18 (`docs/superpowers/specs/2026-09-18-etre-une-
source-pas-un-intermediaire.md`) : Kinda ingérait la presse RSS en 48 min et les
comptes Instagram d'artistes en 141 min (médiane), parce que ces comptes n'étaient
lus que toutes les 3 h. Sur le cas Charlotte de Witte, il a donc **appris l'actu
par DJ Mag 39 heures avant de la voir chez l'artiste** — la définition mécanique
d'un intermédiaire. La propagation côté Meta est quasi immédiate : la latence
était **entièrement la nôtre**.

**L'arithmétique du quota, qui dicte tout.** Le quota Graph de l'app a rendu le
code 4 au-delà de ~430 appels dans l'heure (2026-09-17). Lire les 123 comptes
toutes les 5 min demanderait ~1 476 appels/h : impossible. D'où **deux cadences** :
- **12 comptes surveillés toutes les 5 min** = 144 appels/h, latence ~5 min ;
- **tous les autres toutes les heures** (`instagram_intervalle_heures` passé de 3
  à **1**) = ~111 appels/h, latence ~50 min au lieu de 141 min.

Soit **~255 appels/h en crête**, sous les ~430/h observés. (La note du 18/09
annonçait 181/h en gardant les autres comptes à 3 h ; on a préféré payer 74
appels/h de plus pour faire baisser aussi la latence des 111 autres. Revenir à
`INSTAGRAM_INTERVALLE_HEURES=3` redonne 181/h sans rien casser.)

**Les 12 comptes**, `VEILLE_COMPTES` dans `.env`, **modifiable** — des labels très
actifs et les festivals FR plutôt qu'un seul artiste : `stmpdrcrds`,
`defectedrecords`, `vladimircauchemar`, `fredagainagainagainagainagain`,
`etjusticepourtous`, `edbanger`, `astropolis`, `nuits_sonores`, `transmusicales`,
`positiveeducation`, `madeon`, `worakls.music`. **Chaque compte ajouté coûte
12 appels/h** sur un quota partagé avec la publication.

**Pas de migration** : la cadence est une liste de réglage, pas une colonne.
`InstagramAdapter.intervalle()` compare le username (`compte_depuis_url`) à
`comptes_surveilles()` ; `a_lire()` raisonne désormais en `timedelta` (marge d'un
quart d'heure au-delà de l'heure, un dixième de l'intervalle en dessous — les
cadences déjà mesurées sont inchangées).

**L'espacement est PARTAGÉ**, et c'est ce qui empêche le doublon : `dernier_appel`
est le même état pour les deux agents, donc un compte lu par la veille à T n'est
pas relu par le passage horaire à T+1 min. La veille **redistribue** les appels,
elle ne les double pas.

**Ce que la veille ne fait PAS** : ni illustration (bornée à 40 requêtes réseau
par run) ni purge — elles restent au passage horaire, conçues pour lui. Elle
ingère, `link_duplicates`, score **au plus `veille_scoring_max` (20)** articles
(jamais `score_pending` sans limite : l'arriéré peut se compter en centaines), et
enregistre les mentions. Rien de neuf = pas une requête de plus.

**Deux garde-fous, parce que le quota est partagé.** Un plafond dur
`veille_appels_max_par_heure` (200) compté dans `veille.json` à côté du jeton
(écriture atomique) ; et sur un code de quota Meta (4, 17, 32, 613) la veille
s'arrête **au premier**, se met en pause 30 min et notifie une seule fois.

**Cette pause est PARTAGÉE avec le passage horaire depuis le 2026-09-19**
(`app/pipeline/quota_graph.py`, fichier `quota_graph.json` à côté du jeton).
Ce document affirmait l'inverse — « le pipeline horaire n'est jamais suspendu :
sa cadence n'est pas en cause » — et la production l'a démenti : un appel refusé
pour quota **consomme quand même du quota**, donc le passage horaire qui
insistait sur 123 comptes entretenait tout seul le dépassement (voir la puce
« Le quota Graph de l'app est partagé » dans les contraintes). Le quota est
celui de l'APP, pas celui d'un agent : ce qu'un agent vient d'apprendre vaut
pour l'autre. Deux processus écrivent donc ce fichier — d'où un temporaire
nommé par processus, aucune écriture en LECTURE (une pause échue se reconnaît à
sa date, rien à effacer, donc rien qui puisse effacer la pause que l'autre vient
de poser), et la fin la plus tardive qui gagne quand deux pauses se croisent.

**La PUBLICATION, elle, n'est jamais suspendue par cette pause** : un post gelé
qui attend son heure part même si l'ingestion est à l'arrêt — rater un créneau
programmé coûte plus cher que de sauter une heure d'ingestion. La lecture de
visuels à la composition (`lecture_artistes.py`) garde sa propre pause. Un test
sentinelle (`tests/pipeline/test_quota_graph.py`) échoue si un module de `app/publish` ou
`app/schedule` se met à lire cette pause.
## Illustration des articles (`app/pipeline/illustrations.py`)

57 % des articles arrivaient sans aucun média, et le déficit était concentré sur
les flux RSS — 19 % d'illustration, alors que ce sont eux qui portent la matière
éditoriale. La réponse n'est PAS une recherche web : chaque éditeur publie une
balise **`og:image`**, celle que Facebook, X et Instagram affichent quand on
partage le lien. Elle est à jour, gratuite, et illustre l'article EXACT — une
recherche ramènerait au mieux la même image, au pire celle d'un autre événement.

Mesuré en production : 35 illustrés, 1 échec sur 36 tentatives (97 %).

Deux règles de coût. On n'illustre qu'**après le scoring** et au-dessus du seuil
du digest : un article de bruit ne sera jamais posté, lui chercher une image est
une requête réseau pour rien. Et `illustration_max_par_run` (40) borne un run,
sinon le premier passage partirait sur des centaines de requêtes.

**Plus aucune source Google News** (2026-08-18). Ces liens ne mènent pas à
l'article mais à la page de consentement de Google — irrésoluble, y compris avec
un navigateur complet et le cookie de consentement, tous deux essayés. Deux
conséquences : pas d'`og:image` donc pas d'illustration, et un post citerait
Google au lieu de l'éditeur.

Les quatre sources concernées ont migré, par `scripts/sources/migrer_sources_google.py` :
Mixmag, qui est un ÉDITEUR, retrouve son flux direct (`mixmag.net/rss.xml`) —
le tout-local le rend de nouveau accessible, Cloudflare ne renvoyant 403 que
depuis les runners Azure ; les trois autres sont des RECHERCHES thématiques et
passent sur **Bing News RSS**, qui porte l'URL réelle dans son paramètre `url=`.
`_url_reelle()` dans l'adapter RSS la décode. Résultat : 105 items, zéro lien de
redirecteur, et de la vraie presse française (Ouest-France, Le Dauphiné,
20 Minutes) là où Google ne rendait que des liens opaques.

Deux pièges rencontrés. Bing ne comprend PAS l'opérateur `OR` : « A OR B » y rend
zéro résultat, ce qui éteindrait la source en silence — les requêtes concernées
sont réécrites. Et une source Mixmag directe existait déjà, désactivée à l'époque
du 403 : migrer sans regarder aurait créé un doublon interrogeant le même flux
deux fois par run.

L'adapter RSS lit aussi les `<img>` du HTML de la description, en DERNIER recours
— un `<img>` peut être un logo, une pub ou un pixel de suivi (ceux d'au plus
32 px sont écartés), là où `media_content` est choisi par l'éditeur. Gain mesuré
sur 12 flux réels : +12 % d'articles illustrés sans aucune requête réseau.
