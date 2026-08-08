# Socle local : le Mac comme second ordonnanceur — design

Date : 2026-08-09
Chantier : **0** (voir la décomposition dans `2026-08-08-rendu-adaptatif-gabarits-design.md`)
État : validé avec l'utilisateur, prêt pour le plan d'implémentation

## L'objectif, dans les mots de l'utilisateur

« Rentrer chez moi le soir, regarder le dashboard et directement poster les posts
générés. » Et : « faire en sorte de lancer automatiquement tout ça quand mon Mac
s'allume, et le faire tourner en continu. »

## Ce que l'exploration a trouvé, et qui change le chantier

L'état réel de la machine ne correspondait pas à ce que la documentation décrit.
Ces quatre constats sont la vraie matière du chantier ; sans eux on aurait installé
un socle par-dessus un socle.

**1. Deux agents launchd tournent depuis le 5 juillet, jamais désinstallés.**
`com.musicnewsradar.pipeline` exécute `scripts.run_pipeline` toutes les 30 minutes
— donc ingestion, scoring Haiku, alertes Discord **et** purge de rétention — et
GitHub Actions a été ajouté par-dessus en juillet. L'hybride décidé pour ce
chantier est donc **déjà l'état de fait** ; il est simplement mal cloisonné, le Mac
en faisant trop.

**2. Les logs pèsent 43 Mo** (`pipeline.err.log` 34 Mo, `pipeline.log` 8,4 Mo),
sans rotation depuis cinq semaines. Le problème n'est pas à venir, il est là — et
un uvicorn permanent va s'y ajouter.

**3. L'agent `digest` échoue tous les jours depuis juillet**, et pas à cause du
mail : `failed to resolve host … .neon.tech`. Il se déclenche à 8h30, souvent juste
après le réveil, avant que le réseau soit debout. C'est un piège launchd, et il
concerne directement l'agent d'ingestion, qui aura `RunAtLoad` et partira lui aussi
au réveil.

**4. `CLAUDE.md` affirme faux.** Il écrit que « `run_ingest.py` déclenche aussi les
alertes hot au moment de l'ingestion ». Le code ne le fait pas : `run_ingest` est de
l'ingestion pure. Tout le cloisonnement de ce chantier repose sur ce fait — la
documentation doit cesser de le contredire.

## Décisions prises avec l'utilisateur

| Décision | Choix retenu | Motif |
|---|---|---|
| Alertes Discord temps réel | **GitHub Actions conservé** pour le pipeline | Elles partent du pipeline. Un runner cloud ne dort jamais ; un portable fermé fait rattraper le job au réveil, et l'alerte arrive le soir — elle perd sa raison d'être. |
| Rôle de l'agent local | **Ingestion seule**, toutes les 30 min | L'IP résidentielle débloque Mixmag (403 Cloudflare) et Reddit (429), que l'IP datacenter d'Azure fait bloquer par intermittence. Bénéfice de couverture, pas seulement de contrôle. |
| Scoring | **Reste au cloud** (Haiku) | Si le Mac scorait aussi, il faudrait arbitrer qui score quoi — et le chantier C se retrouverait sans rien à trier, Haiku ayant déjà tout fait. Le modèle local gardera un axe séparé : re-classement à la demande. |
| Dashboard | **Agent launchd permanent** | C'est ce qui fait exister l'expérience visée : l'interface déjà debout quand on rentre. |
| Digest | **Agent supprimé** | L'utilisateur ne veut pas de mail, et l'agent échoue quotidiennement depuis juillet. Le script reste utilisable à la main. |
| LM Studio | **Pas au démarrage** | Un modèle 7B chargé immobilise ~5 Go de RAM en permanence, sans aucun consommateur avant le chantier C. On le câblera là, avec son repli si le serveur est absent. |
| `ci.yml` | **Conservé** | Il ne fait tourner que les tests à chaque push. Ce n'est pas un ordonnanceur. |
| Sources X en 402 | **Laissées actives** | L'utilisateur va activer la facturation sur developer.x.com. La rotation des logs absorbe le bruit en attendant. |
| Installation | **Python, pas bash** | `plistlib` produit des plists valides par construction et testables sans appeler `launchctl`. Supprime aussi les chemins en dur versionnés. |

### Une décision antérieure annulée

La spec du 2026-08-08 acte « **GitHub Actions coupé** ». Le choix sur les alertes
l'annule : le cron cloud reste. Cette spec-là doit être corrigée, elle ne peut pas
rester en contradiction avec l'architecture réelle.

---

# Le design

## 1. Deux ordonnanceurs, cloisonnés par script

```
CLOUD — GitHub Actions, 2 h + rattrapage 30 min        INCHANGÉ
  scripts.run_pipeline
  ingestion + scoring Haiku + alertes Discord + purge de retention
  Raison d'etre : il ne dort jamais. C'est le canal temps reel.

MAC — launchd
  com.musicnewsradar.ingest       scripts.run_ingest, 30 min, RunAtLoad
  ingestion SEULE : ni scoring, ni alertes, ni purge
  Raison d'etre : IP residentielle -> Mixmag et Reddit passent

  com.musicnewsradar.dashboard    uvicorn, permanent, KeepAlive
  l'interface debout quand on rentre

  SUPPRIMÉS : com.musicnewsradar.pipeline, com.musicnewsradar.digest
```

Le cloisonnement ne tient qu'à un fait, à vérifier avant tout le reste :
**`run_ingest` ne fait que de l'ingestion**. Aucune alerte en double, aucun Haiku
payé deux fois. La dédup par hash (`url_norm` + titre) absorbe les articles vus des
deux côtés ; c'est précisément ce pour quoi elle existe.

