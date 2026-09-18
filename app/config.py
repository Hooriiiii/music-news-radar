from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/music_news_radar"
    sql_echo: bool = False

    # Rétention : on ne garde en base que les articles publiés dans les N derniers
    # jours (par date de POST, pas d'ajout) -- le reste est purgé à chaque run
    retention_days: int = 14

    # Scoring Claude (étape 4)
    anthropic_api_key: str | None = None
    scoring_model: str = "claude-haiku-4-5"
    # Composition des posts : contrairement au scoring (volume eleve, Haiku pour
    # le cout), la composition tourne quelques fois par jour a la demande et sa
    # qualite se voit directement sur le feed -- on paie un modele plus fort.
    composer_model: str = "claude-sonnet-5"
    # --- Pré-filtre local (cascade à deux étages) ---
    # Un petit modèle tournant en local note l'article AVANT Claude : sous le
    # seuil, on n'appelle jamais Claude et on garde la note locale.
    # Mesure du 2026-08 sur 36 articles réels : le modèle local n'a JAMAIS noté
    # sous 60 un article que Haiku gardait au-dessus de 60 -- ses erreurs sont
    # toutes des faux positifs (il laisse passer trop), jamais des faux négatifs.
    # Mauvais juge, bon videur : on l'utilise en pré-filtre, pas en remplaçant.
    #
    # Opt-in : le pipeline doit tourner à l'identique sans LM Studio installé.
    local_scoring_enabled: bool = False
    # Endpoint compatible OpenAI exposé par LM Studio
    local_scoring_url: str = "http://localhost:1234/v1/chat/completions"
    # Modèle chargé dans LM Studio (identifiant tel qu'il l'expose)
    local_scoring_model: str = "qwen/qwen3-4b"
    # Sous cette note de pertinence LOCALE, l'article est écarté sans appel Claude.
    # Volontairement plus bas que le seuil du digest (60) : la mesure porte sur
    # 36 articles, assez pour voir la tendance, pas pour jurer que le zéro faux
    # négatif tient à l'échelle. Ces 15 points sont la marge de sécurité.
    local_prefilter_threshold: int = 45
    # Le modèle local tourne sur CPU/GPU de la machine : quelques secondes par
    # article, mais un modèle qui se charge peut prendre bien plus long
    local_scoring_timeout: float = 120.0

    # Seuils du design doc
    digest_relevance_threshold: int = 60
    alert_hotness_threshold: int = 80
    # Un article "hot" publié il y a plus longtemps que ça n'est plus une alerte
    alert_max_age_hours: int = 48

    # X via l'API officielle v2, pay-per-use (~0,005 $/tweet lu, since_id natif)
    x_bearer_token: str | None = None
    x_max_items: int = 50  # max_results par requête timeline (borne API : 5-100)
    # Au tout premier fetch (pas encore de since_id), ne lire que les tweets des
    # N derniers jours -> pas de backfill payant sur du vieux (borne API : < 7 j)
    x_backfill_days: int = 2
    # Mode recherche (hashtags) : volume potentiellement énorme -> cap serré
    # (borne API : 10-100) + throttle x_min_fetch_interval_hours appliqué.
    # Plafond de coût = max_results x runs/jour x 0,005 $
    x_search_max_results: int = 10
    # Sélection par popularité en mode recherche : un tweet passe s'il vient
    # d'un compte vérifié OU s'il atteint l'un de ces seuils d'engagement
    # Calibré sur sonde réelle (2026-07-06) : le flux "noms d'artistes" est
    # propre, 50 attrape les clips qui décollent sans reprendre le bruit
    x_search_min_likes: int = 50
    x_search_min_replies: int = 20
    # Mode UGC (préfixe "ugc:" dans l'url de la source) : au-delà de ce nombre
    # d'abonnés, un compte est considéré média/agrégateur -- pas de l'UGC
    x_ugc_max_followers: int = 25000

    # Radar maison (source X avec url "radar:") : les artistes du moment sont
    # dérivés du flux scoré et injectés dans la recherche X has:videos
    radar_window_days: int = 7
    radar_min_relevance: int = 60
    radar_max_artists: int = 10
    # Seuls les artistes issus de ces genres de source alimentent le radar :
    # les sources festivals (multi-genres) ne doivent pas y injecter du metal/folk.
    # Le modèle ne filtre pas le genre de façon fiable -> gate déterministe ici.
    radar_source_genres: str = "electronic,pop,new_music,music"

    # Alternative Apify (nécessite un plan Apify payant -- non utilisée par défaut)
    apify_token: str | None = None
    apify_actor_id: str = "apidojo~tweet-scraper"
    apify_max_charge_usd: float = 0.25
    x_min_fetch_interval_hours: int = 6

    # --- Source Instagram (business_discovery, connexion FACEBOOK) ---
    # Gratuit, contrairement a X. Jeton DISTINCT de `meta_access_token` (plus
    # bas, connexion Instagram, sert a PUBLIER) : business_discovery n'existe que
    # sur graph.facebook.com et exige un jeton utilisateur Facebook (EAA...).
    # Absent = les sources Instagram sont sautees en silence. Jeton de 60 jours
    # une fois etendu, a renouveler a la main (Graph API Explorer -> debogueur
    # de jeton -> « Etendre »). `meta_ig_user_id` et `meta_api_version` sont
    # partages avec la publication.
    meta_fb_access_token: str | None = None
    # Nombre de posts recents lus par compte et par run. L'appel est gratuit, mais
    # il consomme le quota Graph de l'app (~200 appels/heure) : inutile d'aller
    # chercher loin a chaque passage horaire.
    instagram_max_items: int = 12
    # Chaque compte n'est lu qu'une fois toutes les N heures, et les comptes sont
    # REPARTIS sur les passages (source.id modulo N) : lire 250 comptes a chaque
    # passage horaire depasserait le quota Graph de l'app, et un artiste ne poste
    # pas plusieurs fois par heure. Mesure du 2026-09-17 : ~310 appels + un
    # passage de 120 comptes dans la meme heure ont epuise le quota (code 4).
    # 1 heure depuis la veille rapide (2026-09-18) : la mesure a montre 141 min de
    # latence mediane sur les comptes d'artistes contre 48 min sur la presse RSS
    # -- Kinda apprenait l'actu par les medias. 123 comptes lus chaque heure font
    # 123 appels/h, loin des ~430/h qui ont rendu le code 4 le 2026-09-17.
    instagram_intervalle_heures: int = 1

    # --- Veille rapide (agent `com.musicnewsradar.veille`, toutes les 5 min) ---
    # Une LISTE COURTE de comptes relus a la minute plutot que tous les comptes :
    # 123 comptes toutes les 5 min feraient 1 476 appels/h, 3,4x le plafond
    # observe. Douze comptes en font 144. Des labels tres actifs (STMPD, Defected)
    # et les festivals FR plutot qu'un seul artiste : on veut du flux, pas un pari.
    veille_comptes: str = (
        "stmpdrcrds,defectedrecords,vladimircauchemar,"
        "fredagainagainagainagainagain,etjusticepourtous,edbanger,astropolis,"
        "nuits_sonores,transmusicales,positiveeducation,madeon,worakls.music"
    )
    veille_intervalle_minutes: int = 5
    # Plafond DUR d'appels Graph par heure pour la veille seule. Le quota est
    # partage avec la publication (`business_discovery` des visuels d'artistes) :
    # la veille ne doit jamais pouvoir le manger entierement, meme si la liste
    # grossit par inadvertance.
    veille_appels_max_par_heure: int = 200
    # Sur un code de quota (4, 17, 32, 613), la veille s'arrete net pendant ce
    # delai. Le pipeline horaire, lui, continue : sa cadence n'est pas en cause.
    veille_pause_quota_minutes: int = 30
    # Le scoring d'un passage est BORNE : l'arriere-plan peut porter des centaines
    # d'articles non scores, et la veille tourne 288 fois par jour.
    veille_scoring_max: int = 20

    # --- Supervision : notifications macOS ---
    # Le canal d'alerte du socle local. Discord est coupé et l'utilisateur ne
    # veut pas de mail : sans ça, une panne ne se signale nulle part -- trois
    # sont passées inaperçues (agent digest 5 semaines, source X figée 7 jours,
    # crédits API épuisés).
    notifications_enabled: bool = True
    # Au-delà, une source qui n'a rien ramené est considérée morte et non calme.
    # 7 jours : assez long pour le radar X, muet en semaine par nature ; assez
    # court pour attraper une panne avant qu'elle s'installe.
    source_muette_jours: int = 7

    # --- Illustration des articles (og:image) ---
    # 57 % des articles arrivaient sans média, dont 81 % des articles RSS. La
    # balise og:image des éditeurs couvre 100 % des cas mesurés.
    # Seuil : celui du digest. Un article sous la barre ne sera jamais posté,
    # lui chercher une image serait une requête réseau pour rien.
    illustration_min_relevance: int = 60
    illustration_timeout: float = 20.0
    # Borne le coût réseau d'un run -- surtout au premier passage, où l'arriéré
    # se compte en centaines d'articles.
    illustration_max_par_run: int = 40

    # --- Regroupement des doublons et cascade (2026-09-17) ---
    # Une actu couverte par au moins N sources DISTINCTES passe toujours chez
    # Claude, meme sous le seuil du pre-filtre local.
    cluster_sources_claude: int = 3
    # Borne des reelections de representant et des remises a noter par run : au
    # plus un appel Claude chacune.
    cluster_reevaluations_max_par_run: int = 10

    # --- Visuels d'artistes (jamais ceux des comptes Instagram media) ---
    # Un post d'artiste plus ancien que N jours avant l'article n'est jamais pris.
    visuels_artiste_fenetre_jours: int = 10
    # Hors sujet (un portrait, un live), un post n'est accepte qu'a N jours de l'actu.
    visuels_artiste_generique_jours: int = 5
    visuels_max_photos: int = 3
    # business_discovery en direct, a la composition : le quota Graph est partage
    # avec le pipeline et a deja rendu le code 4 pendant une heure.
    visuels_lectures_max_par_passage: int = 2
    visuels_lectures_max_par_jour: int = 24
    visuels_cache_lectures_heures: int = 6
    visuels_pause_quota_minutes: int = 60
    # Copies locales des images d'artistes : les URL du CDN Instagram expirent.
    visuels_cache_dir: Path = Path.home() / "Library/Application Support/Kinda/visuels"
    visuels_cache_jours: int = 7

    # --- Fond de cover : une illustration, jamais une affiche ---
    # Un juge visuel (Claude vision) classe les images candidates avant d'en faire
    # le fond d'une cover. Coupe-circuit : a false, on garde la premiere image
    # exploitable, c'est-a-dire le comportement d'avant le 2026-09-18.
    illustration_vision_enabled: bool = True
    # Haiku suffit : la question posee est « affiche ou photo ? », pas une analyse
    # fine. Trois images reduites a 512 px coutent ~1 400 jetons d'entree.
    visuels_model: str = "claude-haiku-4-5"
    illustration_max_candidats: int = 3
    illustration_taille_px: int = 512
    # Verdicts memorises par URL : une recomposition (ou un second creneau sur le
    # meme article) ne repaie pas le juge.
    illustration_cache_path: Path = (
        Path.home() / "Library/Application Support/Kinda/illustrations.json"
    )
    illustration_cache_jours: int = 7
    # Fond de SECOURS : quand le fond retenu est encore une affiche, on va
    # chercher des photos ailleurs -- d'abord sur le compte qui a publie l'actu,
    # puis chez les artistes suivis cites (2026-09-18, le post des
    # Transmusicales, dont le carrousel n'etait fait que d'affiches).
    # Deux photos ne suffisaient pas : sur un compte de festival en pleine saison
    # d'annonces, les deux derniers posts sont deux affiches de plus. Six
    # candidats par reserve, et une fenetre bien plus large que celle des visuels
    # d'artistes (10 j) -- un fond n'a pas a ILLUSTRER l'actu, une photo de
    # concert d'il y a trois semaines fait parfaitement l'affaire.
    fond_secours_max_photos: int = 6
    fond_secours_fenetre_jours: int = 30
    # DERNIER recours, apres le compte source et les artistes cites : une image
    # du WEB (2026-09-18, « ne saute pas l'actu, cherche une image sur le web en
    # dernier recours »). Une seule recherche par composition, memorisee par
    # requete -- une recomposition, ou un second creneau, ne repaie rien.
    visuels_web_enabled: bool = True
    visuels_web_max_candidats: int = 3
    visuels_web_cache_path: Path = (
        Path.home() / "Library/Application Support/Kinda/visuels_web.json"
    )
    # Court : la « photo recente » d'un artiste ne l'est plus une semaine apres.
    visuels_web_cache_jours: int = 3

    # --- Instagram (connexion Instagram, sans Facebook) ---
    # App Meta « Kinda Radar », compte kindanewsfrance declare testeur. Le jeton
    # du .env n'est que la valeur INITIALE : une fois rafraichi, le jeton courant
    # vit dans `meta_token_path` -- le code ne reecrit jamais .env.
    meta_access_token: str | None = None
    meta_ig_user_id: str | None = None
    # Verifie en lecture seule le 2026-09-16 : v21.0 et v23.0 repondent.
    meta_api_version: str = "v23.0"
    meta_token_path: Path = Path.home() / "Library/Application Support/Kinda/instagram_token.json"
    # Le mode choisi depuis le dashboard vit ici (a cote du jeton, meme dossier
    # applicatif hors du depot) -- `.env` (publication_mode ci-dessous) n'est
    # plus que la valeur de repli si ce fichier n'existe pas encore.
    publication_mode_path: Path = Path.home() / "Library/Application Support/Kinda/publication.json"

    # --- Publication automatique ---
    # Les fichiers figes au gel (T-2h), un sous-dossier par creneau.
    publication_dir: Path = Path.home() / "Library/Application Support/Kinda/publication"
    # Meta exige des proportions entre 4:5 et 1,91:1 (documentation, 2026-09-16) :
    # la cover 3:4 est recadree en 4:5. A passer a 0.75 seulement si un essai reel
    # montre que Meta accepte le 3:4.
    publication_ratio_min: float = 0.8
    # Fenetres de publication, heure de Paris. L'heure exacte est tiree au hasard
    # dans chaque fenetre, pour ne pas publier comme une horloge.
    publication_fuseau: str = "Europe/Paris"
    publication_fenetre_matin: str = "08:30-11:00"
    publication_fenetre_apres_midi: str = "17:00-19:30"
    # 3 jours x 2 creneaux.
    publication_creneaux: int = 6
    # Eligibilite a la file : la barre du digest, et une actu de moins de 3 jours.
    publication_min_relevance: int = 60
    publication_fraicheur_jours: int = 3
    # arret : l'agent ne fait rien. apercu : tout, jusqu'au conteneur cree chez
    # Meta, SAUF la publication. reel : publie. Publier est public et
    # irreversible : le passage a « reel » est une decision de l'utilisateur.
    publication_mode: Literal["arret", "apercu", "reel"] = "arret"
    # La composition (appel Claude paye) n'a lieu qu'a J-1 : on ne paie jamais
    # un post qui se fait doubler avant demain.
    publication_composition_heures: int = 24
    # Gel a T-2h : rendu et telechargements faits d'avance.
    publication_gel_heures: int = 2
    # Au-dela, on ne publie pas : un Mac rouvert le soir lacherait sinon la
    # journee entiere d'un coup.
    publication_retard_max_minutes: int = 90
    # Un creneau gele a moins de ce delai de son heure est traite des ce passage :
    # reclame, conteneur cree, puis ATTENTE jusqu'a l'heure affichee avant
    # media_publish. Un intervalle de l'agent (600 s) plus une marge : sinon un
    # post pouvait partir jusqu'a 10 minutes apres l'heure du dashboard.
    publication_avance_s: int = 660
    # Un tunnel rapide met parfois plus de 90 s a etre visible du DNS public. Un
    # essai = un tunnel entier (nouveau sous-domaine a chaque lancement), budget
    # de DELAI_JOIGNABLE_S chacun : trois essais bornent l'attente a ~4,5 min,
    # bien en deca des 90 minutes de retard tolere.
    publication_tunnel_essais: int = 3

    # --- Reels automatiques (en plus des carrousels) ---
    # Fenetres propres, heure de Paris, separees par des virgules : une fenetre =
    # un reel par jour. Elles ne doivent chevaucher ni les fenetres carrousel ni
    # entre elles (slots.FenetresInvalides) : publier_a est unique.
    publication_reel_fenetres: str = "12:00-14:00,20:30-22:30"
    # 3 jours x 2 reels. 0 desactive les reels sans toucher aux carrousels.
    publication_creneaux_reel: int = 6
    # Qui a le droit d'etre reposte en video : les comptes qui PRODUISENT l'actu.
    # Jamais un media par defaut -- ce serait une double reprise (voir « Qui parle »).
    publication_reel_categories: str = "artiste,festival,label,club"
    # Meta accepte 3 s a 15 min ; 90 s est une borne EDITORIALE. Trop longue :
    # refusee, jamais coupee.
    publication_reel_duree_min_s: float = 3.0
    publication_reel_duree_max_s: float = 90.0
    # Poids max de la video source telechargee, et du reel assemble (Meta : 300 Mo).
    publication_reel_source_max_mo: int = 100
    publication_reel_max_mo: int = 100
    # Le reel apparait aussi dans la grille du compte.
    publication_reel_share_to_feed: bool = True
    # Coupe-circuit de yt-dlp SEUL (app/publish/ytdlp.py) : a false, seules les
    # videos a URL directe de l'API restent eligibles en reel, et yt-dlp n'est
    # jamais lance. Le coupe-circuit de TOUS les reels vit au dashboard
    # (`reels_actifs` dans publication.json), COUPE tant qu'il n'a pas ete coche.
    publication_reel_ytdlp: bool = True

    # Livraison (étape 5)
    discord_webhook_url: str | None = None
    # Mention ajoutée aux alertes pour déclencher une vraie notification
    # (les webhooks sans mention ne pingent pas avec le réglage Discord par
    # défaut "Mentions @ uniquement"). Mettre vide pour désactiver.
    discord_mention: str | None = "@here"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    digest_to: str | None = None
    digest_from: str | None = None  # défaut : smtp_user
    digest_max_articles: int = 25
    # Anti-monopole : une source prolifique ne peut pas écraser les autres
    digest_max_per_source: int = 6


settings = Settings()
