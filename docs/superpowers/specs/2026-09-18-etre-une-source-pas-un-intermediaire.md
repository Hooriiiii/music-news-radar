# Être une source, pas un intermédiaire — design

Date : 2026-09-18
État : note de stratégie, mesurée sur la base de production (lectures seules). Aucune décision utilisateur prise. Voir « À décider par l'utilisateur ».
Question de départ (utilisateur, verbatim) : « Regarde toutes les actus récupérées par Guettapen, DJ Mag. Pose-toi la question de comment ils font pour avoir les actus avant tout le monde. J'ai envie d'être une source d'information et pas un intermédiaire. »
S'appuie sur : « Philosophie de sourcing (source-level, pas aggregator) », « Qui parle : `sources.categorie` », « Mémoire A&R », « Rétention » (`CLAUDE.md`), `docs/superpowers/specs/2026-09-17-info-medias-visuels-artistes-design.md`.

---

## Le résultat en une phrase

Guettapen et DJ Mag ne sont **pas** plus rapides que Kinda parce qu'ils crawlent mieux :
sur le seul cas où la mesure est propre, **Kinda avait l'info 5 h 42 avant DJ Mag** — et ne
l'a *vue* que 39 h après lui, parce que son propre pipeline lit les comptes d'artistes toutes
les 3 h et la presse RSS toutes les heures. Leur avance ne vient pas de la collecte : elle
vient de **trois canaux entrants que Kinda n'a pas** (communiqués de labels et d'agences,
soumissions d'artistes via Groover, présence physique en événement) et d'un quatrième que
Kinda ne peut pas copier : **leur propre marque** (Top 100 DJs, concours, résidences). Deux
de ces quatre canaux sont ouverts à Kinda dès aujourd'hui, gratuitement.

---

## Avertissement de méthode : la fenêtre est de 3 jours, pas de 10

`RETENTION_DAYS=3` dans `.env` (« Retention alignee sur la fraicheur de la file de
publication »). La base ne contient donc **pas** dix jours d'historique : elle va du
**2026-09-15 au 2026-09-18**, soit 813 articles datés (240 / 286 / 280 / 7) plus 83 sans
`published_at` (flux RSS qui n'en fournissent pas — Mixmag notamment). Toutes les mesures qui
suivent portent sur cette fenêtre. Les tables `artists` / `artist_mentions`, elles, ne sont
jamais purgées et couvrent depuis le 2026-07-13 (3 347 artistes, 9 528 mentions).

C'est déjà un résultat, et le premier de la note : **la fenêtre de rétention est calée sur le
rythme de la file de publication, pas sur celui des sources primaires françaises.** Astropolis
a posté pour la dernière fois le 10/09, Nuits Sonores le 10/09, Ed Banger le 11/09, Justice le
09/06. Ces comptes sont interrogés avec succès à chaque run (`last_ok_at` du 18/09), mais leur
dernier post est hors fenêtre : **54 des 123 sources Instagram actives n'ont aucun article en
base**, dont exactement les primaires FR qu'on a ajoutées pour cela (Astropolis, Nuits Sonores,
Marsatac, Peacock Society, Dour, Ed Banger, Justice, Polo & Pan, Kompromat, Folamour, Agoria,
Cercle). Un festival ne poste pas tous les jours ; une base à 3 jours ne peut pas porter une
mémoire de festivals.

---

# Partie 1 — Ce que la base dit

## 1.1 Volume et traçabilité, par compte

Pour chaque compte média suivi : le nombre d'items sur la fenêtre, et la part de ces items
qu'on peut relier à une **source primaire que Kinda suit déjà** (l'item cite un artiste,
festival, label ou club dont Kinda a le compte en source). Le complément, c'est là où ils ont
un avantage qu'on ne rattrapera pas par la collecte.

