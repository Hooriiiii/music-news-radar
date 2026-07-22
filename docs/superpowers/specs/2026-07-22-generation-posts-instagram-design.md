# Génération de posts Instagram à partir des articles scorés

Date : 2026-07-22
Statut : conception validée, prête pour le plan d'implémentation

## Pourquoi

Kinda s'arrête aujourd'hui à un flux d'articles scorés. C'est un outil de veille et
de détection ; ce n'est pas encore un média. La brique décrite ici franchit cet
écart : transformer un article scoré en un **post carrousel Instagram prêt à
publier** — visuels aux formats du template Figma de Kinda, plus la légende.

C'est la brique n°2 du projet (« le compte d'actu »), celle dont dépendent la
distribution des plugins et, plus tard, le label.

## Périmètre

Dans le périmètre :

- un générateur éditorial qui découpe un article en slides de carrousel + légende ;
- un moteur de rendu qui produit les PNG à partir du template Figma extrait ;
- une file éditoriale (brouillon → validé → publié) dans l'API et le dashboard ;
- l'export d'un post prêt à publier.

Hors périmètre, explicitement :

- **les reels** (assemblage vidéo des clips UGC) — dépend de cette brique et
  constitue un pipeline ffmpeg séparé, à spécifier après ;
- **la publication automatique** vers Instagram (API Graph, compte pro, revue
  Meta) — on exporte, l'utilisateur publie ;
- **la génération d'images par IA** — les visuels viennent de l'article ou de
  l'utilisateur.

## Décisions structurantes

### Figma ne peut pas être le moteur de rendu

L'API REST Figma est en lecture seule sur le contenu d'un fichier : on peut lire un
document et exporter un node en PNG, mais on ne peut pas écrire un texte dans un
calque. L'écriture passe uniquement par l'API Plugin, qui s'exécute dans l'éditeur
Figma ouvert — donc jamais depuis un processus automatisé.

Conséquence retenue : **le template Figma est extrait une fois en HTML/CSS**,
versionné dans le repo, et rendu à l'exécution par un Chromium headless. Figma
reste la source de vérité du design ; une refonte du template déclenche une
ré-extraction, qui est un geste de design et non un geste de runtime.

Alternatives écartées : un plugin Figma semi-manuel (fidélité parfaite mais un
aller-retour manuel par post, et ferme la porte à toute automatisation), et un
rendu programmatique en Pillow/SVG (reproduire à la main dégradés, masques et
auto-layout diverge vite du design).

### Génération à la demande, jamais par le cron

Un post n'est créé que par un clic dans le dashboard. Aucun appel Claude ni aucun
rendu ne part du pipeline planifié. Cela borne le coût, évite une file de
brouillons non lus, et surtout maintient Chromium hors de GitHub Actions.

### Un post n'est pas une colonne d'article

Un article peut donner plusieurs tentatives de post, et les articles sont purgés à
14 jours alors qu'un post publié est une archive permanente. D'où une table
dédiée, sur le motif déjà établi par `artist_mentions`.

## Architecture

Trois unités indépendantes, chacune testable seule :

| Unité | Rôle | Dépend de |
|---|---|---|
| `app/content/compose.py` | Article → JSON du post (slides + légende) | Claude, modèles Pydantic |
| `app/content/render.py` | JSON du post + template → PNG | Jinja2, Playwright, assets |
| `app/api/posts.py` | File éditoriale, édition, export | base, les deux ci-dessus |

Le moteur de rendu ne connaît ni la base ni Claude : il reçoit un dict de slides et
un nom de template. Le générateur ne connaît pas le rendu. C'est ce qui permet de
tester chacun sans lancer l'autre.

## Modèle de données

Nouvelle table `posts` (migration `0008_add_posts.py`) :

| Colonne | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `article_id` | FK `articles.id` `ON DELETE SET NULL`, nullable, index | la purge casse le lien, jamais le post |
| `article_title` | Text | dénormalisé — survit à la purge |
| `article_url` | String(2000) | dénormalisé |
| `source_name` | String(200), nullable | dénormalisé |
| `status` | VARCHAR(20) + CHECK, index | `draft` / `approved` / `published`, défaut `draft` |
| `template` | String(100) | nom du dossier de gabarit |
| `caption` | Text | légende Instagram, en français |
| `hashtags` | JSONVariant | liste de chaînes |
| `slides` | JSONVariant | liste ordonnée, voir schéma ci-dessous |
| `created_at` | timestamptz | `server_default=func.now()` |
| `published_at` | timestamptz, nullable | posé au passage en `published` |

