# Rendu adaptatif des gabarits Kinda — design

Date : 2026-08-08
Chantier : **A** (voir « Décomposition » plus bas)
État : validé avec l'utilisateur, prêt pour le plan d'implémentation

## Le problème

Le gabarit de post est aujourd'hui une transcription HTML/CSS figée de la frame
Figma. Toutes les valeurs de mise en page — corps de 80 px, interlignage 1.15,
interlettrage -0.06 em, hauteur maximale 320 px, paliers de réduction
`(1.0, 0.9, 0.8)` — sont écrites en dur, éparpillées entre `style.css` et
`render.py`. Elles ne sont ni paramétrables, ni testables.

Conséquences concrètes :

1. **Adaptation grossière.** Une accroche à peine trop longue perd 10 % d'un
   coup, ce qui se voit. Une accroche vraiment longue **déborde quand même**
   après le dernier palier : on se contente d'un `logger.warning` et on rend
   l'image coupée.
2. **Un seul gabarit.** Le format cover reel (1080×1920) n'existe pas.
3. **Toute retouche du design passe par une transcription CSS manuelle**, sans
   filet de test.

## Ce qui n'est PAS le problème

Figma n'est jamais appelé à l'exécution, et ne l'a jamais été. Son API REST est
en lecture seule sur le contenu d'un fichier ; l'écriture passe par l'API Plugin,
qui n'existe que dans l'éditeur ouvert. La chaîne est déjà entièrement Python :
`compose.py` → `build_document()` (Jinja2) → Chromium headless → PNG.

Le lien avec Figma est humain et hors exécution : `REFERENCE.md` documente la
procédure de ré-extraction. Ce lien-là reste — Figma est la table à dessin.

## Décisions prises avec l'utilisateur

| Décision | Choix retenu | Motif |
|---|---|---|
| Moteur de rendu | **Chromium conservé** | Il fait de la composition typographique : césures FR, interlettrage négatif, et surtout `box-decoration-break: clone` qui redessine le fond rose sur chaque ligne quand un segment surligné passe à la ligne. En Pillow, tout cela est un moteur de mise en page à réécrire — quelques centaines de lignes fragiles pour un rendu qui divergerait des maquettes. |
| Où vivent les règles | **En Python**, dans `layout.py` | C'est là qu'est le vrai gain de contrôle : sortir les valeurs du CSS, pas retirer le navigateur. |
| Adaptation à la longueur | **Police fixe, bloc extensible** | Cohérence typographique entre posts. La réduction de police devient un filet, pas le régime normal. |
| Hauteur du post | **1080×1440 (3:4)** | La frame Figma `234:8` fait 1440 ; le PNG d'aperçu à 1350 est un export 4:5. Code et Figma étaient déjà d'accord. |
| Inclinaison de la bande crème | **Reproduire**, ~0,6° antihoraire | Effet « autocollant » voulu. Devient un paramètre du gabarit. |
| Périmètre | **Rendu seulement** | La composition automatique des titres de reel et la sélection des vidéos sont un autre chantier. |

## Décomposition et ordre (contexte)

L'utilisateur a décrit huit chantiers. Chacun aura sa propre spec.

