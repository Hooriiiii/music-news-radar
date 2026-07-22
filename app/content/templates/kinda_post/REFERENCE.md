# Template `kinda_post`

Extrait du fichier Figma `kAqCOUyZgvR025RuICJuPW` (« Kinda design »),
frame `234:8` (« TEMPLATE »), le 2026-07-22. Format 1080x1440.

Figma reste la source de verite du design ; ce dossier en est la transcription.
Apres toute refonte du template dans Figma, refaire l'extraction : lire la frame,
mettre a jour `style.css`, puis reboucler sur le diff visuel contre un export PNG
de la frame. Le gabarit ne doit JAMAIS etre modifie a l'aveugle.

Le contrat de variables du gabarit (`css`, `image_data_uri`, `logo_data_uri`,
`headline_html`, `headline_scale`) et l'element `id="headline"` sont consommes par
`app/content/render.py` : les changer casse le rendu.

Les rectangles roses de Figma ne sont PAS reproduits en dur : ils deviennent des
`<mark>` qui suivent le texte automatiquement.

## Pourquoi une transcription et pas un rendu Figma

L'API REST de Figma est en LECTURE SEULE sur le contenu d'un fichier : on peut lire
un document et exporter un node en PNG, on ne peut pas ecrire un texte dans un
calque. L'ecriture passe uniquement par l'API Plugin, qui s'execute dans l'editeur
ouvert. Figma ne peut donc pas rendre a l'execution : le template est extrait UNE
FOIS en HTML/CSS versionne, puis rendu par un Chromium headless.

## Valeurs relevees dans Figma

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

Le bloc d'accroche est ancre par le BAS (`bottom: 76px`) et non par le haut : une
accroche plus longue, ou un `headline_scale` plus petit, doit pousser vers le haut
sans jamais deborder sous la ligne de garde des 85 px.

## Police : substitut a valider

Le fichier Figma utilise **Arial Narrow Bold**, une police commerciale (Monotype)
qu'on ne peut ni redistribuer dans ce depot ni embarquer en base64.

`style.css` embarque a la place **Liberation Sans Narrow Bold** (Red Hat, GPLv2 avec
exception d'embarquement de police), qui est *metriquement compatible* avec Arial
Narrow : memes chasses, donc **memes retours a la ligne**. C'est la propriete qui
compte ici — une police simplement « ressemblante » ferait tomber l'accroche de
reference sur quatre lignes au lieu de trois.

**A faire valider par le proprietaire du design.** Verifie sur l'accroche de
reference : trois lignes identiques, memes coupes, positions des lignes a 1 px pres.
Les dessins de glyphes different legerement d'Arial Narrow (largeur de mot a ~2 %
pres sur certains mots), sans effet visible a l'oeil.

Si le proprietaire preferait une autre police, il faut refaire la boucle de diff
visuel : la taille, l'interlignage et l'interlettrage sont calibres sur celle-ci.

## Details d'implementation qui ne se devinent pas

- **Surlignage** : le fond du `<mark>` passe par un `linear-gradient` plat plutot
  que par `background-color`. La hauteur du fond d'un element en ligne est imposee
  par les metriques de la police (~1.15 em), alors que le rectangle de Figma fait
  72 px pour 80 px de corps (0.9 em) ; `background-size` fixe cette hauteur exacte
  et `background-position` la cale comme dans Figma.
- **`box-decoration-break: clone`** fait que le fond se redessine sur CHAQUE ligne
  quand le segment surligne passe a la ligne. Sans lui, le fond est etire en un
  seul bloc a travers le retour a la ligne.
- **Les trois voiles** sont trois couches `background-image` d'un seul `#scrim`,
  dans l'ordre de la pile Figma (le voile noir bas est au-dessus du rose).
- Le `padding` horizontal du `<mark>` decale de 3 px le texte des lignes qui
  commencent par un surlignage (ligne 3 de l'accroche de reference). Les
  rectangles de Figma sont poses a la main ; l'ecart est imperceptible.

## Assets

- `app/content/assets/logo.png` — export SVG du node `234:13` rasterise a 4x
  (600 x 299, fond transparent). Le PNG rendu directement par Figma embarquait le
  fond blanc de la page : ne pas le reprendre tel quel.
- `app/content/assets/fallback.jpg` — aplat sombre 1080x1440 aux couleurs de Kinda,
  utilise quand la photo de l'article est introuvable. Volontairement pauvre : il ne
  doit jamais etre joli au point qu'on oublie de le remplacer.