Conventions respectées : enum en VARCHAR + CHECK (`native_enum=False`,
`values_callable`), JSONB via `JSONVariant`, contraintes CHECK entourées de
`op.f()` dans la migration.

**Aucune colonne de fichier image.** Les PNG sont rendus à la volée. Le rendu est
déterministe et prend environ une seconde par slide ; le stockage n'achèterait rien
et coûterait du nettoyage, du versionnage d'assets et à terme un bucket. La vérité
du post est son JSON.

**Rétention :** les posts ne sont jamais purgés. Le passage en `published` bascule
aussi l'article en `USED`, ce qui le protège par la règle de rétention existante.

## Le générateur éditorial

`app/content/compose.py`, calqué sur `app/pipeline/scoring.py` : structured output
via `messages.parse()`, modèle configurable dans `.env`.

Schéma de sortie (Pydantic), qui est le contrat central :

```python
class Slide(BaseModel):
    # Accroche, avec balisage **...** pour les segments surlignés en rose
    headline: str = Field(max_length=110)
    image_url: str | None = None

class PostDraft(BaseModel):
    slides: list[Slide] = Field(min_length=2, max_length=6)
    caption: str = Field(max_length=2000)
    hashtags: list[str] = Field(max_length=15)
```

Le schéma est volontairement plat : le template ne comporte **qu'une seule zone de
texte par slide**, donc pas de corps de texte, et toutes les slides partagent le
même gabarit, donc pas de type de slide. Ajouter ces champs reviendrait à modéliser
une mise en page qui n'existe pas.

Le **balisage `**...**`** dans `headline` est le seul élément de structure. Il
transcrit la signature visuelle de Kinda : certains segments de l'accroche sont
surlignés en rose. Un validateur vérifie que les marqueurs sont appariés et qu'au
moins un segment est surligné (sinon la slide perd son identité). Le prompt
explique quoi mettre en avant : le nom d'artiste, le lieu, la date — l'information
qui accroche.

Les bornes vivent dans le schéma, pas dans le prompt. Si le modèle produit huit
slides ou une accroche trop longue, la validation échoue et l'appel est retenté —
c'est ce qui empêche la dérive sur les brèves, où un modèle non contraint invente
du contenu pour remplir des slides.

La borne de 110 caractères est calibrée sur l'accroche de référence du template
(« Nico Moreno, Mosimann, Cassius, le collectif PACT… et c'est ce week-end aux
Vieilles Charrues », 93 caractères sur trois lignes). Elle est approximative par
nature : la vraie contrainte est le débordement visuel, traité côté rendu.

Le prompt porte la ligne éditoriale de Kinda (FR national, cœur électro et
festivals, ton du compte), écrite au même endroit et sous la même forme que le
prompt de scoring, pour qu'il n'existe qu'une seule définition de l'identité
éditoriale.

Longueurs indiquées ci-dessus : provisoires, à caler sur les zones de texte réelles
du template (voir « Questions ouvertes »).

## Le rendu

### Le template, tel qu'il est

Fichier `kAqCOUyZgvR025RuICJuPW` (« Kinda design »), frame `234:8` nommée
`TEMPLATE`. Format **1080×1440** (3:4), pas 1080×1350.

De l'arrière vers l'avant : une photo en plein cadre, un dégradé sombre couvrant le
bas pour la lisibilité, le logo `KINDA news` en haut à droite, et l'accroche en bas.
Le calque `Guideline` (masqué) fixe les marges : 77 px sur les côtés, 85 px en haut
et en bas.

Le fichier contient aussi un template Reels, qui sera l'entrée de la brique vidéo —
hors périmètre ici.

**Le surlignage rose.** Dans Figma, ce sont des rectangles positionnés à la main
derrière des segments de texte. En HTML c'est un `<span>` avec fond, `padding` et
`box-decoration-break: clone` pour que le fond suive correctement le retour à la
ligne. Le rendu HTML est ici **plus robuste que l'original** : le surlignage suit le
texte quand celui-ci se réajuste, alors que dans Figma tout changement de longueur
d'accroche oblige à repositionner les rectangles.

Aucune variable Figma n'est définie dans le fichier : les couleurs, la typo et le
dégradé sont en dur et seront relevés à l'extraction.

### Extraction du template Figma

Geste ponctuel, effectué une fois par template puis à chaque refonte du design :

1. lire le fichier Figma via les outils Figma disponibles ;
2. en tirer un `style.css` et le gabarit Jinja unique de la slide ;
3. **boucler sur un diff visuel** : exporter la frame de référence en PNG depuis
   Figma, rendre la même avec des textes identiques, comparer, corriger jusqu'à
   convergence.