| Compte | Items | Traçables à une primaire suivie | % | Non traçables |
|---|---:|---:|---:|---:|
| Guettapen (IG) | 14 | 6 | 43 % | **8** |
| Electro News (IG) | 15 | 2 | 13 % | **13** |
| DJ Mag (RSS) | 26 | 8 | 31 % | **18** |
| DJ Mag (IG) | 14 | 3 | 21 % | **11** |
| Mixmag (RSS) | 83 | 7 | 8 % | **76** |
| Mixmag (IG) | 7 | 1 | 14 % | 6 |
| Tsugi (IG) | 5 | 0 | 0 % | 5 |
| Tsugi (RSS) | **0** | — | — | — |
| Booska-P (IG) | 24 | 2 | 8 % | 22 |
| Billboard (RSS) | 175 | 35 | 20 % | 140 |
| Billboard (IG) | 34 | 9 | 26 % | 25 |

Lecture. Guettapen est le compte **le plus traçable** (43 %) : c'est le plus proche de ce que
Kinda sait déjà faire, et le plus rattrapable. Mixmag est le moins traçable (8 %) parce que
son flux est massivement **éditorial de marque** (interviews, franchises hebdomadaires,
partenariats) — un contenu qu'on ne « rattrape » pas, qu'on produit ou pas. Et Tsugi en RSS
rend **zéro** article : le flux `tsugi.fr/feed/` est interrogé avec succès mais ne dépose rien
en base sur la fenêtre. À vérifier séparément (c'est une source FR de référence, pas un
détail).

## 1.2 Origine de l'information, item par item

Classification à la main des 55 items des quatre comptes électro (Guettapen, Electro News,
DJ Mag RSS, DJ Mag IG), en lisant le résumé de chaque article.

| Origine de l'info | Guettapen (14) | Electro News (15) | DJ Mag RSS (26) | DJ Mag IG (14) |
|---|---:|---:|---:|---:|
| Annonce artiste / label (communiqué, promo) | 2 | 0 | **9** | 3 |
| Annonce festival / salle / promoteur / billetterie | **7** | **6** | 1 | 1 |
| Communiqué industrie / wire (Suno, NRJ Awards, gear, stats) | 3 | 1 | 6 | 0 |
| Données charts / DSP | 0 | 0 | 0 | 0 |
| Interview / éditorial / archive | 2 | 1 | 4 | 3 |
| **Couverture propre : ils y étaient** (photos, captation) | 0 | **5** | 1 | 3 |
| **Leur propre marque** (Top 100 DJs, concours, résidence) | **1** | 0 | **5** | **4** |
| Rumeur / fuite | 0 | 0 | 0 | 0 |
| Repost communauté / clip d'archive | 0 | 2 | 0 | 0 |

Trois choses sautent aux yeux.

**Zéro rumeur, zéro fuite, zéro donnée de plateforme.** L'imaginaire du « média qui a l'info
avant tout le monde » ne correspond à rien ici. Personne ne fuite. Tout est du **communiqué
reçu** ou de la **présence sur place**.

**Guettapen et Electro News sont des médias d'ÉVÉNEMENT, pas de sortie.** 7/14 et 6/15 de
leurs items sont des annonces de festivals, de salles et de soirées — souvent à l'échelle
d'une soirée parisienne ou lyonnaise (miroir.paris à La Nuit Madeleine, une release party à
La Java, T7, Cabaret Sauvage). Kinda n'a aucun de ces émetteurs en source. Ce n'est pas un
problème de vitesse : c'est un problème de **liste de contacts**.

**Un tiers du feed d'Electro News est sa propre captation.** Cinq items sur quinze sont des
photos et vidéos qu'ils ont tournées au Brunch Electronik de Lyon et au Jardin Électronique
(Nikolina, TNT, Vladimir Cauchemar, le J1). Ce contenu n'est scrapable par personne : il
existe parce qu'un humain avait une accréditation et un téléphone. C'est exactement ce que
l'utilisateur veut dire par « être une source ».

