# Covers en 4 variantes, médias typés, POST ou REELS — design

Date : 2026-08-09
Chantier : **B'** — remplace et précise ce que la décomposition du 2026-08-08 appelait
« B. File quotidienne + feedback »
État : validé avec l'utilisateur, prêt pour le plan d'implémentation

## Ce que l'utilisateur demande, dans ses mots

« À partir d'une actualité sélectionnée, je veux deux formats : si l'actualité contient
uniquement des images → POST uniquement ; si elle contient une vidéo exploitable →
possibilité de choisir entre POST et REELS via un switch. Dans les deux cas, la
première image est une COVER générée avec une image de fond, le logo/branding et un
titre accrocheur. Le système doit proposer 4 variantes de titres/covers et
l'utilisateur en sélectionne une. »

Et : « Je peux changer l'URL de l'image, ce qui me permet d'aller chercher des images
sur le web si la qualité de l'image de base n'est pas bonne. »

## Le changement de nature, et c'est le cœur du chantier

Le chantier A produisait un **carrousel de slides de texte** : 2 à 6 accroches, chacune
rendue dans le gabarit, sur la même photo. Ce chantier remplace ce modèle par :

```
slide 1  = une COVER generee (image de fond + logo + titre accrocheur)
slide 2+ = les MEDIAS REELS de l'actualite (photos, videos), non rendus
```

Ce n'est pas un ajustement du chantier A : c'est un autre objet éditorial. Le renderer
et l'export doivent apprendre à distinguer ce qu'ils **rendent** de ce qu'ils **laissent
passer**.

## Ce que l'exploration a établi

**1. Changer l'URL de l'image marche déjà.** L'éditeur de carrousel du chantier A a un
champ par slide. Ce n'est pas à construire, seulement à recâbler sur la cover.

**2. Un défaut existant à corriger au passage.** `create_post` ne met l'image de
l'article que sur la slide 1 ; les suivantes reçoivent `None`, donc l'image de secours
sombre. Avec le nouveau modèle la question disparaît — les slides suivantes sont des
médias, pas des rendus.

**3. La détection vidéo est impossible en l'état.** `articles.media_urls` est une liste
de chaînes. L'adapter X demande `media.fields=url,preview_image_url` : l'API v2 ne
renvoie `url` que pour les photos, et pour une vidéo elle renvoie une **vignette**. Le
champ `type` existe mais n'est pas stocké, et `variants` — qui porte les URL MP4
lisibles — n'est pas demandé. **Un tweet vidéo est donc aujourd'hui indistinguable d'un
tweet photo.**

**4. Seul X peut fournir une vidéo exploitable.** Côté RSS, `_media_urls` filtre les
pièces jointes sur `image/`, et un flux RSS ne donne presque jamais un fichier vidéo,
juste un lien vers une page. Les sources RSS resteront donc en POST — ce n'est pas une
limitation du code mais de ce que les flux publient.

**5. Les deux gabarits n'ont pas les mêmes zones de texte.** `kinda_post` a un rôle
(`headline`), `kinda_reel` en a deux (`title` + `subtitle`). Une cover n'est pas neutre
au format : la bascule ne peut pas réutiliser le même texte tel quel.

**6. Le logo n'a pas besoin d'être refourni.** `app/content/assets/logo.png` fait
600×299 et s'affiche à 150 px : quatre fois la densité nécessaire. Il est inliné en
base64 au rendu, donc sans dépendance externe. Un SVG ne servirait qu'à un futur
gabarit qui l'afficherait beaucoup plus grand.

## Décisions prises avec l'utilisateur