L'étape 3 n'est pas optionnelle. Sans elle, l'extraction produit un résultat
« presque juste » qui se voit à l'œil sur un feed.

Arborescence :

```
app/content/
  compose.py
  render.py
  templates/<nom>/
    slide.html.j2
    style.css
  assets/
    fonts/*.woff2
    <fonds exportés depuis Figma>.png
```

### Moteur

Playwright en API synchrone (cohérent avec le reste du projet, qui n'est pas
async), un Chromium lancé une fois pour toutes les slides d'un post, viewport
1080×1440, une capture par slide. Un seul gabarit sert toutes les slides.

Trois pièges traités par construction :

**Le débordement du texte.** L'accroche est de longueur variable et la borne de
caractères ne la garantit qu'approximativement — une accroche courte avec des mots
longs peut déborder. Après le rendu, on mesure la hauteur réelle du bloc de texte
via `page.evaluate()` ; si elle dépasse la zone allouée, on réduit la taille de
police d'un cran et on refait la capture, dans une boucle bornée à trois essais.
C'est peu de code et cela supprime la classe entière des posts illisibles.

**Les fontes** sont embarquées en `.woff2` dans `assets/fonts/` et déclarées en
`@font-face`. Se reposer sur les fontes système fait diverger le rendu
silencieusement d'une machine à l'autre — cela se manifeste par des césures et des
débordements, pas par une erreur.

**Les images externes** sont téléchargées, redimensionnées et inlinées en base64
dans le HTML avant la capture. Une image chargée par URL rend la capture non
déterministe : elle part parfois avant la fin du chargement.

Chromium tourne uniquement en local, dans l'application FastAPI. Le pipeline
planifié ne génère aucun post.

## API et dashboard

Nouveau routeur `app/api/posts.py`, aux conventions des routeurs existants :

| Endpoint | Rôle |
|---|---|
| `POST /articles/{id}/posts` | crée un brouillon (un appel Claude), renvoie le post |
| `GET /posts` | file éditoriale, filtrable par `status` |
| `GET /posts/{id}` | détail |
| `PATCH /posts/{id}` | édite `caption`, `hashtags`, `slides`, `status` |
| `GET /posts/{id}/slides/{n}.png` | rend une slide à la volée |
| `GET /posts/{id}/export.zip` | toutes les slides + `caption.txt` |

Le passage en `published` via `PATCH` pose `published_at` et bascule l'article lié
en `USED`.

Dashboard (`app/static/dashboard.html`, page existante) : un bouton « générer le
post » sur chaque article, et un écran post affichant les slides en aperçu avec les
textes éditables en regard, le remplacement d'image et l'export.

Le remplacement d'image se fait en éditant `image_url` sur la slide via `PATCH` —
pas d'upload de fichier dans cette version. Une URL d'image collée par
l'utilisateur suffit et évite d'introduire un stockage de fichiers.

## Tests

Sur SQLite in-memory, fixtures de `tests/conftest.py`, comme le reste du projet.

- **Composition** (Claude mocké) : les bornes 2-6 slides sont imposées ; une
  accroche trop longue est rejetée ; des marqueurs `**` non appariés sont rejetés ;
  une accroche sans aucun segment surligné est rejetée ; le post créé dénormalise
  bien titre, url et source.
- **Rendu** (`skipif` si Chromium absent, pour ne pas casser la CI) : un post
  fixture produit N images non vides aux dimensions 1080×1440 ; une accroche à la
  longueur maximale autorisée ne déborde pas de sa zone (test de la boucle de
  réduction de police).
- **API** : création, listing filtré par statut, édition, passage en `published` qui
  bascule l'article en `USED`.
- **Rétention** : purger un article laisse son post intact avec `article_id` à
  `NULL` et les champs dénormalisés remplis. C'est le test qui garantit la valeur
  d'archive de la table.

## Dépendances et outillage

- `playwright` ajouté aux dépendances du projet ;
- `uv run playwright install chromium` documenté dans le README et `CLAUDE.md` ;
- la CI n'installe pas Chromium — les tests de rendu s'y skippent.

## Questions ouvertes

1. **Licence de la fonte** — l'embarquement en `.woff2` dans le repo suppose que la
   licence de la fonte du template l'autorise. À vérifier s'il s'agit d'une fonte
   commerciale. Sans quoi il faudra soit une fonte libre approchante, soit un
   rendu du texte en image depuis un poste disposant de la licence.
2. **Droits des photos** — la reprise automatique de l'image d'un article place le
   post en zone grise. Le crédit de la source dans la légende est un garde-fou
   d'usage, pas une autorisation ; le remplacement manuel de l'image existe pour
   les cas où la photo d'origine ne peut pas être reprise.