Et DJ Mag : 9 annonces artiste/label sur 26 (35 %) — ce sont des **communiqués de labels**
(KNTXT pour Charlotte de Witte, Dekmantel pour Upsammy × Piezo, Hyperdub pour Fatima Al
Qadiri, Disorder/Capitol pour MPH, Mr Bongo pour Jamz Supernova) — plus 5 items qui sont
**leur propre business** (Top 100 DJs, la campagne Bridges for Music, Top 100 DJs LIVE à
[UNVRS]). Un cinquième de leur flux est de l'auto-actualité : ils sont l'événement.

## 1.3 Qui a publié en premier, et de combien

La dédup par titre (`pipeline/cluster.py`) produit 55 groupes multi-articles sur la fenêtre
(132 membres), dont **5 seulement** relient un média à une source primaire — et 3 de ces 5
sont de faux rapprochements (le groupe LE SSERAFIM fusionne huit posts sur la formule « made
my night »). Le clustering par titre ne sert donc **pas** à mesurer l'antériorité entre un
artiste et la presse : l'artiste écrit « The Resistance is coming. », la presse écrit
« Charlotte de Witte unveils new EP ». Aucun mot significatif partagé.

Reconstruites à la main par recherche plein texte, les chronologies exploitables :

| Actu | Émetteur primaire | Presse | Avance du primaire |
|---|---|---|---|
| Charlotte de Witte, EP « The Resistance » (KNTXT) | **son IG, 15/09 10:40** | DJ Mag RSS 15/09 16:22 · DJ Mag IG 16/09 10:00 | **+5 h 42** / +23 h 20 |
| Noisia, retour | *(pas de source primaire suivie)* | Billboard 17/09 09:00 · **Guettapen 09:06** · r/electronicmusic 09:16 · DJ Mag RSS 15:37 · DJ Mag IG 18:05 | Guettapen bat DJ Mag de **6 h 31** |
| Bande-son GTA VI | *(éditeur du jeu, non suivi)* | DJ Mag 17/09 12:57 · **Guettapen 13:03** · Pitchfork 13:04 · Booska-P 14:19 · Stereogum 14:44 | 6 min d'écart : tout le monde a le même communiqué |
| Sonora Bordeaux | *(festival non suivi)* | Guettapen 17/09 10:47 · Electro News 13:06 | +2 h 19 |
| Tomorrowland Brasil 2027 | **son IG, 15/09 14:41** | aucune reprise dans la fenêtre | — |

Conclusion nette : **sur GTA VI, six médias publient en 1 h 47 — ils ont tous reçu la même
chose au même moment.** Il n'y a pas de scoop, il y a une liste de diffusion. Et sur Charlotte
de Witte, l'artiste bat la presse de près de six heures : l'info primaire *était disponible*,
gratuitement, avant tout le monde.

## 1.4 Le vrai goulot : la latence de Kinda, pas celle des autres

Latence d'ingestion mesurée (`fetched_at − published_at`), **hors premier fetch de chaque
source** (le backfill initial fausse tout) :

| Type de source | n | Médiane | p90 |
|---|---:|---:|---:|
| RSS (presse) | 396 | **48 min** | 201 min |
| Instagram — compte média | 62 | 107 min | 187 min |
| Instagram — compte **artiste** | 19 | **141 min** | 190 min |
| Instagram — festival | 6 | 167 min | 206 min |
| Reddit | 56 | 91 min | 227 min |

**Kinda voit la presse trois fois plus vite que les artistes qu'elle suit.** 141 min contre
48 min. Le pipeline est construit pour rendre Kinda intermédiaire : la seule source qu'il lit
vite est celle qu'il ne peut pas reposter.

Le compte est cohérent avec la configuration : `instagram_intervalle_heures = 3` (attente
moyenne 90 min) + agent `run_pipeline` horaire (attente moyenne 30 min) ≈ 120 min, plus une
vingtaine de minutes de résidu. La propagation côté Meta est donc quasi immédiate — **la
latence est entièrement la nôtre.**

Le cas Charlotte de Witte le montre en grand : elle poste le 15/09 à 10:40, son compte est
lu pour la première fois le **17/09 à 08:26**, soit 45 h 46 plus tard. Entre les deux, DJ Mag
a publié (15/09 16:22) et Kinda a ingéré DJ Mag en ~48 min. **Kinda a donc appris l'actu par
le média, 39 heures avant de la voir chez l'artiste.** C'est la définition mécanique d'un
intermédiaire.

