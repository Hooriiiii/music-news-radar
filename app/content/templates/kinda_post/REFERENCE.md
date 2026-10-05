# Templates de rendu Kinda (`kinda_post`, `kinda_reel`)

Deux gabarits partagent cette architecture : `kinda_post` (1080×1350, ce dossier)
et `kinda_reel` (1080×1920, `../kinda_reel/`). Ce fichier documente les deux —
seul `kinda_post` a une frame Figma extraite, voir plus bas.

Figma reste la source de vérité du design ; ce dossier en est la transcription.
Après toute refonte du template dans Figma, refaire l'extraction : lire la frame,
mettre à jour `style.css`, puis reboucler sur le diff visuel contre un export PNG
de la frame. Le gabarit ne doit JAMAIS être modifié à l'aveugle.

## Pourquoi une transcription et pas un rendu Figma

L'API REST de Figma est en LECTURE SEULE sur le contenu d'un fichier : on peut lire
un document et exporter un node en PNG, on ne peut pas écrire un texte dans un
calque. L'écriture passe uniquement par l'API Plugin, qui s'exécute dans l'éditeur
ouvert. Figma ne peut donc pas rendre à l'exécution : le template est extrait UNE
FOIS en HTML/CSS versionné, puis rendu par un Chromium headless
(`app/content/render.py`).

Les rectangles roses de Figma ne sont PAS reproduits en dur : ils deviennent des
`<mark>` qui suivent le texte automatiquement (voir « Surlignage » plus bas).

## Architecture : valeurs en Python, gabarits sans nombres

Les deux stylesheets (`kinda_post/style.css`, `kinda_reel/style.css`) sont des
**squelettes sans valeurs** : aucune taille de police, aucune couleur, aucune
position n'y est écrite en dur. Toutes les valeurs de mise en page vivent dans
`app/content/layout.py` (`GABARIT_POST`, `GABARIT_REEL`, dataclasses gelées) et
sont injectées au rendu sous forme de variables CSS custom
(`variables_css()` dans `layout.py`, consommées via `--headline-font-size`,
`--title-background`, etc.). Une valeur qui reviendrait en dur dans une feuille de
style serait une règle qui échappe à `layout.py`, donc aux tests : c'est ce que
vérifie `tests/content/test_render.py::test_le_css_ne_contient_plus_de_valeurs_de_mise_en_page`
pour `kinda_post/style.css` (recherche de motifs comme `80px`, `-0.06em`, `77px`,
et interdiction d'un bloc `:root` qui rouvrirait la porte à des valeurs locales).
`kinda_reel/style.css` suit la même discipline par construction (tâche 4) mais
n'a pas encore son sentinelle dédié — à ajouter si la feuille du reel se remet à
dériver.

`layout.py` ne connaît NI Chromium, NI Jinja, NI le réseau, NI la base : c'est du
Python pur, testable sans navigateur (`tests/content/test_layout.py`). La répartition des
rôles avec `render.py` est stricte : Chromium MESURE (lignes rendues, largeur de
chacune, via `Range.getClientRects()`), Python DÉCIDE (échelle à essayer,
acceptation, abandon — la machine à états `Ajustement`).

## Ce que le gabarit rend

Le gabarit ne rend plus un carrousel de slides de texte : il rend **une
cover** — la première image du post (`slide[0]`, `kind: "cover"` dans
`posts.slides`), fond + logo + accroche. Les slides suivantes d'un post
(`kind: "media"`) sont les photos/vidéos réelles de l'actu ; elles ne passent
JAMAIS par ce gabarit ni par Chromium (`app/content/render.py::render_post`
ne rend que les entrées `kind: "cover"`, `app/content/export.py` télécharge
les `kind: "media"` telles quelles). `render_covers()` réutilise ce même
gabarit pour rendre les 4 variantes de cover proposées à l'utilisateur, sur un
fond commun.

## Contrat de variables (`app/content/render.py::build_document`)

`build_document(gabarit_, textes, image_data_uri, scale, scrim_height)` assemble
le HTML autonome d'UNE cover. Deux entrées consomment ce contrat et cassent le
rendu si on les change sans mettre ce fichier à jour :

- `variables` : les variables CSS produites par `variables_css()` — voir la liste
  des noms dans `layout.py`. Les variables absentes d'un gabarit (ex. `--scrim-height`
  pour le reel qui n'a pas de voile) ne sont PAS écrites à vide, pour éviter un
  `background: ;` silencieusement invalide.
- `textes` : un dict `{role: texte}` — plus une accroche unique. Les clés doivent
  correspondre aux rôles déclarés dans `Gabarit.roles` (`headline` pour le post ;
  `title`, `subtitle` pour le reel). Chaque valeur passe par `to_html()`
  (`app/content/highlight.py`) qui transforme `**segment**` en `<mark>`.

**Les ids HTML sont consommés par le JS de mesure, pas seulement par le CSS** :
`_MESURE_JS` dans `render.py` fait `document.getElementById(role)` avec le NOM DU
RÔLE (`headline`, `title`, `subtitle`) comme id — voir `slide.html.j2` de chaque
gabarit (`<div id="headline">`, `<span id="title">`, `<span id="subtitle">`).
Renommer un id dans un template sans le répercuter casse le comptage de lignes
**en silence** : `getElementById` renvoie `null`, `mesurer_role()` lève une
exception JS invisible dans le rapport, pas une image visiblement fausse. C'est le
piège le plus facile à ne pas voir en review — vérifier systématiquement que
`id="{role}"` dans le `.j2` correspond aux clés de `Gabarit.roles`.

## Références visuelles

Deux PNG dans `exemples/` (racine du dépôt, pas ce dossier) servent de référence
de comparaison pour les deux gabarits :

- `exemples/[PREVIEW] - Post information.png` — maquette du post ; à comparer à un
  export de la frame Figma `234:8` (§ ci-dessous).
- `exemples/[PREVIEW] - Cover Reels.png` — seule référence du reel, en l'absence de
  frame Figma extraite (§ « Gabarit `kinda_reel` »).

**Après toute modification d'un template** (style.css, slide.html.j2, ou valeurs
dans `layout.py`), rerendre une slide de référence et comparer visuellement à ces
deux PNG avant de committer. Rien dans la suite de tests ne remplace cette
comparaison à l'œil : les tests vérifient la structure et les valeurs numériques,
pas la lisibilité du résultat.