Le `since_id` de l'API X vit dans `sources.state`, donc en base, donc partagé : si
le cloud lit les tweets en premier, le Mac n'en repaye aucun, et inversement.

## 2. L'agent dashboard

Commande : `uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --log-level warning`

Quatre choix, chacun pour une raison :

- **Pas de `--reload`.** Le rechargeur lance un processus enfant que launchd ne
  suit pas, et surveille des fichiers pour rien sur un service permanent.
  `--reload` reste pour les lancements manuels en développement.
- **`--no-access-log` et niveau `warning`.** launchd ne fait aucune rotation. Le
  dashboard interroge l'API en boucle ; journaliser chaque requête remplit le
  disque en silence. Constat 2 montre que ce n'est pas hypothétique.
- **`127.0.0.1` et non `0.0.0.0`.** Dashboard local : l'exposer au réseau de la
  maison n'apporte rien et ouvre une surface pour rien.
- **`KeepAlive` inconditionnel, `ThrottleInterval` à 60.** Un serveur qui tombe
  doit repartir. Mais `KeepAlive` face à un port durablement occupé produit une
  boucle de redémarrage : sans `ThrottleInterval`, launchd réessaie toutes les 10 s
  et inonde les logs.

Le rendu des posts se fait dans ce processus (Playwright synchrone dans un endpoint
FastAPI `def`, donc exécuté hors boucle d'événements, dans le pool de threads).
C'est déjà le fonctionnement actuel ; l'agent ne le change pas.

## 3. Le préambule réseau

Conséquence directe du constat 3. Un job `RunAtLoad` ou réveillé par
`StartInterval` peut partir avant que la résolution DNS fonctionne, et échouer sec
pour rien.

`app/db.py` gagne `attendre_base(essais, delai)` : quelques `SELECT 1` espacés
avant de rendre la main, et un échec explicite si la base reste injoignable. Les
points d'entrée planifiés l'appellent en premier. C'est dans `app/db.py` et non
dans `scripts/` parce que c'est une préoccupation de connexion, et que ce module
possède déjà l'engine.

Testable en injectant une fonction de connexion qui échoue les *n* premières fois.

## 4. `scripts/install_agents.py`

Point d'entrée unique, avec l'idiome que le projet utilise déjà pour
`seed_fr_sources` : **sans `--apply`, il affiche le plan ; avec, il applique.**
Plus `--status` et `--uninstall`.

Les plists sont construits avec `plistlib` (bibliothèque standard) : valides par
construction, et **testables sans jamais appeler `launchctl`** — on asserte le
contenu du dictionnaire. Les trois fichiers `.plist` versionnés disparaissent du
dépôt, avec leurs chemins en dur (`/Users/theoherve/...`), qui cassaient
silencieusement si le dépôt bougeait.

Les chemins sont résolus à l'installation : racine du dépôt, `$HOME`, et le `uv`
trouvé sur le PATH.

**La migration est le premier travail du script**, pas une note dans la
documentation : décharger `com.musicnewsradar.pipeline` et
`com.musicnewsradar.digest`, supprimer leurs plists de `~/Library/LaunchAgents`.
Sans ça, l'ancien agent continue de tourner `run_pipeline` en local à côté du
nouveau — le double emploi qu'on cherche à supprimer.

## 5. Rotation des logs

`newsyslog`, natif macOS, sur `~/Library/Logs/music-news-radar/*.log`, avec un
plafond en taille.

**Friction assumée :** `/etc/newsyslog.d/` demande `sudo`, que le script
d'installation ne peut pas prendre tout seul. Il affichera donc le contenu du
fichier et la commande exacte à lancer, une fois pour toutes, et `--status` dira
si la rotation est configurée. Une commande manuelle vaut mieux qu'un script qui
réclame les droits administrateur pour trois lignes.

Au passage, `--apply` tronque les logs existants : 43 Mo de 402 X ne servent à rien.

## 6. Ce qui est vérifiable, et ce qui ne l'est pas

Il faut le dire franchement : `launchctl` n'est pas testable en CI.

**Testable, et testé :** la construction de chaque plist (chemins résolus, cadence,
`RunAtLoad`, `KeepAlive`, `ThrottleInterval`, redirections de logs), la liste des
labels à migrer, le préambule réseau avec une connexion qui échoue les premières
fois, et le plan affiché sans `--apply`.

**Non testable, à vérifier à la main :** que les agents démarrent bien à l'ouverture
de session — cela demande une déconnexion/reconnexion. Le plan le prescrira comme
étape manuelle explicite, pas comme une case cochée d'office.

## 7. Hors périmètre

- Le modèle local et son câblage (chantier C).
- La file quotidienne de posts et la boucle validé/refusé (chantier B).
- Toute composition automatique de posts : l'agent d'ingestion n'engage aucun appel
  Claude. Le déclencheur et le nombre seront décidés au chantier B.
- L'activation de la facturation X (décision de l'utilisateur, hors code).
- Empêcher le Mac de dormir : écarté, inadapté à un portable.

## Corrections de documentation incluses

1. `CLAUDE.md` : `run_ingest` ne déclenche pas les alertes hot (constat 4).
2. `CLAUDE.md` : la section scheduler décrit GitHub Actions comme seul
   ordonnanceur et les agents launchd comme une « ancienne alternative locale ».
   C'est l'inverse de la cible : deux ordonnanceurs, cloisonnés.
3. `docs/superpowers/specs/2026-08-08-rendu-adaptatif-gabarits-design.md` : le
   chantier 0 n'y est plus « GitHub Actions coupé ».