## 1.5 La liste de manques : entités citées, non suivies

Entités récurrentes dans leurs items dont Kinda n'a **aucune** source. C'est la liste de
courses concrète.

**Salles, clubs, promoteurs FR** (tous cités par Guettapen ou Electro News, aucun en source) :
T7 (Paris), Cabaret Sauvage, La Java, La Nuit Madeleine, La Felicità, miroir.paris,
offshore.event, 1.2.3.sauvage, Brunch Électronik (Lyon), Le Jardin Électronique (Nord),
Ninkasi/Heat — *(seuls Rex Club et Ninkasi sont déjà suivis)*.

**Festivals FR non suivis** : One Life Infinity (Rennes — c'est du breton, l'angle du compte),
Stellar Festival (Poitiers), Les Castelrouquines (Châteauroux), Sonora (Bordeaux), Dionysa
(Bordeaux), Hard Boat Festival (Lyon), Utopia (Marseille), Big Fest (Paris).

**Labels non suivis** : KNTXT (Charlotte de Witte), Dekmantel, Hyperdub, Mr Bongo, Drumcode
*(suivi)*, Galactica, Disorder/Capitol, Vision (Noisia).

**Institutions / franchises** : ADE (Amsterdam Dance Event), NRJ Music Awards, Bridges for
Music, Top 100 DJs, Boiler Room *(suivi)*, BBC Radio 1 Essential Mix.

**Artistes cités, jamais en source** (top de la fenêtre) : Noisia (4 mentions), Hot Since 82,
Erol Alkan, Shlømo, Bonobo, SOPHIE, Rick Wade, Gaskin, 999999999, Cera Khin, Nikolina, Cloudy,
ØTTA, Sub Zero Project, Vortek's, Eczodia, colinbenders, klangkuenstler, youngpulse, Art Of
Tones, Tryangle Man, Yann Dulché — ces derniers sont précisément le **deal-flow émergent FR**
que l'axe `emergence` est censé capter.

---

# Partie 2 — Les canaux, un par un, sans mythologie

## 2.1 Ce qui marche, et que Kinda peut brancher maintenant

**Groover (et SubmitHub) — le canal le plus sous-estimé.** Guettapen a un profil de curateur
Groover public : « Top curator/pro », « Very impactful », **3 643 réponses données, 83 % de
taux de réponse, 12 % de taux de partage**, genres dance/deep house/house/melodic, refus
explicite de l'IA. Autrement dit : **les artistes leur envoient les sorties directement**,
Groover les paie **1 € par retour rédigé** (minimum 15 mots), et la contrepartie qu'ils offrent
est un article ou une place en playlist. Candidature média/pro ouverte, examinée en 24-48 h ;
il faut un site ou un compte avec une audience réelle et une capacité à offrir de la
couverture. *Évaluation :* branchable **cette semaine**, coût nul (rémunéré), et le flux
entrant est *exactement* la matière de l'axe `emergence`. C'est le seul canal de cette note qui
rapporte de l'argent au lieu d'en coûter.