---

## Gabarit `kinda_post`

Extrait du fichier Figma `kAqCOUyZgvR025RuICJuPW` (« Kinda design »),
frame `234:8` (« TEMPLATE »), le 2026-07-22. Frame Figma 1080x1440 ; **rendu en
1080x1350**, voir « Hauteur de la toile » ci-dessous.

### Valeurs relevées dans Figma

| Element | Node | Valeur |
|---|---|---|
| Frame | `234:8` | 1080 x 1440 |
| Photo de fond | `234:10` | plein cadre, recadrage `cover` |
| Voile noir haut | `234:11` | y=0, h=727, `rgba(0,0,0,.8)` -> transparent |
| Voile rose bas | `234:28` | y=623, h=817, `rgba(253,139,195,0)` -> `rgba(253,139,195,.3)` |
| Voile noir bas | `234:29` | y=623, h=817, `rgba(0,0,0,0)` -> `rgba(0,0,0,.8)` |
| Logo | `234:12` / `234:13` | 150 x 74.75, aligne a DROITE, bord droit x=1003, y=87 |
| Accroche | `234:34` | Arial Narrow Bold 80 px, interlignage 92.8 px, interlettrage -4.8 px (-0.06 em), couleur `#fff7fa`, bloc x=77 large de 926, y=1088 |
| Surlignage | `234:31` / `237:41` / `234:32` | `rgba(253,139,195,.8)`, hauteur 72 px (0.9 em), debord horizontal ~3 px |
| Guideline | `234:35` | marges 77 px lateral, 85 px haut/bas |

Le bloc d'accroche est ancré par le BAS (`bottom: 76px`) et non par le haut : une
accroche plus longue, ou une échelle plus petite (`--scale` réduite par
`Ajustement`), doit pousser vers le haut sans jamais déborder sous la ligne de
garde des 85 px. Ces valeurs vivent dans `GABARIT_POST` (`app/content/layout.py`),
pas dans `style.css`.