- **0. Socle local** — agents launchd sur le Mac, **base Neon conservée**
  (sauvegardée, accessible d'ailleurs, aucune migration).
  *Révisé le 2026-08-09 — voir `2026-08-09-socle-local-design.md` :* ce document
  actait « GitHub Actions coupé », et c'est **annulé**. Les alertes Discord partent
  du pipeline ; un portable fermé fait rattraper le job au réveil, donc l'alerte
  arriverait le soir et perdrait sa raison d'être. Le cron cloud reste donc en
  place pour l'ingestion, le scoring et les alertes ; le Mac fait l'ingestion
  seule (IP résidentielle, ce qui débloque Mixmag et Reddit) et tient le dashboard
  en permanence. LM Studio n'est pas lancé au démarrage : sans consommateur avant
  le chantier C, un modèle 7B chargé immobiliserait ~5 Go pour rien.
- **A. Rendu** — *ce document*.
- **B. File quotidienne + feedback** — 5 posts/jour mis en avant, validé/refusé
  avec motif écrit.
- **C. Tri par modèle local** — `hermes-2-pro-mistral-7b` via LM Studio.
- **D. Sources proposées, puis reels** — l'IA *propose* d'ajouter/couper des
  sources, l'humain valide.

L'ordre n'est pas esthétique : **B produit la donnée sans laquelle C et D sont
creux** (un modèle local n'apprend sur rien tant qu'il n'y a pas des centaines de
décisions validé/refusé).

Deux points tranchés en amont, qui touchent l'architecture :

- **Le calendrier disparaît.** L'utilisateur a lui-même convergé vers « juste
  tous les jours 5 posts avec les actus les plus chaudes ». Sans créneaux
  réservés, une grosse actu n'a rien à décaler : elle **remonte dans un
  classement**, ce que `hotness` fait déjà. Le chantier « priorité dynamique »
  s'évapore.
- **L'auto-gestion des sources reste sous validation humaine.** Une IA qui coupe
  et ajoute des sources en autonomie dérive en silence : elle coupe une source
  calme mais précieuse, elle ajoute un agrégateur qui pollue. Elle propose, on
  valide d'un clic.

---

# Le design

## 1. `app/content/layout.py` — les règles

Module nouveau, **sans aucune dépendance** : ni Chromium, ni Jinja, ni réseau, ni
base. De la géométrie et de la typographie en dataclasses gelées. Testable en
millisecondes.

```python
@dataclass(frozen=True)
class Typo:
    font_size: float          # px, valeur de la maquette
    line_height: float        # multiplicateur
    letter_spacing: float     # em
    color: str
    background: str | None = None    # bande de fond (cover reel)
    tilt: float = 0.0                # degres, negatif = antihoraire

@dataclass(frozen=True)
class Zone:
    left: int
    right: int
    anchor: Literal["top", "bottom", "center"]
    anchor_offset: int
    align: Literal["left", "center"]
    max_lines: int            # plafond -> au-dela on reduit
    max_line_width: int       # plafond de largeur (bandes du reel)
    min_scale: float          # plancher absolu

@dataclass(frozen=True)
class Gabarit:
    name: str
    width: int
    height: int
    roles: dict[str, tuple[Typo, Zone]]
```

### La couture qui compte

```
Chromium MESURE          ->   Python DECIDE
lignes rendues,               fit(zone, mesure) ->
largeur de chaque ligne         Accepte | Reduire(echelle) | Abandonne
```

Chromium ne prend aucune décision : il compose le texte et rend des chiffres.
Toutes les règles sont dans `layout.py`, en Python pur. C'est là qu'est le
contrôle total demandé.

Le comptage de lignes passe par **`Range.getClientRects()`**, qui renvoie un
rectangle par ligne rendue — et donc aussi la largeur de chacune, dont on a
besoin pour les bandes du reel. Diviser la hauteur par l'interlignage serait
faux : un accent ou une descendante change la hauteur de boîte.

## 2. L'algorithme d'ajustement, en trois étages

**Étage 1 — Coulée à taille fixe.** La police garde la valeur de la maquette. Le
texte coule, le bloc grandit dans le sens de son ancrage : vers le haut pour le
post (ancré en bas), symétriquement pour la cover reel (ancrée au centre). C'est
le régime normal.

**Étage 2 — Plafond.** Deux composantes :

- *hauteur* : `Zone.max_lines`
- *largeur* : `Zone.max_line_width` — une bande de reel ne doit jamais toucher
  les bords ; le texte passe à la bande suivante avant.

**Étage 3 — Repli continu.** Seulement si le plafond est franchi : **dichotomie**
sur la taille de police (≈ 5 mesures) jusqu'à tenir, avec plancher `min_scale`.
Remplace les trois paliers actuels — un saut de 10 % se voit, et après 0.8 on
rendait quand même l'image débordée.

**Si même le plancher ne tient pas :** on ne produit pas une image cassée en
silence. `render_report.overflow = true`, le post est signalé en défaut dans le
dashboard.

Le repli sera rare, et c'est voulu : `compose.py` plafonne les accroches à
`HEADLINE_MAX = 110`. L'accroche de la maquette fait ~93 caractères sur 3 lignes,
soit ~31 caractères/ligne → 110 caractères font 4 lignes au pire, sous un plafond
de 5.

## 3. Le voile doit suivre le bloc

Conséquence directe du choix « bloc extensible », et non optionnelle.

Les trois dégradés du post ont aujourd'hui une géométrie figée (bande de 817 px à
partir de y=623). Une accroche de 5 lignes remonte hors du dégradé le plus dense :
sur une photo claire, le texte devient illisible. La maquette ne le révèle pas
parce que le bas de sa photo est déjà sombre.