**Les comptes Instagram professionnels des émetteurs FR d'événements.** La source `instagram`
existe déjà et est gratuite. Il manque les comptes, pas la technique : T7, Cabaret Sauvage,
La Java, Brunch Électronik, miroir.paris, One Life Infinity, Stellar, Les Castelrouquines,
Sonora, Dionysa, Hard Boat, Utopia, Big Fest. *Évaluation :* branchable aujourd'hui, coût nul,
sous réserve que le compte soit business/creator (`business_discovery` ne voit rien d'autre).

**Les Bing News ciblés et les flux d'éditeurs.** Déjà en place, déjà rentables sur les
festivals FR (`rel_moy` 73 sur « Festivals électro FR »). Rien à inventer.

## 2.2 Ce qui exige une relation humaine (et donc du temps, pas du code)

**Listes promo de labels et attachés de presse.** C'est le canal de DJ Mag : 35 % de son flux.
On n'y entre pas par API, on y entre en écrivant à `press@`, en se présentant avec des chiffres
d'audience et en publiant assez régulièrement pour que l'attaché de presse ait intérêt à vous
garder. Pour la scène FR : Ed Banger, Roche Musique, Because, Believe, Pain Surf, Hardfist,
Astropolis Records, plus les agences (Super, Ephelide, Les Bons Contacts). *Évaluation :*
réaliste à 3-6 mois, une fois qu'il y a un feed public à montrer. **Zéro effet avant.**

**Accréditation presse en festival.** C'est le canal d'Electro News : 33 % de son flux, et le
seul contenu vraiment inimitable. Nuits Sonores documente sa procédure (`press@arty-farty.eu`,
accréditations ouvertes jusqu'à mi-avril pour l'édition de mai). Astropolis ne publie pas de
procédure : passer par le contact du festival. *Évaluation :* réaliste, saisonnier, et cher en
temps humain — mais c'est la seule chose qui transforme Kinda en source au sens strict.

**Canaux Broadcast Instagram et DM d'artistes.** Utile en émission (fidéliser), **pas** en
réception : l'API Graph **n'expose pas** la publication en canal Broadcast, et Instagram ne
permet aucune programmation tierce ; tout message de canal doit être envoyé à la main.
*Évaluation :* levier éditorial, jamais un levier de pipeline.

## 2.3 Les impasses, à ne pas réouvrir

**Billetterie.** Shotgun a bien un programme « Promoters » (portail `promoters.shotgun.live`)
et une API, mais **côté organisateur** : l'`Organizer id` + le token API servent à *exporter
ses propres* événements. Il n'y a pas d'API publique de catalogue. Dice : API GraphQL
« Ticket Holders », réservée aux partenaires onboardés, tokens délivrés via MIO, « no public
sign-up flow ». *Évaluation :* **pas d'API de veille**. L'affiliation Shotgun reste un modèle
de **monétisation** (c'est ce que fait Guettapen), pas de détection. Scraper leurs pages
d'événements serait hors ToS pour les deux.

**Beatport.** Catalogue en lecture seule via `api.beatport.com/v4`, mais accès par *partner
portal* et accord développeur, pas de self-serve. Cohérent avec ce que `CLAUDE.md` acte déjà
(« Spotify/Beatport écartés — API fermées/gated »). Ne pas y retourner.

**Bandcamp.** Aucun flux RSS natif. Il existe des générateurs communautaires (GitHub Pages,
Deno) et des scrapers Apify, tous non officiels. *Évaluation :* techniquement faisable, mais
c'est du scraping d'un tiers pour alimenter un média — le même reproche qu'on fait aux
reposteurs, avec en plus un risque ToS. À écarter sauf besoin précis.

**Bandsintown / Songkick.** Bandsintown délivre une clé API **par artiste**, depuis le compte
artiste (« Each API Key is linked to a single artist ») : inutilisable comme radar de scène.
Songkick a une API d'événements, mais son intérêt est faible sur le club électro FR, mal
couvert.

**Reddit / 4chan pour les fuites.** Zéro fuite mesurée dans la fenêtre. Reddit sert de
thermomètre (r/electronicmusic a publié Noisia 10 min après Guettapen), pas de source
d'antériorité.

**Charts / DSP.** Zéro item d'origine « données de plateforme » chez les quatre concurrents
électro. Confirme la position de `CLAUDE.md` : inutile de concurrencer Chartmetric.

---

# Partie 3 — Plan classé : devenir une source

Classement par (avance gagnée) ÷ (coût). Chaque entrée dit ce que ça change dans ce dépôt.

### 1. Rendre le pipeline aussi rapide sur les artistes que sur la presse

*Pourquoi d'abord :* c'est la seule action qui change un chiffre mesuré aujourd'hui, sans
dépendre de personne. 141 min → quelques minutes.

*Le calcul de quota, honnêtement.* 123 sources Instagram actives × 8 lectures/jour = **984
appels/jour**, soit 41/h en moyenne. Le plafond observé en production (`CLAUDE.md`,
2026-09-17) : ~430 appels dans l'heure ont déclenché le code 4 pendant une heure. Donc :

| Scénario | Appels/h | Tient sous ~430/h ? | Latence médiane attendue |
|---|---:|---|---|
| Actuel (123 comptes / 3 h, run horaire) | 41 | oui | 141 min *(mesuré)* |
| Tous les comptes toutes les heures | 123 | oui | ~50 min |
| Tous les comptes toutes les 5 min | **1 476** | **non (3,4×)** | — |
| Veille de 12 comptes / 5 min + 111 comptes / 3 h | **181** | oui | **~5 min** sur les 12 |

*Réponse à la question posée :* **oui, Kinda peut être premier sur une annonce d'artiste**,
mais seulement sur une **liste courte** de comptes, et seulement sur des comptes
**professionnels** (`business_discovery` ne voit pas un profil perso — donc pas le jeune
artiste émergent). Sur le cas Charlotte de Witte, un tel dispositif aurait publié ~5 h 35
avant DJ Mag. Endpoint inchangé (`business_discovery`), coût API nul (quota, pas facturation),
coût réel = le scoring Claude des items supplémentaires.

*Changements dans le code :* une colonne ou une clé d'état `cadence` sur `sources` (ou une
liste dans la config) que `InstagramAdapter._doit_appeler()` (`app/sources/instagram.py:319`)
consulte à la place du seul `instagram_intervalle_heures` ; un quatrième agent launchd
`com.musicnewsradar.veille` toutes les 5 min dans `scripts/install_agents.py`, qui n'ingère
que les sources de la veille et ne score qu'elles — et **son log dans la conf `newsyslog`**,
donc `--status` redira « obsolète » et il faudra repasser le `sudo` (piège déjà documenté).
`attendre_base()` en premier, comme tout point d'entrée planifié.

*Droits :* aucun sujet. C'est l'API officielle de Meta, avec le jeton déjà en place.

*Premier pas :* choisir les 12 comptes. Proposition, tirée de la fenêtre : Charlotte de Witte,
Vladimir Cauchemar, Fred again.., Justice, Ed Banger, Astropolis, Nuits Sonores, Transmusicales,
Positive Education, Trym, Madeon, Worakls.

### 2. Créer le profil curateur Groover (et SubmitHub)

*Pourquoi :* c'est le seul canal qui **inverse le sens du flux**. Aujourd'hui Kinda va
chercher ; là, on lui envoie. Guettapen en a tiré 3 643 retours. Et le flux entrant est
précisément de l'artiste émergent — l'axe `emergence`, le deal-flow du label, la brique 3 du
projet.

*Coût :* nul, rémunéré 1 €/retour. Contrainte : **répondre**, 15 mots minimum, sans quoi le
profil se déclasse. C'est un engagement humain récurrent, pas une intégration.

*Changements dans le code :* à terme, une source `groover` (un adapter de plus dans
`app/sources/`, registry dans `__init__.py`, `categorie = 'artiste'`, contrat `fetch() ->
list[RawItem]` inchangé). Rien d'urgent : on commence à la main, on n'automatise que si le
volume le justifie.

*Droits :* les soumissions sont faites *pour* être écoutées et couvertes — c'est l'objet même
de la plateforme. Vérifier les conditions curateur avant de reposter un visuel.

*Premier pas :* candidature sur `groover.co/en/influencer/signup/` (24-48 h de délai), avec le
compte Instagram et les genres : techno, hard techno, house, mélodique, scène FR.

### 3. Ajouter les émetteurs d'événements FR comme sources primaires

*Pourquoi :* 7/14 de Guettapen et 6/15 d'Electro News sont des annonces d'événements. C'est le
plus gros trou mesuré, et il se bouche avec la mécanique existante.

*Coût :* ~20 appels/h supplémentaires si on ajoute une vingtaine de comptes au rythme de 3 h.
Négligeable.

*Changements dans le code :* **aucun**. `scripts/add_source --type instagram --url ig:<compte>
--categorie festival|club|label`. Éventuellement une extension du mapping explicite de
`scripts/categoriser_sources.py` pour les nouveaux noms.

*Le préalable non technique :* **il faut relever `RETENTION_DAYS`.** À 3 jours, un festival qui
poste le lundi est invisible le jeudi. On ne peut pas suivre des sources hebdomadaires avec une
base à 3 jours. Découpler les deux (garder 3 jours de fraîcheur pour la **file**, garder
14 jours en **base**) est un changement de config, pas de code — `app/pipeline/retention.py` ne
connaît qu'un nombre.

*Droits :* API officielle, comptes publics professionnels. Ne jamais reposter leur visuel sans
crédit — la règle 4 de la spec du 17/09 s'applique déjà aux médias, elle vaudra ici aussi.

*Premier pas :* `uv run python -m scripts.add_source` pour T7, Cabaret Sauvage, Brunch
Électronik, One Life Infinity, Stellar, Sonora, Hard Boat, Utopia, Big Fest, Les Castelrouquines.

### 4. Publier la donnée que personne d'autre n'a : `rising_artists`

*Pourquoi :* c'est le seul endroit où Kinda est **déjà** une source primaire et ne le sait pas.
3 347 artistes, 9 528 mentions, historique depuis le 13/07 — la courbe de citation d'un artiste
dans la presse électro FR, Reddit et l'UGC. Aucun des quatre concurrents ne publie ça, et
Chartmetric ne le mesure pas (il mesure l'aval : streams, playlists). Un format hebdomadaire
« les 5 noms dont on a le plus parlé cette semaine, et depuis quand » est **du contenu que les
autres devraient citer**. C'est littéralement l'inverse de l'intermédiaire.

*Réserve mesurée, à traiter avant de publier :* le top actuel est pollué par la K-pop (Stray
Kids 298 mentions, SEVENTEEN 188, aespa 176) — le volume des sources pop écrase le signal
électro FR. La fonction `rising_artists()` existe (`app/pipeline/artists.py`) mais il faut un
filtre par genre de source, exactement comme le radar X en a un (« GATE PAR GENRE DE SOURCE »,
`CLAUDE.md`), sans quoi le premier post publierait un classement K-pop.

*Changements dans le code :* un paramètre de genre/catégorie sur `rising_artists()` ; un
endpoint ou un panneau dashboard ; à terme un gabarit de cover dédié dans
`app/content/templates/`.

*Droits :* c'est notre mesure, sur des données publiques. Rien à demander.

*Premier pas :* rejouer `rising_artists()` avec le gate électro/FR et regarder si le top 5 est
défendable. Si oui, en faire le rendez-vous hebdomadaire du compte.

### 5. Accréditation presse : Astropolis, Nuits Sonores, Positive Education

*Pourquoi :* le seul canal qui produit du contenu **inimitable**, et le seul qui explique le
tiers du feed d'Electro News. C'est aussi ce qui débloque le reste : un média accrédité est un
média à qui les attachés de presse écrivent.

*Coût :* du temps humain et du déplacement. Rien de technique.

*Changements dans le code :* aucun à court terme. À terme, les captations alimentent les reels
(`app/content/video.py`), qui aujourd'hui dépendent d'une vidéo de l'actu — et « aucune source
ne fournissait plus de vidéo » au moment de la conception de la file. Des vidéos maison
règleraient ce manque à la racine.

*Droits :* l'accréditation encadre précisément ce qu'on peut filmer et publier. La lire.

*Premier pas :* écrire à `press@arty-farty.eu` pour Nuits Sonores (fenêtre d'accréditation
2027 à surveiller, celle de 2026 fermait mi-avril) et au contact d'Astropolis, en joignant les
chiffres du compte.

### 6. Listes promo labels et agences

*Pourquoi :* c'est le canal de DJ Mag, et celui qui donne l'**embargo** — recevoir à J-3 ce que
les autres publient à J. Mais il ne s'ouvre pas à un compte sans historique.

*Coût :* nul en euros, lourd en relation. À engager **après** que 1, 2 et 3 aient produit un
feed régulier à montrer.

*Changements dans le code :* une boîte mail promo dédiée puis, si le volume le justifie, un
adapter `email` dans `app/sources/` (IMAP → `RawItem`). Le contrat de la couche sources le
permet sans rien toucher ailleurs — c'est précisément ce que le caractère pluggable achète.

*Premier pas :* ouvrir l'adresse `promo@`, puis écrire à Ed Banger, Astropolis Records,
Hardfist, Roche Musique.

### 7. Contenu original : interviews et guestmix

*Pourquoi :* c'est le modèle de Hands Up Électro (~12 K) et une partie de Mixmag. Ça ne rend pas
plus rapide ; ça rend **citable**. Et c'est l'amorce directe de la brique 3 (interviews croisées
pour le label).

*Changements dans le code :* le pipeline ne sait pas créer un article sans source. Il faudra
soit une source `manuel`, soit un endpoint de création côté dashboard.

*Premier pas :* une interview d'un artiste du top `rising_artists` filtré électro/FR. Elle sert
deux fois : contenu, et premier contact A&R.

---

## Ce qu'il ne faut pas se raconter

- **Aucune optimisation de collecte ne rattrapera 13 items sur 15.** Electro News est
  non traçable à 87 % parce qu'il *était sur place*. Le code ne remplace pas un billet de train.
- **Être premier sur GTA VI est impossible et sans intérêt** : six médias en 1 h 47, tous avec
  le même communiqué. La différenciation est ailleurs.
- **Le gain de vitesse n'est réel que sur les comptes professionnels.** Le jeune artiste
  émergent en profil perso reste invisible de `business_discovery`. Pour lui, le canal est
  Groover, pas l'API.
- **Publier plus vite que DJ Mag ne sert à rien si on publie la même chose.** Le seul énoncé qui
  tienne est : « Kinda l'a dit parce que l'artiste le lui a dit / parce que Kinda y était /
  parce que Kinda l'a mesuré. »

---

## À décider par l'utilisateur

1. **`RETENTION_DAYS`.** Le passer de 3 à 14 en base tout en gardant 3 jours de fraîcheur pour
   la file ? Sans ça, les sources primaires FR (festivals, labels) resteront structurellement
   invisibles — 54 sources sur 123 n'ont aucun article aujourd'hui pour cette seule raison.
   *(C'est le blocage le plus bête et le plus coûteux de la note.)*
2. **L'agent de veille à 5 minutes.** On l'ajoute ? Si oui, **quels 12 comptes** ? Chaque compte
   ajouté à cette liste coûte 12 appels/h de quota et du scoring Claude à chaque nouveau post.
3. **Groover.** On candidate ? C'est un engagement à répondre régulièrement (15 mots minimum par
   titre) — un rendez-vous humain hebdomadaire, pas une intégration qu'on oublie.
4. **Accréditations presse.** Est-ce que l'utilisateur est prêt à se déplacer et filmer ? Toute
   la Partie 3 ne vaut que si la réponse est oui au moins pour Astropolis (l'angle breton).
5. **Le rendez-vous `rising_artists`.** En fait-on le format signature du compte ? Et accepte-t-on
   de **filtrer la K-pop** de ce classement, alors que la ligne éditoriale dit de ne pas fermer
   le compte à la pop ? *(Proposition : filtrer le classement A&R, pas le feed.)*
6. **Tsugi RSS rend zéro article.** Anomalie à investiguer ou source à retirer ? Elle est
   interrogée avec succès (`last_ok_at` du 18/09) mais ne dépose rien.
7. **L'affiliation Shotgun** (le modèle prouvé de Guettapen) : c'est de la monétisation, pas de
   la veille. Sujet à ouvrir maintenant ou plus tard ?