| Décision | Choix retenu | Motif |
|---|---|---|
| Ce qui varie entre les 4 variantes | **Le titre seul**, fond constant | On compare des formulations à fond constant ; l'image se remplace une fois, pas quatre. |
| Détection vidéo | **Type stocké à l'ingestion** | Sans elle, le chantier reels devrait reconstruire la donnée sur des articles déjà purgés. |
| Contenu de l'export | **Cover PNG + médias téléchargés** | Le plus direct pour l'usage du soir : déposer le zip dans Instagram. |
| Bascule POST ↔ REELS | **Chaque variante porte les deux textes** | Un seul appel Claude ; la bascule est instantanée et conserve la sélection. |
| Variantes non retenues | **Conservées**, avec l'indice de la retenue | Le choix entre quatre formulations est du signal gratuit pour le modèle local (chantier C). |
| Brouillons abandonnés | **Supprimables d'un clic**, eux seuls | La règle « jamais purgé » protège l'archive éditoriale — un brouillon jamais retenu n'en fait pas partie. |
| Légende et hashtags | **Communs aux quatre** | Les variantes sont des formulations, pas des angles : une légende convient, et elle reste éditable. |

---

# Le design

## 1. Les médias deviennent typés

`articles.media_urls: list[str]` est remplacée par :

```python
media: list[dict] | None
# [{"type": "video", "url": "https://.../vid.mp4",
#   "preview_url": "https://.../thumb.jpg",
#   "duration_ms": 31000, "width": 1280, "height": 720}]
```

`type` ∈ {`photo`, `video`, `gif`}. L'adapter X demandera
`media.fields=type,url,preview_image_url,variants,duration_ms,width,height`, et prendra
dans `variants` le MP4 de plus haut débit sous un plafond.

**`width` et `height` ne sont pas cosmétiques.** Un reel fait 9:16 et une vidéo X est
souvent en 16:9 : savoir avant de publier qu'elle sera recadrée évite de le découvrir
dans Instagram. Les dimensions sont affichées dans le sélecteur de médias.

**La migration est indolore parce que les articles sont purgés à 14 jours.** Les lignes
existantes deviennent `{"type": "photo", "url": ...}` ; le stock disparaît de lui-même
en deux semaines. Une vignette de vidéo ancienne sera classée photo à tort — sans
conséquence, le pire cas étant qu'on ne propose pas REELS sur un article déjà périmé.

### « Vidéo exploitable », définition retenue

