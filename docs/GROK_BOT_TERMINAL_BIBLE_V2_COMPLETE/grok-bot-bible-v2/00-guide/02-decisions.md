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

**Mais :** aucune clé API privée Kraken n'est configurée ni utilisée, l'implémentation privée n'est pas codée et toute commande `START` en `LIVE` retourne `LIVE_NOT_APPROVED` tant qu'une ADR ultérieure n'a pas ouvert le chantier P6. Clés futures : permissions « Query Funds », « Query/Create/Cancel Orders » uniquement, **jamais** « Withdraw Funds ».

## ADR-010 — Paramètres paper par défaut

**Décision.** Capital paper initial **10 000 USD** (écrit au ledger comme `CashMovement` de type dépôt, donc hors PnL) ; frais = palier de base Kraken Spot **0,25 % maker / 0,40 % taker** (paramétrable, à revérifier sur la grille Kraken en vigueur) ; ordres paper exécutés contre le **carnet réel** Kraken au moment de la décision (consommation des niveaux, fills partiels possibles), jamais au dernier prix. Interface en anglais (comme la capture canonique) avec i18n FR/EN prêt ; terminal accessible uniquement après authentification, landing publique inchangée. Aucun LLM dans la boucle de décision ; l'assistant explicatif (06-agents/02) est reporté.
