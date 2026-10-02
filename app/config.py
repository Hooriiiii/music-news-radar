from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/music_news_radar"
    sql_echo: bool = False

    # Rétention : on ne garde en base que les articles publiés dans les N derniers
    # jours (par date de POST, pas d'ajout) -- le reste est purgé à chaque run.
    # 2 jours (2026-09-21) : le feed ne vit que de l'actu fraîche, et la file de
    # publication ne regarde que `publication_fraicheur_jours`. Les USED ne sont
    # jamais purgés (archive éditoriale), voir `pipeline/retention.py`.
    retention_days: int = 2

    # Cadence du passage de `run_pipeline` (agent launchd
    # `com.musicnewsradar.pipeline`). Elle ne vivait que dans
    # `scripts/install_agents.py`, et le dashboard annonçait « toutes les 2 h »
    # en dur -- faux depuis que l'agent est horaire. Un texte d'interface qui
    # recopie un réglage finit toujours par mentir : la valeur vit donc ici,
    # `install_agents` la lit, et l'interface la DÉRIVE (`GET /reglages`).
    pipeline_intervalle_s: int = 3600

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
    # Qwen3 **1.7B** et non 4B : mesure du 2026-09-30 sur la machine de
    # l'utilisateur (16 Go de RAM, swap a 9,5 Go sur 10,2 avant reglage -- le
    # 4B chargé large faisait planter le poste). Sur 30 articles deja notes par
    # Claude, le 1.7B ne perd AUCUN article : il ne descend jamais sous le
    # seuil un article que Claude a juge bon (>= 60). Ses 10 desaccords vont
    # tous dans le sens SUR -- il envoie a Claude un article que Claude note
    # sous 45, ce qui ne coute qu'un appel. C'est exactement le compromis
    # voulu pour un pre-filtre : rater une economie, jamais un article.
    local_scoring_model: str = "qwen/qwen3-1.7b"
    # Le contexte avec lequel `scripts.assurer_prefiltre` charge le modele. Le
    # defaut de LM Studio (4096) ne suffit PAS au SYSTEM_PROMPT (~180 lignes)
    # plus un article long : mesure le 2026-09-24, il rend un 400 sur les seuls
    # articles longs -- donc il echoue precisement sur ceux qui coutent le plus
    # cher chez Claude, et reussit sur les autres, ce qui lui donne l'air de
    # fonctionner. 16384 passe largement et tient dans la memoire d'un 4B.
    # 8192 et non 16384 : le prompt systeme pese a lui seul ~14 000 caracteres
    # et domine tout (l'article n'ajoute que quelques centaines) ; le plus gros
    # prompt mesure sur 400 articles reels fait 15 851 caracteres, soit ~5 300
    # tokens en estimation prudente. 8192 laisse de la marge pour la sortie.
    # 4096 etait TROP COURT et rendait un 400 sur les seuls articles LONGS --
    # donc precisement ceux qui coutent le plus cher chez Claude.
    local_scoring_context: int = 8192
    # UN SEUL emplacement : le pre-filtre note article par article, jamais en
    # parallele. Le defaut de LM Studio est 4, et le cache d'attention se paie
    # par emplacement -- 16384 x 4 tenait 65 536 tokens en memoire pour rien,
    # sur une machine de 16 Go. C'est ce reglage, plus que le poids du modele,
    # qui a fait planter le poste.
    local_scoring_parallel: int = 1
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
    # Sur un code de quota (4, 17, 32, 613), les LECTURES Meta s'arretent net
    # pendant ce delai -- veille rapide ET passage horaire, qui partagent la
    # meme pause depuis le 2026-09-19 (`app/pipeline/quota_graph.py`) : un appel
    # refuse consomme quand meme du quota, donc insister entretient la panne.
    # Le nom garde son prefixe `veille_` pour ne pas invalider les `.env`
    # existants ; la valeur, elle, gouverne desormais les deux agents. La
    # PUBLICATION n'est jamais suspendue par cette pause.
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
    # Variante PLEINE RESOLUTION de l'og:image (2026-09-21). Certains CDN
    # d'editeurs (WordPress) recopient dans og:image le parametre de
    # redimensionnement de la page -- mesure sur le post 71 (CLAUDE.md) :
    # `?w=564` rendait 564x345 (50 Ko) la ou la meme image sans ce parametre
    # pese 386 Ko en 2156x1320. Le resultat (variante retenue) est memorise par
    # URL, meme mecanique que `illustration_cache_path`, pour ne pas repayer la
    # requete de verification a chaque passage tant que l'og:image de
    # l'article ne change pas.
    illustration_variante_cache_path: Path = (
        Path.home() / "Library/Application Support/Kinda/illustrations_variantes.json"
    )
    illustration_variante_cache_jours: int = 7

    # --- Regroupement des doublons et cascade (2026-09-17) ---
    # Une actu couverte par au moins N sources DISTINCTES passe toujours chez
    # Claude, meme sous le seuil du pre-filtre local.
    cluster_sources_claude: int = 3
    # Borne des reelections de representant et des remises a noter par run : au
    # plus un appel Claude chacune.
    cluster_reevaluations_max_par_run: int = 10
    # Fenetre d'archive (2026-09-21) : distincte de DEFAULT_WINDOW_HOURS (48h,
    # rapprochement ordinaire entre articles frais). Un article deja PUBLIE
    # (USED) sert de representant possible pour un nouvel article au-dela de
    # cette fenetre -- lui seul, jamais l'inverse (voir `link_duplicates`).
    # 10 jours (2026-10-02), apres avoir ete a 30 puis ramene a 7 le 2026-09-21.
    # L'histoire de cette valeur compte, parce qu'elle se lit dans les deux sens.
    # Le passage de 30 a 7 n'etait PAS arbitraire : sur un feed centre sur
    # quelques artistes recurrents, plus la fenetre est large, plus deux actus
    # DIFFERENTES du meme artiste ont de chances de se croiser -- et un faux
    # positif d'archive est irreversible en pratique. Mais 7 a RATE un cas reel
    # le 2026-10-02 : « Calvin Harris DJs for 1.6 million people at
    # record-breaking Sao Paulo show » (DJ Mag, 21/09) repris par Mixmag le
    # 30/09, soit 8,99 jours plus tard -- la paire n'etait meme pas examinee.
    #
    # La valeur est donc MESUREE sur `event_observations`, la seule table qui
    # porte des ecarts au-dela de la retention de 2 jours (jamais purgee) :
    # 298 ecarts entre la premiere source d'un evenement et chacune de ses
    # autres sources distinctes (152 evenements a >= 2 sources), le 2026-10-02.
    #
    #     <= 2 j  84,9 %    <= 7 j  97,7 %    <= 9 j  100,0 %
    #     <= 3 j  89,3 %    mediane 0,53 j    p95 5,74 j    max 8,91 j
    #
    # La duree de vie editoriale REELLE d'une actu s'arrete a 8,91 jours sur ces
    # donnees. 7 couvrait 97,7 % ; 10 couvre 100 % avec un jour de marge, et
    # c'est la plus petite valeur qui le fait. On ne revient PAS a 30 : la queue
    # mesuree est vide entre 9 et 30 jours, donc les 20 jours de plus
    # n'ajouteraient aucun vrai positif et rouvriraient exactement le risque qui
    # avait fait descendre la valeur.
    #
    # Cout mesure du meme jour : 11 representants USED a 7 jours, 19 a 10 jours,
    # et ZERO paire de plus dans les bandes de TITRE (certaine >= 0,8 comme
    # douteuse >= 0,65) a n'importe quelle profondeur jusqu'a 30 jours sur ce
    # cliche. L'elargissement ne se paie donc, aujourd'hui, que sur la porte du
    # RESUME ci-dessous, dont le budget est borne a part.
    #
    # Cette borne sert aussi au cout : les USED ne sont jamais purges, donc sans
    # elle la comparaison (quadratique) grandirait avec l'age du projet.
    #
    # DEUX DEPENDANTS A CONNAITRE. (1) `events._evenement_deja_observe` lit
    # cette valeur pour sa branche URL (detection de re-ingestion) : elle passe
    # de 7 a 10 jours, ce qui est benin -- l'identite d'URL est bien plus forte
    # que celle du titre, et un marronnier republie des mois plus tard sort de
    # toute facon de la fenetre. (2) `event_features.NOUVEAUTE_HORIZON_HEURES`
    # porte 7 * 24 EN DUR et ne suit PAS : cet horizon repond a une autre
    # question (« quelle profondeur d'archive faut-il pour oser conclure a la
    # nouveaute ? »), calibree sur sa propre mesure -- la faire bouger ici
    # changerait une feature de la Phase 2C par effet de bord.
    cluster_archive_jours: int = 10
    # Seuil PROPRE a l'archive, strictement plus haut que `DEFAULT_THRESHOLD`
    # (0,65) et sur une mesure differente (`archive_similarity_from_tokens` :
    # les mots partages rapportes au titre le plus LONG, la seule facon de
    # compter ce qui DIFFERE -- le recouvrement, lui, vaut 1.00 des qu'un titre
    # est contenu dans l'autre). Cale sur les trois faux positifs releves le
    # 2026-09-21 : « ... annonce un nouvel EP » / « ... annonce une tournee
    # mondiale » (0,60), « ... sort un nouveau morceau » / « ... un nouvel
    # album » (0,75), « Les Transmusicales devoilent leur programmation » /
    # « ... une nouvelle vague » (0,67) -- tous trois epargnes. Une vraie
    # reprise traduite (« Dour Festival unveils its 2027 lineup » / « Dour
    # Festival devoile son line-up 2027 ») reste a 0,80, donc attrapee.
    cluster_archive_seuil: float = 0.8

    # --- Arbitrage Claude des reprises douteuses contre l'archive (Task 7,
    # 2026-09-21). La bande entre `cluster_archive_seuil` (certain) et
    # `DEFAULT_THRESHOLD` (0.65, "different" en dessous) est celle ou les deux
    # mesures se contredisent -- voir `app/pipeline/cluster_arbitrage.py`.
    cluster_arbitrage_enabled: bool = True
    # Borne le cout par PASSAGE de `link_duplicates` : ~0,001 $ par appel
    # (Haiku, quelques centaines de jetons d'entree et de sortie mesures --
    # voir le rapport de la tache), donc 20 appels/passage horaire reste
    # negligeable meme a 24 passages/jour. `run_veille` (toutes les 5 min) ne
    # passe jamais d'arbitre : seul le passage horaire (`run_pipeline`) paie.
    cluster_arbitrage_max_par_passage: int = 20
    # Verdicts memorises par PAIRE d'articles : une paire n'est jamais
    # redemandee. A cote du jeton, meme mecanique que `illustration_cache_path`.
    cluster_arbitrage_cache_path: Path = (
        Path.home() / "Library/Application Support/Kinda/cluster_arbitrage.json"
    )
    # Mesure du 2026-09-21 : le SDK retire `maxLength` du schema envoye au
    # modele, donc une reponse dont `raison` deborde n'est PAS rejetee cote
    # serveur -- seulement revalidee cote client, APRES facturation
    # (`VerdictArbitrage` tronque desormais au lieu de rejeter).
    # Ce plafond de TENTATIVES reste un filet pour toute autre panne
    # systematique sur une meme paire (JSON hors schema pour une autre
    # raison, par exemple) : sans lui, une paire qui echoue toujours aurait pu
    # consommer le budget de CHAQUE passage indefiniment, au detriment des
    # paires jamais tentees (voir `link_duplicates`, qui sert ces dernieres en
    # priorite). Au-dela, la paire n'est plus jamais retentee -- sans pour
    # autant memoriser un verdict, puisqu'aucune reponse valide n'a jamais ete
    # rendue.
    cluster_arbitrage_tentatives_max: int = 3

    # --- Arbitrage des paires FRAICHES (Phase 2B, Task 3, 2026-09-24) ---
    # La meme machinerie (cache par paire, verdicts manuels prioritaires,
    # plafonds, coupe-circuit) s'applique desormais aussi aux paires de deux
    # articles FRAIS dont le recouvrement de titre est SOUS le seuil de lien
    # automatique (`DEFAULT_THRESHOLD`, 0.65) mais au-dessus de ce plancher.
    # Coupe-circuit propre : a false, aucune paire fraiche n'est arbitree et
    # le pipeline rend EXACTEMENT les memes decisions qu'avant ce chantier.
    cluster_arbitrage_frais_enabled: bool = True
    # 0.30 et pas 0.35 : mesure du 2026-09-24 sur 50 paires reelles jugees par
    # Haiku -- 0 vrai positif sur les 18 paires sous 0.30, et les deux seuls
    # rates purement semantiques sont a 0.33. La courbe est PLATE entre 0.33 et
    # 0.57 (22 % / 20 % / 10 % de vrais meme-evenements par tranche) : aucun
    # seuil ne les separe, d'ou un arbitrage et non un seuil abaisse (abaisser
    # `DEFAULT_THRESHOLD` a 0.40 donnait 87 % de faux rapprochements).
    cluster_arbitrage_frais_seuil: float = 0.30
    # Anti-gabarit : nombre d'articles d'UNE MEME source qui doivent porter les
    # mots partages d'une paire pour que la ressemblance soit imputee a un
    # gabarit de redaction plutot qu'a un evenement. 3 = « au moins un article
    # au-dela de la paire elle-meme ». Mesure du 2026-09-24 sur la fenetre
    # reelle de 48 h (601 articles, 488 paires dans la bande) : 211 paires
    # ecartees, dont les 179 de AIM UK (« The winner of X at the 2026
    # Independent Music Awards », un gabarit de legende publie en serie), et la
    # distribution est nette -- 44 paires a 2 porteurs, puis 17 a 3, 12 a 6,
    # 60 a 18. Les paires Billboard x2 et Soompi x2 qui couvrent reellement
    # deux fois la meme actu restent a 2 porteurs, donc gardees : c'est ce qui
    # distingue ce critere du remede naif (« ecarter les paires de meme
    # source »), qui tuerait 3 des 5 vrais rates mesures.
    cluster_arbitrage_gabarit_min_articles: int = 3

    # --- Porte de candidature sur le RESUME (2026-09-24) ---
    # La dedup rapproche par les mots du TITRE ; de vrais memes-evenements n'en
    # partagent aucun (le record d'Ella Langley couvert par 4 articles
    # Billboard, une contrefacon de vinyles chez MBW et Mixmag). Ce qui les
    # attrape est le nombre ABSOLU de mots significatifs partages des RESUMES
    # -- le recouvrement NORMALISE sature sur les captions courtes. Voir
    # `app/pipeline/cluster_resume.py` pour la mesure complete.
    # Coupe-circuit propre : a false, aucune paire n'est candidate par son
    # resume et le pipeline rend EXACTEMENT les memes decisions qu'avant.
    cluster_arbitrage_resume_enabled: bool = True
    # 6 mots partages. Mesure du 2026-09-24 sur 48 h reelles : 9 paires a
    # sh>=7 (9 vraies), 17 a sh>=6 (10 vraies), 43 a sh>=5 (13 vraies). C'est
    # une borne de BUDGET, pas de precision : un seuil bas ne pose PAS de
    # mauvais liens (l'arbitre a rejete 27 faux sur 27), il paie des appels
    # pour rien. Le baisser coute de l'argent, jamais un lien a tort.
    cluster_arbitrage_resume_seuil: int = 6
    # Budget PROPRE, volontairement separe de
    # `cluster_arbitrage_max_par_passage` : au premier passage sur une fenetre
    # de 48 h deja pleine, cette porte peut presenter des dizaines de paires
    # (56 mesurees le 2026-09-24) et affamerait la bande ARCHIVE, celle qui
    # protege contre le repost d'une actu DEJA PUBLIEE -- le risque le plus
    # cher des trois. Deux budgets se bornent chacun ; un budget commun se
    # ferait manger par le plus bavard.
    cluster_arbitrage_resume_max_par_passage: int = 10

    # --- La meme porte, mais CONTRE L'ARCHIVE (2026-10-02) ---
    # Le defaut constate ce jour-la : le compte s'appretait a republier DEUX
    # actus deja publiees, et aucune des deux n'etait rattrapable par les
    # mesures de titre.
    #   1. « This is happening.. » (Charlotte de Witte, son propre compte,
    #      publie le 29/09) contre « Charlotte de Witte and KI/KI announce
    #      first-ever b2b set at Flanders Expo » (DJ Mag) : ZERO mot de titre
    #      en commun, donc 0,00 aux deux mesures. Les RESUMES, eux, partagent
    #      six mots (belgique, charlotte, expo, flanders, premier, witte).
    #   2. « Calvin Harris DJs for 1.6 million people... » (DJ Mag, 21/09)
    #      contre « Calvin Harris sets Guinness World Record... » (Mixmag,
    #      30/09) : similarite d'archive 0,25, recouvrement 0,29, et CINQ mots
    #      de resume partages (calvin, grand/million, harris, public, record).
    # La porte sur le resume les attrapait tous les deux -- elle ne regardait
    # simplement jamais l'archive. C'est l'extension que le module documentait
    # comme « possible, a mesurer d'abord » ; la voila mesuree, en production.
    # Coupe-circuit propre, comme les deux bandes precedentes : a false, aucune
    # paire d'archive n'est candidate par son resume et le pipeline rend
    # EXACTEMENT les decisions d'avant.
    cluster_arbitrage_resume_archive_enabled: bool = True
    # CINQ mots partages, et pas les six de la bande fraiche : le cas 2 ci-dessus
    # est a cinq. Baisser le seuil ne pose aucun lien a tort -- c'est l'arbitre
    # qui tranche, et contre l'archive il tranche TOUJOURS (il n'existe aucune
    # bande « certaine » sur le resume) -- cela paie seulement des appels.
    # Mesure du 2026-10-02 (592 articles frais x les representants USED de la
    # fenetre, paires que les bandes de titre n'ont pas deja prises) :
    #
    #     fenetre   sh>=4   sh>=5   sh>=6   sh>=7
    #        7 j      3       2       2       2
    #       10 j     12       6       3       3
    #       14 j     16       9       4       4
    #
    # A 10 jours et sh >= 5 : SIX paires, dont les TROIS vrais positifs connus
    # (les deux cas ci-dessus, plus le meme record de Calvin Harris vu depuis
    # son propre compte) ; les trois autres sont des annonces d'albums
    # d'artistes differents qu'un arbitre ecarte sur les seuls titres. Descendre
    # a 4 doublerait le nombre de paires sans ajouter un seul vrai positif
    # mesure. Monter a 6 reperdrait le cas 2.
    cluster_arbitrage_resume_archive_seuil: int = 5
    # TROISIEME budget, et pour la meme raison qui en avait fait un second :
    # l'archive est la seule bande dont un faux NEGATIF reposte une actu DEJA
    # PUBLIEE. Un budget commun avec la porte fraiche (qui peut presenter des
    # dizaines de paires au premier passage sur une fenetre pleine) la ferait
    # affamer par le plus bavard des deux. Six paires mesurees au premier
    # passage, et chacune n'est payee QU'UNE FOIS (cache par paire) : 10 est
    # large.
    cluster_arbitrage_resume_archive_max_par_passage: int = 10
    # --- Garde-fou de RECURRENCE (meme chantier) ---
    # Une PUBLICATION RECURRENTE (« Billboard Hot 100 Top 10 Countdown for
    # Sept. 26, 2026 ») raconte les memes faits qu'une vraie actu sans en etre
    # une couverture : l'arbitre repond « meme actu » et il a raison sur les
    # faits, mais lier sortirait un article de la file au nom d'un format qui
    # reviendra la semaine prochaine. Une recurrence HEBDOMADAIRE est
    # invisible a tout critere borne a 48 h ; `event_observations` n'est
    # jamais purgee et porte donc la repetition.
    # Nombre d'AUTRES titres archives de la MEME source qui doivent porter un
    # mot pour qu'il compte comme un mot de CADRE. 4 et pas 3 : mesure du
    # 2026-09-24, a 3 le critere attrapait aussi « Taylor Swift's 'The Life of
    # a Showgirl' era isn't over yet. » (Billboard IG) sur les mots « taylor »
    # et « swift » -- un artiste recurrent n'est pas un format recurrent.
    cluster_recurrence_min_titres: int = 4
    # Part du titre que le cadre doit couvrir. 0.4 mesure : a ce reglage, 8
    # des 56 paires candidates sont ecartees sans rien payer (les 7 paires du
    # Countdown et une paire de recaps de classement) et AUCUN vrai positif
    # n'est touche -- les trois cas de reference ont un cadre VIDE (0.00).
    cluster_recurrence_part_min: float = 0.4
    # Profondeur d'archive lue dans `event_observations`. 30 jours : de quoi
    # voir quatre editions d'un format hebdomadaire, sans faire grandir la
    # requete avec l'age du projet (les observations ne sont jamais purgees).
    cluster_recurrence_jours: int = 30

    # --- Tout article devient l'observation d'un evenement (Phase 2B, Task 1) ---
    # Borne le nombre d'articles qu'un passage horaire peut faire entrer dans
    # un evenement, meme esprit qu'`illustration_max_par_run` : au premier
    # passage l'arriere se compte en centaines d'articles, et chacun coute
    # quelques INSERT. 200 est cale sur le volume reel : la retention est a
    # 2 jours pour ~650 articles en base, soit ~13 articles neufs par heure --
    # le regime de croisiere n'est donc JAMAIS freine par ce plafond (15x de
    # marge), et un arriere de 650 se resorbe en quatre passages.
    events_observation_max_par_passage: int = 200

    # --- Visuels d'artistes (jamais ceux des comptes Instagram media) ---
    # Un post d'artiste plus ancien que N jours avant l'article n'est jamais pris.
    # BORNE PAR LA PURGE : ces posts sont lus dans la table `articles`, que
    # `retention_days` (2 depuis le 2026-09-21) vide au-dela de 2 jours. Tant
    # que la retention reste plus courte, 10 j ne peut PAS etre atteint -- la
    # fenetre effective est celle de la purge. Valeur gardee telle quelle : elle
    # redevient utile si la retention remonte. D'ici la, c'est la recherche WEB
    # (seul barreau qui ne lit pas la base) qui reprend le relais du fond.
    visuels_artiste_fenetre_jours: int = 10
    # Hors sujet (un portrait, un live), un post n'est accepte qu'a N jours de l'actu.
    # Meme borne par la purge que ci-dessus.
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
    # fine. Une image reduite a 512 px coute ~230 jetons d'entree.
    visuels_model: str = "claude-haiku-4-5"
    # La borne du LOT juge en UN appel. Dix et non trois depuis le 2026-09-19 :
    # ce lot ne sert plus seulement a choisir le fond, il sert aussi a plafonner
    # les cartes d'annonce du carrousel (`illustration.plafonner_affiches`), et
    # une image non jugee est une slide GARDEE -- juger trois images sur sept en
    # laissait passer cinq. Dix parce qu'un carrousel Meta ne porte pas plus de
    # dix elements (`publish.preparation.CARROUSEL_MAX`) : au-dela on paierait
    # pour juger des images que le post ne pourra jamais publier. Cout :
    # ~2 300 jetons d'entree, ~0,002 $ par composition.
    illustration_max_candidats: int = 10
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
    # BORNE PAR LA PURGE (2026-09-21) : les reserves « compte source » et
    # « artiste cite » lisent la table `articles`, que `retention_days` (2) vide
    # au-dela de 2 jours. 30 j est donc DECORATIF tant que la retention reste
    # courte : ces deux reserves sont quasi vides, sauf lecture en direct du
    # compte source. Decision de l'utilisateur : c'est la recherche WEB, seul
    # barreau qui ne lit pas la base, qui reprend le relais -- y compris pour un
    # fond ecarte par le plancher de nettete. Valeur gardee : elle redevient
    # utile si la retention remonte un jour.
    fond_secours_fenetre_jours: int = 30
    # Plancher de resolution du FOND de cover (2026-09-21). Distinct de
    # `publication_crop_upscale_max` : ce dernier borne le cadrage des SLIDES du
    # carrousel (toujours 1080x1350), alors que le fond peut remplir le gabarit
    # REEL, plus grand (1080x1920) -- pas la meme cible, donc pas le meme reglage.
    # Mesure sur les 6 derniers posts REELLEMENT publies (CLAUDE.md, releve du
    # 2026-09-21) : nets a 1,05x et 0,89x d'agrandissement (aucun agrandissement
    # notable), flous des 2,13x (2,13x / 2,39x / 3,52x / 3,91x, dont un visage
    # baveux a l'oeil nu sur le post 71).
    #
    # Le seuil est 2,2x -- la MEME valeur et la meme famille de raisonnement que
    # `publication_crop_upscale_max`, pour la meme raison : l'og:image standard
    # d'un editeur fait 1200x630, soit 1080/1200 = 0,9 mais 1350/630 = 2,14x en
    # hauteur, et c'est de loin l'illustration la plus frequente du pipeline
    # (1920x1080 -> 1920/1080 = 1,78x en reel, meme famille). Un seuil a 1,5x la
    # rejetait : la majorite des posts de presse seraient partis sur l'aplat
    # Kinda, et un article dont les slides sont par ailleurs ecartees par
    # `publication_crop_upscale_max` finissait en `PreparationImpossible`, donc
    # `file_exclu_raison` -- il ne revient JAMAIS. Jamais degrader le cas
    # nominal pour attraper un cas limite : un fond un peu mou vaut mieux qu'un
    # aplat Kinda, et bien mieux qu'un article perdu.
    #
    # Ce qu'on laisse passer sciemment : le fond du post 62 (720x900 pour un
    # reel, 2,13x), a un centieme du seuil. Les trois autres fonds flous mesures
    # (2,39x, 3,52x, 3,91x) sont bien attrapes.
    cover_fond_upscale_max: float = 2.2

    # Agrandissement du fond par super-resolution (Tache 6, 2026-09-23) : le
    # dernier barreau AVANT le saut, quand aucun candidat (actu, compte,
    # artiste, web) ne passe `cover_fond_upscale_max`. Outil : `upscayl-bin`,
    # le backend headless d'Upscayl (github.com/upscayl/upscayl-ncnn) --
    # meme famille de modeles Real-ESRGAN-ncnn-vulkan que celui cite par le
    # plan, mais activement maintenu (derniere release 2025-12-07 contre 2022
    # pour le depot officiel xinntao) et empaquete avec l'app GUI signee, donc
    # sans probleme Gatekeeper au premier lancement.
    #
    # Verifie sur cette machine (utilisateur, 2026-09-23) : le binaire vit
    # dans le bundle de l'app (`/Applications/Upscayl.app/Contents/Resources/
    # bin/upscayl-bin`), PAS sur le PATH d'un terminal ni installe par
    # Homebrew -- meme piege que ffmpeg et cloudflared pour launchd
    # (`app/content/video.py::EMPLACEMENTS_CONNUS`), en pire : il n'existe
    # meme pas d'emplacement Homebrew connu a essayer en repli, d'ou un
    # chemin exact en reglage plutot qu'une recherche sur le PATH.
    visuels_upscale_enabled: bool = True
    visuels_upscale_binaire: Path = Path(
        "/Applications/Upscayl.app/Contents/Resources/bin/upscayl-bin"
    )
    visuels_upscale_modeles: Path = Path(
        "/Applications/Upscayl.app/Contents/Resources/models"
    )
    # `upscayl-lite-4x`, pas `upscayl-standard-4x` : mesure sur l'image du
    # probleme reel (660x440, l'ADE et Shlomo, post 76) sur cette machine,
    # 1,1 s contre 5,8 s -- environ 5x plus rapide. Le fond de cover est
    # recouvert d'un voile sombre ET du texte de l'accroche (CLAUDE.md,
    # « Agrandir le fond plutot que sauter le post ») : le detail que le
    # modele standard rendrait en plus n'y est de toute facon pas visible,
    # donc la difference de qualite ne justifie pas de payer 5x plus de
    # temps, six fois par jour, pour un element decoratif. Reglable : si un
    # fond agrandi a la vole a l'oeil trop degrade, repasser sur
    # `upscayl-standard-4x` (verifie fonctionnel, mesure a 5,8-10,7 s selon
    # la charge de la machine) est un changement de configuration, pas de code.
    visuels_upscale_modele: str = "upscayl-lite-4x"
    visuels_upscale_echelle: int = 4
    # En dessous, meme le facteur 4 ne suffit STRUCTURELLEMENT pas a passer
    # `cover_fond_upscale_max` sur le gabarit le plus exigeant (le reel,
    # 1080x1920) : le vrai seuil de bascule est 219 px de cote source --
    # `resolution_suffisante((s*4, s*4), (1080, 1920))` rend False a 218 px
    # (1920/(4*218) = 2,202 > 2,2) et True a 219 px (1920/(4*219) = 2,191).
    # Un premier seuil pose a 300 px se voulait prudent mais demontrait
    # l'inverse de sa propre conclusion (« 1,6x, confortablement SOUS le
    # plancher » -- etre sous le plancher signifie justement que ca PASSE) :
    # il etait ~37 % trop haut et ecartait a tort les sources de 219 a 299 px,
    # exactement celles que cette tache existe pour sauver (revue, tour de
    # correction 1/5). 230 px garde une marge de securite modeste (~5 %) sur
    # le seuil structurel, sans reproduire l'erreur d'un seuil arbitrairement
    # conservateur. Un cote source de 200 px (l'exemple du plan) reste
    # ecarte : 800 px agrandi, 1920/800 = 2,4x, au-dela du plancher --
    # agrandir n'aurait servi a rien.
    #
    # `min(taille)` reste un proxy grossier sur un ratio extreme (ex.
    # 300x2000 : cote court retenu, mais apres plafonnement a
    # `visuels_upscale_sortie_max_px` le cote LONG domine et peut retomber
    # sous le plancher) -- rattrape sans consequence par la RE-verification
    # du plancher au gel (`preparation._agrandir_fond_si_marque`), au prix
    # d'un agrandissement tente pour rien dans ce cas rare.
    visuels_upscale_source_min_px: int = 230
    # Budget de temps par tentative, meme esprit que `video.DELAI_MAX_S` et
    # `hosting.DELAI_JOIGNABLE_S` : mesure reelle 1,1-10,7 s selon le modele
    # et la charge, marge large (x5 a x10) pour une machine chargee sans
    # laisser un gel trainer indefiniment. Une seule tentative par post (le
    # gel ne boucle pas), au GEL uniquement -- jamais a la composition ni a
    # l'ingestion.
    visuels_upscale_delai_max_s: float = 60.0
    # Le resultat n'a pas besoin de rester a la taille brute du modele (4x
    # sur 660x440 donne 2640x1760) : le fond n'a besoin que de remplir le
    # plus grand gabarit publie (1080x1920, le reel). Redimensionne apres
    # coup vers cette borne (avec une marge de cadrage) plutot que de faire
    # porter une image deux fois plus grande que necessaire au reste du gel
    # (rendu Chromium, cache disque).
    visuels_upscale_sortie_max_px: int = 1920

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

    # Mise a l'ecart TEMPORAIRE d'un article saute faute de visuel de qualite
    # (app/schedule/pause_visuels.py). Mesure : sans cette pause, un article
    # au meilleur score revenait sur le
    # creneau libere par son propre saut a CHAQUE passage de 10 min, jusqu'a
    # MANQUE, et faisait taire le compte toute une journee. Le defaut (180 min
    # = 3 h) est la MOITIE du cache du lecteur de comptes Instagram
    # (`visuels_cache_lectures_heures`, 6 h) : retenter avant ce cache relit
    # les memes donnees pour rien, mais retenter deux fois par cycle de cache
    # laisse une chance des qu'une nouvelle publication du compte source ou
    # d'un artiste cite peut avoir change la donne -- sans laisser le compte
    # silencieux une demi-journee. Reste tres court face a `retention_days`
    # (2 j) : un article peut retenter une quinzaine de fois avant sa purge.
    visuel_saute_pause_minutes: int = 180

    # Un REPORT (`VisuelsDifferes`, panne de transport dans une reserve) qui
    # ne guerit jamais rejoue exactement le defaut qu'une mise a l'ecart
    # corrige -- mesure : sans borne, l'article campe sur son creneau jusqu'a
    # MANQUE (1 publication sur 4, 3 MANQUE, 0 mise a l'ecart). Au-dela de ce
    # nombre de reports CONSECUTIFS, l'article rejoint la mise a l'ecart
    # normale (`marquer_saute`). Calibre sur la pause de quota Graph a la
    # composition (`visuels_pause_quota_minutes`, 60 min) rapportee a la
    # cadence de publication (10 min, `StartInterval` de l'agent) : le 1er
    # report tombe des le 1er passage en panne, le 7e report tombe donc APRES
    # 6 intervalles de 10 min, soit exactement les 60 min de la pause de
    # quota -- assez pour la laisser se resorber TOUTE SEULE avant
    # d'abandonner (un seuil a 6 aurait bascule des la 50e minute, avant la
    # fin de la pause qu'on cherche justement a laisser passer).
    visuel_differe_max_reports: int = 7

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
    # Cadrage des images du carrousel (toutes sorties en 1080x1350, le format de
    # la cover, parce qu'Instagram impose le ratio du PREMIER element a tous les
    # autres). Une image qu'il faudrait agrandir plus que `upscale_max` fois, ou
    # dont le recadrage jetterait plus de `perte_max` de la surface, est ECARTEE
    # du carrousel plutot que publiee massacree -- voir app/publish/images.py.
    publication_crop_upscale_max: float = 2.2
    publication_crop_perte_max: float = 0.60
    # Fenetres de publication, heure de Paris. L'heure exacte est tiree au hasard
    # dans chaque fenetre, pour ne pas publier comme une horloge.
    publication_fuseau: str = "Europe/Paris"
    # Une fenetre = un carrousel par jour, separees par des virgules -- exactement
    # comme les fenetres reel. Le nombre de posts par jour est donc une ligne de
    # `.env`, jamais une ligne de code. Aucune ne doit chevaucher une fenetre
    # reel ni une autre (slots.FenetresInvalides) : publier_a est unique.
    publication_fenetres: str = "08:30-10:30,14:30-16:30,19:00-20:15"
    # REPLI pour un `.env` d'avant la generalisation, qui ne connait que ce
    # couple : il ne sert que si publication_fenetres est VIDE, pour qu'un
    # reglage existant ne soit pas ignore en silence (voir slots.fenetres).
    publication_fenetre_matin: str | None = None
    publication_fenetre_apres_midi: str | None = None
    # 3 jours x 3 creneaux.
    publication_creneaux: int = 9
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
    # Nombre maximal de tunnels LANCES pour une publication. Ce n'est plus un
    # nombre d'attentes de 90 s : depuis le 2026-10-02 on patiente longuement sur
    # UN sous-domaine (hosting.DELAI_JOIGNABLE_S) et on ne relance que ce qui le
    # merite (tunnel jamais demarre, ou sous-domaine resolu qui ne rend pas 200) --
    # la propagation DNS mesuree (16 a 66 s) rendait chaque reessai contre-productif.
    # Le plafond borne la lecture d'URL (DELAI_URL_S) comme avant.
    publication_tunnel_essais: int = 3
    # Budget TOTAL de patience, toutes tentatives confondues : 3 x 90 s, c'est-a-dire
    # exactement l'ancienne borne (~4,5 min), bien en deca des 90 minutes de retard
    # tolere (publication_retard_max_minutes). Ne pas l'augmenter : c'est lui qui
    # garantit qu'un passage ne mange pas le creneau suivant.
    publication_tunnel_budget_s: int = 270

    # --- Reels automatiques (en plus des carrousels) ---
    # Fenetres propres, heure de Paris, separees par des virgules : une fenetre =
    # un reel par jour. Elles ne doivent chevaucher ni les fenetres carrousel ni
    # entre elles (slots.FenetresInvalides) : publier_a est unique.
    publication_reel_fenetres: str = "11:00-12:30,17:00-18:30,21:00-22:30"
    # 3 jours x 3 reels. 0 desactive les reels sans toucher aux carrousels.
    publication_creneaux_reel: int = 9
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
    # --- Qualite d'encodage, PARTAGEE par le reel et la video de carrousel ---
    # (`app/content/video.py::encodage_meta`).
    #
    # La perte est COMPOSEE : Instagram encode deja la video quand l'artiste la
    # publie, on la reencode, et Instagram la reencode une troisieme fois a la
    # reception. On lui donne donc la meilleure source possible.
    #
    # Sans `-crf`, x264 applique son defaut de distribution web (crf 23) --
    # personne ne l'avait choisi. Mesure du 2026-10-02 sur un vrai reel du
    # pipeline (1080x1920, 23,8 s, 4,3 Mbit/s), VMAF contre la source :
    #
    #   crf implicite 23, audio 128k : 11,80 Mo, VMAF 96,81   (l'ancien defaut)
    #   crf 20, audio 192k           : 14,22 Mo, VMAF 97,56   (+0,75, retenu)
    #   crf 18 preset slow, 192k     : 15,78 Mo, VMAF 98,00   (+1,28, trop cher)
    #
    # 20 prend 60 % du gain pour 60 % du surpoids, a temps d'encodage egal (6 s
    # dans les deux cas : a crf explicite, x264 cherche moins). 192k parce que
    # c'est de la musique.
    encodage_crf: int = 20
    encodage_preset: str = "medium"
    encodage_audio_bitrate: str = "192k"
    # Le garde-fou de POIDS, et c'est la propriete dont la violation coute une
    # ACTU : au-dela de `publication_reel_max_mo`, `preparation_reel.
    # verifier_sortie` leve `PreparationImpossible`, qui EXCLUT l'article a vie.
    # Baisser le crf reduit la marge, donc le garde-fou est explicite : si la
    # sortie depasse, on REENCODE sous un debit moyen vise, calcule depuis le
    # poids vise et la duree MESUREE, au lieu de laisser lever. La degradation
    # n'arrive donc que sur les passages qui, sinon, seraient perdus -- jamais
    # sur le cas nominal, qui ne voit pas ce second encodage.
    # `encodage_poids_marge` paie le depassement de consigne de x264 et les
    # entetes du conteneur.
    encodage_poids_marge: float = 0.90
    # Plancher de debit video : sous cette valeur le plafond calcule n'a plus de
    # sens (une source tres longue et tres chargee). On encode quand meme a ce
    # plancher -- l'alternative est l'exclusion definitive de l'article.
    encodage_debit_min_kbit: int = 300
    # Combien de fois on corrige. x264 DEPASSE sa consigne de debit sur un clip
    # court (~26 % mesure le 2026-10-02 sur 2 s : son controle de debit ne
    # converge pas en 60 frames), d'ou une correction proportionnelle au
    # depassement CONSTATE plutot qu'un calcul auquel on fait confiance. Chaque
    # reprise coute un encodage de plus, mais seulement sur un contenu qui,
    # sinon, serait perdu.
    encodage_reprises_max: int = 3
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
