# Registre des décisions d'architecture (ADR)

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Ce registre tranche les points `[OPEN]` de la V2.0 et prime sur toute formulation contraire des autres chapitres (voir la hiérarchie dans [00-lecture](00-lecture.md)). Une décision n'est modifiée que par une nouvelle ADR qui la remplace explicitement (`Remplacée par ADR-xxx`) ; on ne réécrit jamais une ADR actée.

| ID | Décision | Statut | Date |
|---|---|---|---|
| ADR-000 | La documentation est la source unique de vérité | Actée | 2026-11-17 |
| ADR-001 | Nom du produit : **0-hl** | Actée | 2026-11-17 |
| ADR-002 | Première venue : Kraken Spot, adaptateurs extensibles | Actée | 2026-11-17 |
| ADR-003 | Univers : paires cotées USD, top-N par volume | Actée | 2026-11-17 |
| ADR-004 | RUG CHECK devient un contrôle de qualité de marché sur CEX | Actée | 2026-11-17 |
| ADR-005 | Première stratégie : STRAT-01 Volume Spike Momentum | Actée | 2026-11-17 |
| ADR-006 | Rôles métier en table de domaine, mode solo | Actée | 2026-11-17 |
| ADR-007 | Monolithe modulaire PostgreSQL sur le châssis gitsky | Actée | 2026-11-17 |
| ADR-008 | Premier jalon : tranche verticale paper | Actée | 2026-11-17 |
| ADR-009 | LIVE préparé dans la conception, désactivé | Actée | 2026-11-17 |
| ADR-010 | Paramètres paper par défaut | Actée | 2026-11-17 |
| ADR-011 | Prérequis châssis corrigés en amont (gitsky) | Actée — livrée par le template (réseau + chaîne `domain`), `copier update` à appliquer | 2026-11-18 |
| ADR-012 | Dépendance `websockets` et environnement de développement | Actée | 2026-11-18 |
| ADR-013 | Démarrage automatique du bot d'amorçage, PAPER uniquement | Actée | 2026-11-18 |
| ADR-014 | STRAT-01 v0.2.0 : planchers d'activité ; ré-amorçage du bot jamais démarré | Actée | 2026-11-18 |
| ADR-015 | Point d'extension des routes frontend fourni par le template gitsky | Actée — livrée (châssis fd9a22b) | 2026-11-18 |
| ADR-016 | Live Terminal pleine largeur par défaut ; réplique 550×688 en option | Actée — option retirée par ADR-019 | 2026-11-18 |
| ADR-017 | Flux Kraken : mesure du retard, carnet profondeur 10, réception allégée | Actée | 2026-11-19 |
| ADR-018 | Carnet d'ordres incrémental (checksum sans reformatage) | Actée | 2026-11-19 |
| ADR-019 | Suppression de la réplique 550×688 et de la mesure de fidélité à la capture 3 | Actée | 2026-11-19 |

---

## ADR-000 — La documentation est la source unique de vérité

**Décision.** Le présent dossier (`docs/GROK_BOT_TERMINAL_BIBLE_V2_COMPLETE/grok-bot-bible-v2/`) est l'unique référence du produit. Toute décision, tout changement de périmètre ou tout écart découvert pendant l'implémentation est **d'abord** consigné ici (nouvelle ADR + chapitres et contrats concernés), **puis** implémenté.

**Conséquences.** Le code suit la documentation ; en cas de divergence code/doc, la doc prévaut ou est amendée avant toute modification du code. Une PR qui modifie un comportement métier sans modification documentaire correspondante est incomplète (voir « Définition de terminé »).

## ADR-001 — Nom du produit : 0-hl

**Décision.** Le produit s'appelle **0-hl** (domaine `0-hl.com`, projet gitsky `0-hl`). La dénomination « Grok Bot Terminal » ne désigne plus que le logiciel tiers observé sur les captures `[OBS]`.