Correctif : la hauteur du voile bas devient fonction de la hauteur mesurée du
bloc, injectée en variable CSS. Cela impose un **rendu en deux passes** — mesurer,
puis rendre avec le voile ajusté. Coût nul : la boucle enchaîne déjà plusieurs
`set_content`.

## 4. Le CSS devient un squelette sans valeurs

`style.css` ne contient plus de nombres de mise en page : il consomme des
variables (`var(--font-size)`, `var(--line-height)`, `var(--scrim-height)`…) que
Python injecte depuis le `Gabarit`.

Effet de bord utile : cela réduit la surface du bug corrigé le 2026-08-08 —
l'autoescape de Jinja transformait les guillemets de la feuille en `&#34;`, ce qui
invalidait `font-family: "KindaDisplay"` et `format("woff2")`. L'`@font-face`
n'était alors pas enregistré du tout et Chromium rendait l'accroche dans son serif
par défaut, avec d'autres césures — **sans aucune erreur**. Le correctif
(`build_document()` extrait + CSS passé en `Markup` + deux tests) est le
prérequis de ce chantier.

## 5. Les gabarits, valeurs relevées

### `kinda_post` — 1080×1440

Source : frame Figma `234:8`, déjà transcrite (voir `REFERENCE.md`).

| Paramètre | Valeur |
|---|---|
| Rôle | `headline` |
| Corps | 80 px |
| Interlignage | 1.15 (pas de 92 px) |
| Interlettrage | -0.06 em |
| Couleur | `#FFF7FA` |
| Bloc | left 77, right 77, ancré en bas, offset 76 |
| Surlignage | `rgba(253,139,195,.8)`, hauteur 0.9 em, position `0 55%`, padding `0 .0375em` |
| Plafond | 5 lignes (à confirmer par mesure), plancher 0.7 |
| Voile | 3 dégradés, le bas devient dynamique |

### `kinda_reel` — 1080×1920 (nouveau)

Source : `exemples/[PREVIEW] - Cover Reels.png`, mesuré au pixel.

| Mesure | Valeur relevée |
|---|---|
| Bande rose (titre) | x 263→796 · **largeur 534** · y 731→824 · **hauteur 94** · bords verticaux, **non inclinée** |
| Bande crème (sous-titre) | x 161→914 · **largeur 753** · y ≈831→910 · hauteur ≈ 85 · **inclinée ~0,6° antihoraire** |
| Centres | 529 et 537 pour une toile centrée à 540 → centrées, **chacune sur sa propre largeur** |
| Empilement | jointives (bas du rose 824, haut du crème 825) |
| Centre vertical du bloc | ≈ 823 / 1920 ≈ **42,9 %** |
| Voile | **aucun** — le contraste vient des bandes |
| Rose | `#FD8BC3` opaque |
| Crème | `#FFF7FA` (exactement la couleur du texte du post) |
| Texte titre | `#FFF7FA` sur rose |
| Texte sous-titre | `#1D1D1D` sur crème |