### Hauteur de la toile : 1350 et non les 1440 de la frame (2026-09-18)

La frame Figma est en 3:4, **un rapport que Meta refuse dans le fil** (4:5 à
1,91:1). La cover était donc rendue en 1080x1440 puis recadrée au centre en
1080x1350 juste avant publication, par `app/publish/images.py`.

Ce recadrage prenait 45 px en haut et 45 px en bas — c'est-à-dire, sur l'image
que les gens voient : la moitié de la marge du logo (87 → 42) et 60 % de celle
de l'accroche (76 → 31). Toutes les marges posées contre les bords, et elles
seules, étaient fausses à la publication. C'est ce que l'utilisateur a vu en
comparant aux premiers posts.

La cover est donc **rendue nativement au format publié** et plus rien n'est
recadré. Les valeurs du dessin ne bougent pas : 77 px de marge latérale, 87 px
pour le logo, 76 px sous l'accroche, 817 px de voile bas, 727 px de voile haut —
toutes se comptent depuis un bord ou depuis le bloc de texte, et la largeur n'a
pas changé. Ce qui disparaît, ce sont 90 px de **photo** au milieu du cadre,
exactement ce que la publication retirait déjà.

Conséquence pour une ré-extraction Figma : les valeurs relevées dans la frame
1440 restent valables telles quelles SAUF une position mesurée depuis le HAUT
d'un élément ancré en bas (l'accroche est à `y=1088` dans la frame, ce qui
correspond à `bottom: 76`, la seule forme à transcrire). Deux sentinelles
gardent la règle : `test_le_post_est_rendu_au_format_publie`,
`test_les_marges_publiees_sont_celles_de_la_maquette` (mesure au pixel dans
Chromium).

### Plafond de lignes (`max_lines`, calibré tâche 8)

Le choix de design est « police fixe, bloc de texte extensible » : une accroche
plus longue prend plus de lignes plutôt que de réduire la police en continu. Le
plafond `max_lines=5` de `GABARIT_POST` existe pour arrêter cette croissance avant
qu'elle ne mange toute la photo — au-delà, l'échelle est réduite.