**Conséquences.** Interface, titres, contrats, en-têtes et textes publics utilisent « 0-hl ». Les captures originales ne sont pas retouchées : elles restent des preuves visuelles. Le nom de dossier `GROK_BOT_TERMINAL_BIBLE_V2_COMPLETE` est conservé pour la stabilité des chemins.

## ADR-002 — Première venue : Kraken Spot, adaptateurs extensibles

**Contexte.** La V2.0 visait principalement des tokens DEX récemment listés (Solana pressenti). L'opérateur ne peut trader que sur **Kraken**.

**Décision.** La première et unique venue implémentée est **Kraken Spot** (pas de Futures, pas de marge, positions **long-only**). Les données de marché proviennent des API publiques Kraken (REST + WebSocket v2), voir [07-data/01](../07-data/01-connecteurs-et-normalisation.md). Toute venue passe par l'adaptateur commun défini en [08-risque-execution/02](../08-risque-execution/02-execution-engine.md) ; ajouter une autre CEX ou une DEX consiste à écrire un nouvel adaptateur (et, pour une DEX, réactiver le RUG CHECK on-chain, ADR-004), sans modifier les agents.

**Conséquences.** L'identité d'actif devient `venue_id + venue_symbol` pour une CEX (`chain_id + contract_address` reste la règle pour une DEX). Les chapitres DEX (07-data/02, STRAT-03, STRAT-08) restent valides mais **hors périmètre** tant qu'aucune venue DEX n'est ajoutée.

## ADR-003 — Univers : paires cotées USD, top-N par volume

**Décision.** Le SCANNER surveille les paires Spot Kraken cotées **USD** (`quote = ZUSD`/`USD`), au statut `online`, classées par volume 24h converti en USD ; on retient les **N premières** (défaut `N = 40`, paramètre de la stratégie). La liste est recalculée périodiquement (défaut toutes les 60 min) ; une paire sortie de l'univers ne reçoit plus de nouvelle entrée mais ses positions restent supervisées. Les stablecoins contre USD (USDT/USD, USDC/USD…) et les paires fiat/fiat sont exclus. La comptabilité paper est tenue en **USD**.

## ADR-004 — RUG CHECK devient un contrôle de qualité de marché sur CEX

**Contexte.** Sur une CEX régulée, LP lock, dev wallet et bundled launch n'ont pas de sens.

**Décision.** L'agent conserve son emplacement, son libellé visuel `RUG CHECK` dans le terminal canonique (fidélité capture 3) et sa sémantique `PASS/FAIL/UNKNOWN` par critère, mais sur une venue CEX il évalue la **tradabilité** : statut de paire, spread, profondeur du carnet pour la taille d'entrée **et** de sortie, taille/coût minimum d'ordre, fraîcheur des données, mouvement de prix anormal, cooldown. Les critères exacts sont en [06-agents/01](../06-agents/01-architecture-agents.md#rug-check--variante-cex-adr-004). Le contrôle anti-rug on-chain (07-data/02) est conservé comme spécification de la **variante DEX**, activée par adaptateur.

## ADR-005 — Première stratégie : STRAT-01 Volume Spike Momentum