Un média tel que :
- `type == "video"` ;
- une **URL MP4 directe** est disponible (sans elle — cas d'un RSS qui ne donne qu'un
  lien de page — la vidéo n'est pas exploitable) ;
- la durée tient sous la limite Instagram : **90 s** pour un reel, **60 s** pour une
  slide de carrousel.

Le switch REELS n'apparaît que si au moins un média satisfait ces trois conditions.

## 2. Un post décrit ce qui sera publié

`posts.slides` devient une liste ordonnée où chaque entrée déclare sa nature :

```python
slides: [
  {"kind": "cover", "headline": "...", "title": "...", "subtitle": "...",
   "image_url": "..."},
  {"kind": "media", "type": "video", "url": "...", "width": 1280, "height": 720},
]
```

La cover porte **les trois textes** — l'accroche du post et le couple titre/sous-titre
du reel. C'est ce qui rend la bascule instantanée : le rendu prend le champ
correspondant au gabarit, sans rien redemander à Claude.

`render_post` ne rend que les entrées `kind: "cover"` ; l'export laisse passer les
`kind: "media"`. Une seule colonne, et **pas de nouveau champ de format** :
`posts.template` fait déjà ce travail (`kinda_post` / `kinda_reel`) et l'API l'expose.

Nouvelle colonne `variants: list[dict] | None` — les quatre propositions et l'indice de
la retenue (`null` tant qu'aucune n'est choisie).

## 3. Le déroulé, du clic au zip

**Un seul appel Claude** rend les quatre variantes (accroche + titre + sous-titre
chacune), la légende et les hashtags. Puis **un seul Chromium** rend les quatre covers
d'affilée.

Ce point conditionne l'expérience du soir : le lancement du navigateur coûte, la capture
non. Quatre covers partageant le même fond = **une** récupération d'image et **un**
démarrage de navigateur. Les quatre covers sont donc présentées **rendues**, pas en
texte : une cover se juge à l'œil, et tout le chantier A visait à ce que le rendu soit
exact — un aperçu HTML aurait d'autres métriques de police et mentirait.

L'interface donne :
- les **4 covers** rendues, une à retenir ;
- un **switch POST / REELS**, REELS désactivé sans vidéo exploitable **avec la raison
  affichée** (« pas de vidéo », « durée 4 min > 90 s », « pas d'URL directe ») — un
  bouton grisé sans explication est une énigme ;
- le **champ URL de l'image de fond**, prérempli avec le média de l'actu ; le changer
  re-rend les quatre covers ;
- la **liste des médias** à cocher pour les slides suivantes, avec type et dimensions.

Le post est créé en base **dès la génération**, statut `DRAFT`, `variants` rempli et
retenue à `null` — c'est ce qui permet d'y revenir sans repayer un appel. Un brouillon
sans variante retenue est supprimable d'un clic ; un post publié ne l'est jamais.

## 4. L'export

```
01_cover.png          la cover rendue, au format du gabarit choisi
02_photo.jpg          les medias coches, numerotes dans l'ordre des slides
03_video.mp4
caption.txt           legende + hashtags + source
medias_manquants.txt  present seulement si un telechargement a echoue
```

**Un média qui ne se télécharge pas ne fait pas échouer le zip** : il part dans
`medias_manquants.txt` avec son URL et la raison. Même philosophie que l'image de
secours du chantier A — un export partiel vaut mieux qu'une erreur, surtout à 23 h.

Deux garde-fous : un **plafond de taille** par média et un **délai borné**. Une vidéo de
90 s pèse 10 à 30 Mo, raisonnable dans un zip ; une source qui renvoie 2 Go ne doit pas
bloquer l'export.

Pour un REEL, le zip contient la cover **et** la vidéo : Instagram définit la couverture
d'un reel séparément de la vidéo, les deux fichiers sont donc nécessaires.

## 5. Hors périmètre

- La **file quotidienne** de 5 posts générés d'office : ce chantier rend la génération
  déclenchée par un clic, ce qui est ce que l'utilisateur a demandé en dernier. La
  génération automatique en lot reste ouverte.
- Le **feedback écrit** validé/refusé avec motif : le choix parmi quatre variantes en
  est une forme implicite et gratuite. Le motif explicite reste au chantier C.
- Le **modèle local** (chantier C) et l'**auto-gestion des sources** (chantier D).
- Le **montage vidéo** : la vidéo est reprise telle quelle, jamais recadrée ni
  transcodée. Les dimensions sont affichées pour que l'utilisateur sache à quoi
  s'attendre.

## Réserve à lever, notée une fois

Utiliser la vidéo ou les photos d'un tiers comme contenu d'un post Kinda est de la
**republication** : une question de droits et de conditions d'utilisation, pas de
technique. Le système présente les médias et produit le zip ; la décision de publier
appartient à l'utilisateur, à qui les sources sont affichées.

## Dette connexe découverte pendant ce chantier

Deux trous mis au jour par l'incident `render_report` (colonne absente en base alors que
le modèle la déclarait, d'où un 500 sur `/posts`) :

1. **Rien ne vérifie la parité du schéma au démarrage.** L'agent uvicorn tourne en
   permanence ; si modèle et base divergent, il sert des 500 indéfiniment sans que rien
   ne le signale. Un contrôle au démarrage — comparer la révision courante à la tête —
   transformerait des heures de 500 silencieux en un message immédiat.
2. **La CI ne peut pas l'attraper.** `ci.yml` ne fait que `ruff` et `pytest` ;
   `alembic check` exige une base. Un service Postgres jetable dans le workflow, un
   `alembic upgrade head` puis `alembic check` auraient bloqué le merge.

Le second attrape la classe entière de défauts. Ce chantier ajoute **deux** migrations
(médias typés, `variants`), donc le risque se répète — à traiter avant ou avec lui.