Calibré en rendant des accroches de longueur croissante sur une VRAIE photo (pas
le fallback sombre, qui masque l'effet) et en regardant à partir de combien de
lignes le sujet de la photo cesse d'être visible sous le texte. Mesures (92 px par
ligne à l'échelle 1.0, formule confirmée au pixel par Chromium) :

| Lignes | Hauteur du bloc | Observation visuelle |
|---|---|---|
| 2 | 184 px | photo intacte |
| 3 | 276 px | photo intacte (accroche de référence) |
| 4 | 368 px | sujet toujours visible (torse, jambes) |
| 5 | 460 px | sujet encore visible sous le bloc |
| 6 (déjà réduit par le plafond) | 552 px | limite haute testée hors plafond, sujet encore lisible |
| 7-8 (rendu forcé hors plafond, à l'échelle pleine) | ~640-736 px | le bloc recouvre les visages : la photo cesse d'exister comme image |

5 est donc le dernier palier où la photo « existe » encore. `HEADLINE_MAX = 110`
dans `app/content/compose.py` plafonne déjà les accroches normales à ~4 lignes
(l'accroche de référence, ~93 caractères, en fait 3) : le plafond de 5 lignes est
un filet de sécurité, pas le régime courant.

### Police : substitut à valider

Le fichier Figma utilise **Arial Narrow Bold**, une police commerciale (Monotype)
qu'on ne peut ni redistribuer dans ce dépôt ni embarquer en base64.

`style.css` embarque à la place **Liberation Sans Narrow Bold** (Red Hat, GPLv2 avec
exception d'embarquement de police), qui est *métriquement compatible* avec Arial
Narrow : mêmes chasses, donc **mêmes retours à la ligne**. C'est la propriété qui
compte ici — une police simplement « ressemblante » ferait tomber l'accroche de
référence sur quatre lignes au lieu de trois.

**À faire valider par le propriétaire du design.** Vérifié sur l'accroche de
référence : trois lignes identiques, mêmes coupes, positions des lignes à 1 px près.
Les dessins de glyphes diffèrent légèrement d'Arial Narrow (largeur de mot à ~2 %
près sur certains mots), sans effet visible à l'œil.

Si le propriétaire préférait une autre police, il faut refaire la boucle de diff
visuel : la taille, l'interlignage et l'interlettrage sont calibrés sur celle-ci.

### Détails d'implémentation qui ne se devinent pas

- **`@font-face` dupliqué** : le bloc `@font-face` (police embarquée en base64)
  est répété OCTET POUR OCTET dans `kinda_post/style.css` et
  `kinda_reel/style.css` — chaque gabarit est une feuille de style autonome,
  aucune n'importe l'autre. Ses guillemets doubles (`font-family: "KindaDisplay"`,
  `format("woff2")`) doivent survivre à l'interpolation Jinja NON échappés :
  `build_document()` passe la feuille via `Markup(...)` (pas une simple
  interpolation `{{ css }}` sur une `str`) précisément pour ça — l'autoescape de
  Jinja transformerait ces guillemets en `&#34;`, ce qui invaliderait
  silencieusement l'`@font-face`. Aucune erreur ne remonte : Chromium retombe sur
  un serif système, avec d'autres retours à la ligne que le gabarit Figma, et rien
  ne le signale sauf à comparer visuellement contre `exemples/`. Test qui garde
  ce piège fermé : `tests/content/test_render.py::test_la_feuille_de_style_nest_pas_echappee_par_le_gabarit`.
- **Surlignage** : le fond du `<mark>` passe par un `linear-gradient` plat plutôt
  que par `background-color`. La hauteur du fond d'un élément en ligne est imposée
  par les métriques de la police (~1.15 em), alors que le rectangle de Figma fait
  72 px pour 80 px de corps (0.9 em) ; `background-size` fixe cette hauteur exacte
  et `background-position` la cale comme dans Figma.
- **`box-decoration-break: clone`** fait que le fond se redessine sur CHAQUE ligne
  quand le segment surligné passe à la ligne. Sans lui, le fond est étiré en un
  seul bloc à travers le retour à la ligne.
- **Les trois voiles** sont trois couches `background-image` d'un seul `#scrim`,
  dans l'ordre de la pile Figma (le voile noir bas est au-dessus du rose). La
  hauteur du voile (`--scrim-height`) n'est PAS fixe : `hauteur_voile()` dans
  `layout.py` la recale sur la hauteur mesurée du bloc d'accroche (`SCRIM_MARGE`,
  `VOILE_MINIMUM`), pour que le dégradé suive un bloc qui grandit avec le texte.
  D'où le rendu en DEUX passes dans `render.py::_render_one` : la première mesure
  la hauteur du texte, la seconde rend avec le voile recalé.
- Le `padding` horizontal du `<mark>` décale de 3 px le texte des lignes qui
  commencent par un surlignage (ligne 3 de l'accroche de référence). Les
  rectangles de Figma sont posés à la main ; l'écart est imperceptible.

### Assets

- `app/content/assets/logo.png` — export SVG du node `234:13` rasterisé à 4x
  (600 x 299, fond transparent). Le PNG rendu directement par Figma embarquait le
  fond blanc de la page : ne pas le reprendre tel quel.
- `app/content/assets/fallback.jpg` — aplat sombre 1080x1440 aux couleurs de Kinda
  (un dégradé, recadré en `cover` sur la toile 1350 : rien à regénérer),
  utilisé quand la photo de l'article est introuvable. Volontairement pauvre : il ne
  doit jamais être joli au point qu'on oublie de le remplacer. Ne PAS l'utiliser
  pour juger un rendu à l'œil (surlignage, plafond de lignes) — il masque les
  effets qui dépendent du contenu de la photo ; toujours comparer sur une vraie
  photo ou sur `exemples/`.

---

## Gabarit `kinda_reel`

Format 1080×1920, deux rôles (`title`, `subtitle`), chacun porté par une bande de
couleur pleine plutôt qu'un dégradé sur photo — le contraste vient des bandes, pas
d'un voile (`GABARIT_REEL.voile = False`). Bloc ancré par le BAS, à 420 px du bord
(`block_bottom`, soit y=1500 sur la toile 1920) — comme le post, mais pas collé au
bord : la zone d'interface Instagram (légende, nom de compte, colonne de boutons)
se superpose au bas d'un reel. Avant le 2026-09-18 le bloc était centré à 42,9 %
de la hauteur (`block_center`), ce qui le faisait ressortir au milieu du cadre
dans la grille du profil, en corps étranger au milieu des posts dont le bloc est
en bas — voir le commentaire sur `REEL_LIMITE_BASSE_UI` dans `layout.py`.

**Sa source est `exemples/[PREVIEW] - Cover Reels.png`, PAS une frame Figma
extraite.** Aucun node Figma n'existe encore pour ce gabarit — contrairement au
post, dont chaque valeur remonte à la frame `234:8`. **Une frame devrait être
extraite au prochain passage design** ; en attendant, les valeurs ci-dessous sont
mesurées au pixel sur l'export PNG, avec les limites que ça implique (pas de
node id, pas de valeur "officielle" à comparer).

### Valeurs mesurées

| Rôle | Fond | Couleur texte | `font_size` | `line_height` | `letter_spacing` | Inclinaison |
|---|---|---|---|---|---|---|
| `title` | `#FD8BC3` (rose) | `#FFF7FA` | 84.3 px | 1.1 | -0.02 em | 0° (droite) |
| `subtitle` | `#FFF7FA` (crème) | `#1D1D1D` | 75.3 px | 1.1 | -0.08 em | -0.6° (antihoraire) |

Piège à l'œil documenté parce qu'il a déjà failli être codé de travers : la bande
rose (`title`) est DROITE, la bande crème (`subtitle`) est inclinée de 0,6°
antihoraire (effet autocollant). Confirmé comme voulu par le propriétaire du
design — ne pas "corriger" en alignant les deux bandes.

### Comment ces nombres ont été obtenus

`font_size`, `letter_spacing` (et le padding de `style.css`) ne se déduisent PAS
par inversion des glyphes de la maquette — une police de substitution n'a pas les
mêmes métriques que celle de l'export PNG. Ils sont calés en rendant puis en
comparant (`app/content/render.py::mesurer_bandes`), dans cet ordre — la largeur
de bande dépend des trois valeurs à la fois, la hauteur seulement de
`font_size`/`line_height`/padding vertical, ce qui découple le problème :

1. `font_size` calé sur la HAUTEUR de bande mesurée sur la maquette (94 px rose,
   89 px crème) — insensible à `letter_spacing` ;
2. `letter_spacing` calé ensuite sur la LARGEUR de bande mesurée (534 px rose,
   753 px crème), `font_size` étant déjà fixé.

**Ces chiffres sont calés sur le texte d'exemple utilisé à la calibration** :
`"Gregoire Cliquet"` (titre) et `"au festival des vieilles Charrues"` (sous-titre)
— voir `tests/content/test_render.py::test_les_largeurs_de_bande_retombent_sur_la_maquette`
et `::test_les_hauteurs_de_bande_retombent_sur_la_maquette`. Une recalibration sur
un texte différent (longueur de chaîne différente) ne retombera pas forcément sur
ces mêmes valeurs : la largeur de bande dépend du nombre de caractères autant que
du corps de police. Ne pas réutiliser cette procédure sur une autre chaîne de
référence en s'attendant aux mêmes nombres.

### Ce qui reste hors périmètre

Le plafond `max_lines=2` par rôle (une bande, ou deux si le titre est trop long
pour tenir sur une — voir `tests/content/test_render.py::test_un_titre_de_reel_trop_long_passe_sur_une_deuxieme_bande`)
et les autres valeurs de `GABARIT_REEL` sont hors du périmètre de la tâche 8 : la
calibration du reel a été réglée et validée à la tâche 5, ne pas la rouvrir ici
même si une mesure ultérieure semble diverger — remonter l'écart plutôt que de
corriger la valeur en place.