**Décision.** La tranche verticale exécute de bout en bout **STRAT-01** adaptée à Kraken (voir [05-strategies/02](../05-strategies/02-catalogue-strategies.md#strat-01--variante-kraken-spot-adr-005)), avec l'exemple [`examples/strategy-volume-spike.json`](../examples/strategy-volume-spike.json). Les autres stratégies restent au catalogue en `RESEARCH_ONLY`.

## ADR-006 — Rôles métier en table de domaine, mode solo

**Contexte.** Le châssis gitsky impose son énumération `UserRole` (`anonymous/waitlist/user/premium/admin`) dans `app/core`, non modifiable.

**Décision.** Les cinq rôles métier (Observer, Analyst, Operator, Risk Officer, Admin) sont stockés dans une table de domaine `trading_role_assignment (user_id, role, granted_by, granted_at, revoked_at)` rattachée aux utilisateurs du châssis ; un utilisateur peut cumuler plusieurs rôles. Le RBAC métier est vérifié côté backend sur chaque route `/api/v1`. Un `admin` châssis sans rôle métier n'a **aucun** droit trading implicite, sauf l'amorçage : le premier `admin` châssis reçoit tous les rôles métier à l'initialisation (événement d'audit).

**Mode solo.** L'opérateur unique cumule les rôles. Les actions qui exigent une double validation en multi-utilisateurs (passage LIVE, abaissement d'une protection, changement de compte d'exécution) exigent en solo : **confirmation explicite**, **délai de révision** (défaut 10 min entre demande et confirmation), **MFA** lorsqu'il est disponible, et **audit**. On ne simule jamais une seconde personne. Le passage en multi-utilisateurs active la règle « deux rôles distincts » sans migration de données.

## ADR-007 — Monolithe modulaire PostgreSQL sur le châssis gitsky

**Contexte.** La V2.0 propose Redis Streams/NATS et ~9 processus. Le châssis fournit FastAPI + PostgreSQL + un service `worker` ; ajouter des services implique de modifier `docker-compose.yml` (fichier châssis).

**Décision.**
- **Pas de Redis ni de NATS.** Le bus est une **outbox transactionnelle PostgreSQL** (table `domain_event` + curseurs de consommation par agent), conforme au « modular monolith initial » de [10-backend/01](../10-backend/01-architecture-deploiement.md).
- **Processus :** `backend` (API REST `/api/v1`, WebSocket `/ws/v1/terminal`, auth) et `worker` (châssis) qui exécute les six agents. `run_cycle` de `app/domain/worker_cycle.py` tourne en **boucle interne continue** et vérifie `stop_requested` entre chaque étape ; tout l'état est en base, donc reprise idempotente après chaque redéploiement (chaque `git push` redémarre le worker).
- **Emplacement du code :** tout le code métier vit sous `app/domain/` (sous-paquets autorisés) et dans de nouveaux fichiers frontend ; aucun fichier châssis n'est modifié sans ADR dédiée. La correspondance avec l'organisation cible de la V2.0 est en [10-backend/01](../10-backend/01-architecture-deploiement.md#implémentation-sur-le-châssis-gitsky-adr-007).
- **Contrat flotte intouchable :** `GET /health`, labels Traefik, noms de conteneurs, structure de `.env`, `MODULE_FLAGS`.

**Réversibilité.** Si une mesure démontre un besoin (latence, débit), une ADR ultérieure pourra introduire un bus dédié ; les agents consomment déjà via une interface de bus abstraite.

## ADR-008 — Premier jalon : tranche verticale paper

**Décision.** Plutôt que P0 (réplique visuelle seule), le premier jalon livre une **tranche verticale paper** sur données réelles Kraken : ingestion → SCANNER → RUG CHECK (qualité de marché) → SIZER → EXECUTOR paper → LOGGER → EQUITY → ledger → terminal UI-01 animé par de vrais événements, plus un UI-02 minimal (bots, démarrer/pause/E-stop). Le détail et les critères de fin sont dans [13-roadmap](../13-roadmap/01-plan-execution.md).

## ADR-009 — LIVE préparé dans la conception, désactivé

**Décision.** Le trading réel sur Kraken doit rester **possible ultérieurement sans refonte**. Dès le premier jalon :
- l'adaptateur Kraken expose l'interface complète (`submit`, `get_order`, `cancel_order`, `get_fills`, `get_balances`) ; l'implémentation **paper** et l'implémentation **privée** partagent la même interface ;
- les ordres portent dès maintenant `client_order_id` (mappé sur `cl_ord_id` Kraken) et `intent_id` déterministe ;
- `environment ∈ {DEMO, PAPER, LIVE}` existe dans le modèle, les gates (ADR-006, 12-tests « Go/No-Go LIVE ») sont modélisés ;
- les secrets sont référencés via `CredentialRef`, jamais stockés en clair ni exposés au frontend.

**Mais :** aucune clé API privée Kraken n'est configurée ni utilisée, l'implémentation privée n'est pas codée et toute commande `START` en `LIVE` retourne `LIVE_NOT_APPROVED` tant qu'une ADR ultérieure n'a pas ouvert le chantier P6. Clés futures : permissions « Query Funds », « Query/Create/Cancel Orders » uniquement, **jamais** « Withdraw Funds », et **restriction obligatoire à l'adresse IP du serveur** de production (exigence du propriétaire du châssis, 2026-11-18).

## ADR-010 — Paramètres paper par défaut

**Décision.** Capital paper initial **10 000 USD** (écrit au ledger comme `CashMovement` de type dépôt, donc hors PnL) ; frais = palier de base Kraken Spot **0,25 % maker / 0,40 % taker** (paramétrable, à revérifier sur la grille Kraken en vigueur) ; ordres paper exécutés contre le **carnet réel** Kraken au moment de la décision (consommation des niveaux, fills partiels possibles), jamais au dernier prix. Interface en anglais (comme la capture canonique) avec i18n FR/EN prêt ; terminal accessible uniquement après authentification, landing publique inchangée. Aucun LLM dans la boucle de décision ; l'assistant explicatif (06-agents/02) est reporté.

## ADR-011 — Prérequis châssis corrigés en amont (gitsky)

**Contexte.** Deux lacunes du châssis généré (`_commit 2f53680`) empêchent M1 de fonctionner en production :
1. **Pas d'accès Internet pour le worker.** Le service `worker` n'est relié qu'à `internal-net`, déclaré `internal: true` : il ne peut joindre ni `api.kraken.com` ni `ws.kraken.com`, alors qu'il héberge le flux Kraken et les six agents (ADR-007).
2. **Pas de chaîne de migration pour `app/domain`.** Les tables métier ne sont créées que par `create_all` en `development` ; en production, le service `migrate` (`scripts/migrate.py`, `alembic.ini`) n'applique que les chaînes core et modules.

**Décision.** Les deux points sont corrigés **dans le template gitsky** (par le propriétaire du châssis), puis propagés ici par `copier update`. Aucun patch local de `docker-compose.yml`, `alembic.ini` ou `scripts/migrate.py`.

Besoin exprimé au template :
- un service `worker` capable de sortir vers Internet (réseau de sortie dédié, sans exposition Traefik), au moins quand `MODULE_WORKER` est actif ;
- une chaîne Alembic `domain` (ex. `alembic/domain`, table de version propre), appliquée **en dernier** par `scripts/migrate.py`, avec un emplacement de révisions appartenant au projet (non réécrit par `copier update`).

**Suivi (2026-11-18).**
- *Accès Internet du worker* : **livré par le template.** Après `copier update`, le service `worker` doit être relié à `internal-net` **et** `egress-net` ; le redéploiement crée `egress-net`, et le worker peut alors joindre `api.kraken.com` et `ws.kraken.com`. Vérification obligatoire dans `docker-compose.yml` après la mise à jour.
- *Chaîne de migration `domain`* : **livrée par le template** (`alembic/domain/`, appliquée par le service `migrate`). Procédure obligatoire : après `copier update`, à **chaque** modification de `app/domain/models.py` ou des modèles qu'il importe, générer une révision avec `alembic -n domain revision --autogenerate -m "<description>"`, **relire le script généré** (types `Dec`/`UtcDateTime`, contraintes nommées, ordre des tables) avant tout push, et livrer la révision dans le même commit que le changement de modèle (10-backend/03 « Migrations »).
- *Ordre de livraison M1.1* : `copier update` poussé d'abord (sans code métier), puis code M1.1 + révision initiale `domain` dans un même push, pour que `migrate` crée les tables avant le démarrage du worker.

**Conséquences.** Le développement de M1 se poursuit en local (`create_all` en `development`, ADR-012). Les modèles de domaine sont écrits pour être migrables (pas de dépendance à `create_all`, conventions de nommage explicites). Les révisions Alembic du domaine seront produites dès que la chaîne existera. **Le déploiement en production de M1 est bloqué** jusqu'à la propagation du template ; d'ici là, un push ne doit pas activer le flux Kraken en production (le worker démarre sans erreur fatale si le réseau est injoignable et signale `PROVIDER_UNAVAILABLE`).

## ADR-012 — Dépendance `websockets` et environnement de développement

**Décision.**
- Ajout de la dépendance Python `websockets` (épinglée **`websockets==17.4`**, la version déjà résolue par `uvicorn[standard]==0.51.0` du châssis ; ligne additive dans `requirements.txt`) pour le client Kraken WebSocket v2 ; `docker compose build` local avant tout push qui la contient (AGENTS.md).
- Développement local via `docker-compose.dev.yml` du châssis (backend sur SQLite + frontend ; **pas** de PostgreSQL ni de worker dans ce compose — précisé en [10-backend/03](../10-backend/03-modele-donnees-m1.md#environnement-de-développement-correction-dadr-012)) avec un `.env` local créé depuis les exemples, **jamais versionné** ; worker lancé localement ; PostgreSQL jetable pour les tests marqués `postgres`.
- Les tests (`pytest`) n'accèdent jamais au réseau : le flux Kraken est remplacé par des **fixtures enregistrées** (messages WS v2 et réponses REST) rejouées par un faux transport. Un test d'intégration optionnel contre l'API publique réelle peut exister, désactivé par défaut.
- Le worker exécute `run_cycle` comme une **boucle longue** qui ne rend la main qu'à l'arrêt (`stop_requested`) ; l'audit châssis `WorkerRun` montre donc une exécution par démarrage du worker, et la santé fine des agents est exposée par le domaine (`/api/v1/health`, UI-08).

## ADR-013 — Démarrage automatique du bot d'amorçage, PAPER uniquement

**Contexte.** Au jalon M1.3, les agents peuvent trader en paper, mais l'API (M1.4) et l'écran (M1.5) qui permettent d'envoyer `START` n'existent pas encore.

**Décision.** Le worker émet lui-même une commande `START` (acteur `system:worker`, motif « ADR-013 démarrage automatique PAPER », auditée, clé d'idempotence `autostart:<bot_id>`) pour un bot qui remplit **toutes** les conditions : `environment = PAPER`, statut `READY`, et **aucune session n'a jamais existé** pour ce bot. La commande suit exactement le même traitement qu'une commande d'opérateur.

**Conséquences.** Le démarrage automatique ne se produit qu'**une fois** dans la vie d'un bot : un bot mis en pause, arrêté ou dégradé par la suite n'est jamais relancé automatiquement, y compris après un redéploiement. Un bot `LIVE` n'est jamais démarré automatiquement (ADR-009). Cette ADR pourra être retirée lorsque l'écran UI-02 permettra le démarrage manuel.

## ADR-014 — STRAT-01 v0.2.0 : planchers d'activité ; ré-amorçage du bot jamais démarré

**Constat (essai réel M1.3, 2026-11-18).** Les données sont exactes (chaque minute `live` est identique à la bougie OHLC Kraken), mais STRAT-01 v0.1.0 déclenche sur du bruit : sur les paires peu actives, la médiane des fenêtres de référence est quasi nulle, et quelques trades (ex. SUI/USD : 2 124 USD en 11 trades) produisent des « spikes » de 18 à 72× avec une part acheteuse de 100 %. Calibration sur 12 h réelles (35 paires) : sans plancher, 63 fenêtres « spike ≥ 4 » par heure ; avec ≥ 50 000 USD et ≥ 40 trades par fenêtre de 5 min, 15 par heure (avant filtres d'achat et de prix).

**Décision.**
- Nouvelle version **STRAT-01 0.2.0** = 0.1.0 + deux conditions : `window_notional_usd ≥ 50000` (USD, fenêtre courante `live`) et `window_trade_count ≥ 40`. Un ordre paper type (~1 300 USD) reste sous 3 % du flux de la fenêtre. Seuils **expérimentaux**, révisables par nouvelle version.
- La version 0.1.0 publiée reste immuable (INV-06).
- **Ré-amorçage** : l'amorçage publie la version d'exemple courante ; le bot d'amorçage qui **n'a jamais eu de session** est relié à cette version (audit `SEED_REBIND`). Un bot ayant déjà eu une session n'est jamais modifié par l'amorçage.

## ADR-015 — Point d'extension des routes frontend fourni par le template gitsky

**Contexte.** Le backend dispose d'un point d'extension (`app/domain/routers.py`, variables `*_router`), pas le frontend : les routes sont déclarées dans `frontend/src/App.tsx`, fichier châssis réécrit par `copier update`.

**Décision.** Comme pour ADR-011, le point d'extension est ajouté **dans le template gitsky**. Besoin exprimé :
- un fichier projet `frontend/src/domain/routes.tsx`, **jamais réécrit** par `copier update` (`_skip_if_exists`), exportant `domainRoutes` (fragment React de `<Route>` relatifs à l'`AppShell`) ;
- `App.tsx` l'importe et le place dans le `<Routes>` de l'`AppShell` (sous `PasswordGate`) ; le scaffold initial fournit un `domainRoutes` vide.

**Conséquences.** Les écrans M1.5 sont développés et testés dans `frontend/src/features/` et déclarés dans `frontend/src/domain/routes.tsx` ; ils deviennent accessibles (`/terminal`, `/bots`) dès la propagation du template. Aucun patch local de `App.tsx`.

## ADR-016 — Live Terminal pleine largeur par défaut ; réplique 550×688 en option

**Contexte.** Livré au M1.5 conformément à 02-design/03, le Live Terminal était la réplique exacte de la capture 3 : un canevas de 550×688 px (textes de 6 à 9 px) mis à l'échelle. Jugé par l'opérateur (2026-11-18) inutilisable en usage réel : dimensions non standard, texte minuscule, écran largement vide sur un poste de bureau.

**Décision.**
- La vue **par défaut** de UI-01 est une **disposition pleine largeur** sur grille responsive standard (conçue pour 1440×900, lisible : textes de 12 à 14 px, chiffres clés de 24 à 32 px), qui reprend le **langage visuel** de la capture 3 (palette, typographie monospace, colonne des agents, flux scan → check → size → exec → log, trade, `tail -f`, résultat, journal, bandeau sombre) et **exactement les mêmes données**.
- La **réplique exacte 550×688** reste disponible par un sélecteur « Reference canvas » (mémorisé par navigateur) et demeure la référence de la mesure de fidélité à la capture 3 (02-design/03, M1.6).
- Remplace, pour la vue par défaut, la règle de 02-design/03 « l'écran canonique ne doit pas être remplacé par un dashboard générique ».

## ADR-017 — Flux Kraken : mesure du retard, carnet profondeur 10, réception allégée

**Constat (recette du 2026-11-19).** 85 coupures du WebSocket en ~36 h, toutes `CONNECTION_LOST` avec le code `1011 keepalive ping timeout` côté client, en rafales toutes les 1 à 3 min aux heures actives (≈ 17 h UTC). Cause : le worker consomme le flux moins vite que Kraken ne l'émet (≈ 3 300 messages/s mesurés hors pointe pour 35 paires en profondeur 25) ; `websockets` 17 cesse alors de lire le socket, la réponse au ping reste derrière l'arriéré et dépasse 20 s. Défaut de sécurité associé : pendant l'arriéré, des carnets en retard de plusieurs secondes restaient `LIVE` (contraire à INV-05).

**Décision.**
1. **Mesure du retard** : pour chaque message `book`/`trade`, `retard = réception − horodatage Kraken`. Une paire dont le retard dépasse **2 s** passe `STALE` (motif `FEED_LAG`) et redevient `LIVE` au premier carnet valide reçu avec moins de **1 s** de retard. Un retard global au-delà de **15 s** provoque une **reconnexion volontaire** (motif `FEED_LAG`), qui vide l'arriéré avant le délai du ping.
2. **Carnet en profondeur 10** au lieu de 25 (le checksum ne porte que sur 10 niveaux ; −23 % de messages mesurés). Les contrôles de liquidité (RUG CHECK, `liquidity_cap` du SIZER) ne voient plus que 10 niveaux : plus prudents, jamais plus permissifs.
3. **Réception allégée** : plus de création de tâches asyncio par message ; le chien de garde de 5 s est un délai sur la réception elle-même.
4. **Observabilité** : le résumé du flux (toutes les 5 min) indique le débit (messages/s), le coût moyen de traitement par message (µs) et le retard maximal observé ; chaque passage `FEED_LAG` est journalisé (WARNING).

**Conséquences.** Une nouvelle fenêtre de recette de 24 h est nécessaire après ce correctif. Si le retard persiste aux heures de pointe malgré ces mesures, l'étape suivante est un processus d'ingestion séparé (07-data/03, réversibilité d'ADR-007).

## ADR-018 — Carnet d'ordres incrémental (checksum sans reformatage)

**Constat (production, 2026-11-19, après ADR-017).** Horloge du serveur synchronisée (NTP, écart < 1 s avec Kraken) : les `FEED_LAG` sont dus à la charge. Le traitement coûte ~200 µs par message sur le VPS (≈ 4,5× ce PC) pour 2 500 à 3 700 messages/s, avec des pics de retard de 7 à 9,5 s aux quarts d'heure ronds (rafales d'activité de marché). Profil : le **checksum représente ~67 % du coût**, car chaque message reformate les 20 meilleurs niveaux (40 décimaux) alors qu'en général un seul a changé.

**Décision.** Le carnet devient incrémental :
- le texte de checksum de chaque niveau (prix et quantité formatés selon la précision de la paire, point et zéros de tête retirés) est calculé **une fois**, à l'insertion ou à la modification du niveau, et mis en cache ; un changement de précision (canal `instrument`) invalide le cache ;
- les prix de chaque côté sont tenus **triés en continu** (insertion et suppression par dichotomie) au lieu d'un tri complet à chaque message ;
- l'algorithme du checksum est inchangé : il doit rester valide à 100 % sur la session Kraken réelle enregistrée (T-M1.2-01).

**Suite.** Si les résumés du flux montrent encore des retards au-delà de 2 s aux heures chargées, l'étape suivante reste un processus d'ingestion séparé (ADR-017).

## ADR-019 — Suppression de la réplique 550×688 et de la mesure de fidélité à la capture 3

**Décision (opérateur, 2026-11-19).** La réplique exacte 550×688 de la capture 3 n'est plus maintenue : le sélecteur « Reference canvas » d'ADR-016 et son code sont retirés, la vue pleine largeur est l'unique vue de UI-01. L'exigence de mesure de fidélité (captures Playwright 550×688 superposées à la capture 3, 02-design/03) est **abandonnée**.

**Conséquences.** La capture 3 reste une **inspiration visuelle** (palette, typographie monospace, organisation agents → flux → trade → journal), sans contrôle pixel. Remplace l'option de la réplique d'ADR-016 et les passages de 02-design/03, UI-01, 03-ecrans/M1, 12-tests/02 et du jalon P0 qui en dépendaient. La préférence de vue mémorisée dans le navigateur n'a plus d'objet.