**Correction d'une erreur d'appréciation à l'œil, à ne pas reproduire dans le
code :** les deux textes ne sont *pas* de tailles très différentes. Les glyphes
mesurent 83 px du sommet de l'accent de `Grégoire` au bas du `q` (titre) contre
64 px de l'ascendante au pied (sous-titre) ; ramenés aux métriques d'une narrow
bold, les deux tombent dans les **mid-80 px**. Ce qui fait paraître le titre plus
gros, c'est sa bande plus haute (94 vs 85) et ses capitales larges (`G`, `C`) face
à un sous-titre plein de lettres étroites (`i`, `l`, `t`, `f`, `s`).

**Calibration requise à l'implémentation.** Les corps exacts, paddings de bande et
l'angle précis ne se déduisent pas par inversion des mesures : ils se calent en
rendant puis en comparant aux valeurs du tableau ci-dessus. C'est une tâche
explicite du plan, pas une valeur à deviner.

### Règle d'adaptation propre au reel

**Un titre trop long ne rétrécit pas : il passe sur une deuxième bande rose.**
C'est ce que produit `box-decoration-break: clone`, déjà en place sur le post.
L'empilement de bandes *est* le langage visuel du gabarit. Maximum 2 bandes par
rôle ; au-delà, le repli par dichotomie s'applique.

Plafond de largeur proposé : **926 px** (les 77 px de marge du post, pour une
cohérence entre gabarits). La plus longue bande de la maquette fait 753 px.

## 6. Modèle de données

**Les rôles remplacent l'accroche unique.** Une slide est aujourd'hui
`{"headline": ..., "image_url": ...}`. Le `Gabarit` déclare ses rôles et les clés
de la slide doivent correspondre :

- `kinda_post` → `headline`
- `kinda_reel` → `title`, `subtitle`

`Post.template` existe déjà en base et porte le nom du gabarit : rien à changer
pour ça.

**Ajout : colonne `render_report`** (JSONB, nullable) sur `posts`. Par slide : le
nombre de lignes, l'échelle retenue, un drapeau de débordement. C'est ce qui
permet au dashboard de signaler un post en défaut au lieu de laisser publier une
image coupée. Une migration, une colonne.

## 7. Gestion d'erreur

| Défaillance | Comportement |
|---|---|
| Photo introuvable | repli sur `fallback.jpg` — déjà en place |
| Police non enregistrée | verrouillé par le test anti-échappement |
| Débordement même au plancher | `render_report.overflow = true`, post signalé, **jamais publié en silence** |
| Chromium absent | erreur explicite à l'entrée de `render_post`, pas une trace au fond de Playwright |

## 8. Tests

- **`layout.py` — unitaires purs, sans Chromium.** Les règles deviennent des
  assertions : 6 lignes déclenchent une réduction, une bande de 950 px passe à la
  ligne, le plancher abandonne proprement.
- **Géométrie — avec Chromium, ignorés si absent.** On rend les textes exacts des
  maquettes et on vérifie le retour sur les valeurs mesurées : 3 lignes pour
  l'accroche du post ; bande rose 534 px, bande crème 753 px, empilement jointif
  pour le reel. Tolérance de quelques pixels.
- **Anti-échappement CSS** — le test existant, conservé.
- **Comparaison visuelle manuelle** — les deux PNG d'`exemples/` deviennent les
  références documentées dans `REFERENCE.md`.

Un diff pixel automatique a été écarté : il exigerait la photo exacte des
maquettes, qu'on n'a pas. Un test qui ne prouve rien est pire que pas de test.

## 9. Hors périmètre (explicitement)

- Composition automatique des titres/sous-titres de cover reel par Claude
- Sélection, téléchargement et vérification de format des vidéos
- File quotidienne, calendrier, feedback validé/refusé
- Modèle local, auto-gestion des sources
- Socle launchd (chantier 0)

Le renderer de cover reel est livré **complet et testé**, mais alimenté par un
titre et un sous-titre fournis.

## Réserve à lever

Une réserve juridique, notée ici pour ne pas la perdre : télécharger la vidéo d'un
tiers pour la republier sur le compte Kinda est une question de droits et de ToS,
pas de technique. Le système peut repérer et présenter des candidats (lien,
format, engagement) ; l'automatisation jusqu'à la republication est une décision
de l'utilisateur, hors de ce chantier.
