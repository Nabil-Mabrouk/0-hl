# Roadmap, backlog et consignes aux agents développeurs

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Phases

| Phase | Livrable | Gate de fin |
|---|---|---|
| P0 | Réplique visuelle et tokens CSS | *Overlay 550×688 abandonné (ADR-019)* |
| P1 | API, Auth, events, ledger, schemas | Tests invariants + contract |
| P2 | 15 écrans admin en paper | Parcours E2E sans ordre réel |
| P3 | Connecteurs et scanner/rug, risk | Sources TTL et rejection tests |
| P4 | Exécuteur paper, backtest, replay | Réconciliation et tests stress |
| P5 | Exploitation/monitoring | SLO/alerts/runbooks éprouvés |
| P6 | Live pilot conditionnel | Double validation, garde-fous, conformité |

## Jalon M1 — Tranche verticale paper Kraken (ADR-008) — gate technique franchi (2026-11-19 et 2026-11-20)

Ce jalon remplace l'enchaînement P0→P4 pour le premier livrable ; il prend une tranche fine de P0, P1, P2, P3 et P4.

| Lot | Contenu | Références |
|---|---|---|
| M1.1 Fondations — **livré (local, 2026-11-18)** | modèles (instrument, stratégie/versions, risk policy, bot, session, candidat, décision, assessment, intent, ordre, fill, position, cash movement, ledger, event, audit, rôles) ; outbox ; RBAC métier ; amorçage stratégie + policy + bot `PAPER` | 04-metier, contracts/, **10-backend/03**, ADR-006/007 |
| M1.2 Connecteur Kraken — **livré (local, 2026-11-18)** | référentiel `AssetPairs`, top-N USD, WS v2 `instrument/ticker/book/trade`, checksum, fraîcheur, agrégats 1 min, amorçage OHLC | 07-data/01, **07-data/03**, ADR-002/003 |
| M1.3 Agents — **livré (local, 2026-11-18)** | SCANNER (STRAT-01 Kraken), RUG CHECK variante CEX, SIZER (05-strategies/03), EXECUTOR paper, LOGGER, EQUITY + surveillance stop/TP/temps | 06-agents/01, **06-agents/03**, 08-*, ADR-004/005/010/013 |
| M1.4 API & WS — **livré (local, 2026-11-18)** | `/api/v1` : health, bots, commands, strategies (lecture), portfolio, trades, events ; `/ws/v1/terminal` snapshot + deltas + gap | 10-backend/02, **10-backend/04** |
| M1.5 UI — **livré (local, 2026-11-18), non routé en attente d'ADR-015** | UI-01 Live Terminal fidèle à la capture 3, animé par les événements réels ; UI-02 minimal (liste bots, START / PAUSE_ENTRIES / RESUME_ENTRIES / EMERGENCY_STOP) | 02-design, 03-ecrans, **03-ecrans/M1-terminal-et-bots**, ADR-015 |
| M1.6 Tests — **outillage livré (local, 2026-11-18) ; fenêtre de 24 h à mener** | invariants INV-01..07 applicables, QA-01, 03, 04, 05, 06, 07, 09, 10, 13, 16–20 ; tests sans réseau (fixtures Kraken enregistrées) | 12-tests, **12-tests/02-recette-m1** |

**Gate de fin M1.** Un bot `PAPER` tourne 24 h sur données Kraken réelles sans intervention ; chaque trade affiché est reconstructible depuis le journal (candidat → assessment → sizing → intent → fill → sortie) ; ledger réconcilié ; redéploiement en cours de session sans doublon ; suite de tests verte (`npm run typecheck && npm run test && npm run build`, `pytest`).

**Prérequis de production (ADR-011).** Accès Internet du worker et chaîne de migration `domain` livrés par le template gitsky ; sans eux, M1 est validé en local uniquement.

**Hors M1.** UI-03..15 complets, backtests/replay, analytics, alertes avancées, assistant IA, LIVE.

**Bilan M1 (2026-11-20).** Critères bloquants R-01..R-08 OK à deux reprises ; pertes de flux réduites de 54 à 2 en ~8 h (ADR-017/018), comportement aux heures de pointe US à confirmer. STRAT-01 sans avantage mesurable : entrées en pause, stratégie de démonstration (ADR-020).

## Jalon M2 — Laboratoire de stratégie (ADR-021) — en cours

Objectif : pouvoir juger une stratégie **avant** de la faire tourner, sur des semaines de données, avec le même code que le bot, et la faire évoluer sans écrire de code.

| Lot | Contenu | Références |
|---|---|---|
| M2.1 Données longues — **livré (2026-11-20)** | rétention `hl_trade_minute` portée de 7 à 180 jours ; instantané du carnet (10 niveaux par côté) chaque minute par paire `LIVE` dans `hl_book_snapshot`, rétention 90 jours | **07-data/03 « Instantanés de carnet »**, 10-backend/03 |
| M2.2 Backtest — **livré (local, 2026-11-20)** | rejeu du `DecisionEngine` réel dans un bac à sable SQLite, pas d'une minute, carnets des instantanés M2.1 ; stratégie et policy publiées ou brouillons JSON ; coûts du bot et stress des frais ; découpage temporel, référence, en-tête de reproductibilité ; commande hors production (UI-11 et registre d'expériences dans un lot ultérieur) | **09-recherche/04**, 09-recherche/01–03 |
| M2.3 Versions sans code | création d'une version de stratégie ou de policy (brouillon → publiée, immuable, INV-06) et rattachement d'un bot à une version, par API et commande d'administration, avec audit et validation solo (ADR-006) ; écrans UI-04 (liste et versions), UI-05 (éditeur de paramètres validé par schéma), UI-06 (policy de risque) | 05-strategies, 08-*/01, UI-04/05/06 |
| M2.4 Plusieurs bots | création de bots `PAPER` (un bot = une version de stratégie + une policy + un capital paper), exécution simultanée par le moteur de décision, isolation du capital et des limites par bot ; UI-02 complète | 04-metier, UI-02 |
| M2.5 Suivi | UI-03 (positions et trades par bot, filtres, trace) et UI-12 (résultat net, frais, drawdown, profit factor par stratégie et par version, comparaison à la référence) | UI-03, UI-12 |
| M2.6 Tests | backtest déterministe (même entrée ⇒ même rapport), équivalence décision bot / décision backtest sur fixtures enregistrées, immutabilité des versions, isolation entre bots | 12-tests |

**Gate de fin M2.** Une nouvelle stratégie est décrite, publiée en version, backtestée sur au moins 4 semaines avec un résultat net reproductible jugé sur une période non utilisée pour la choisir, puis lancée sur un bot paper dédié à côté d'un autre bot, sans écrire de code ; les résultats des deux bots se comparent dans UI-12.

**Ordre proposé.** M2.1 d'abord (chaque jour d'attente est un jour d'historique perdu), puis M2.2, M2.3, M2.5, M2.4.

**Hors M2.** LIVE (ADR-009), autres venues que Kraken, assistant IA, alertes avancées.

## Organisation du dépôt visé

Cible logique V2.0 ; réalisation sur le châssis gitsky en [10-backend/01](../10-backend/01-architecture-deploiement.md#implémentation-sur-le-châssis-gitsky-adr-007) (ADR-007). `frontend/src/features/{terminal,bots,strategies,risk,execution,agents,market,data,research,analytics,logs,alerts,security}` ; `backend/app/{api,domain,agents,services,execution,risk,ledger,providers,events,persistence}` ; `contracts/` ; `tests/{unit,contract,integration,e2e,chaos}` ; `docs/` ; `infra/`.

## Règle d'engagement d'un agent développeur

1. Lire les chapitres concernés et les invariants `04-metier/` + contrats.
2. Écrire un mini plan incluant exigences, fichiers, endpoints et tests.
3. Implémenter avec données déterministes de test ; aucun service live caché.
4. Ajouter tests et traces, exécuter typecheck/lint/test + screenshot pour UI.
5. Comparer rendu à la vue PNG, et au canon pour terminal ; consigner les écarts.
6. Livrer PR avec changements de schéma et impact de version si nécessaire.

## Backlog actionnable

- **EPIC-01** Live Terminal — livrer écran `01`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-02** Bots & Sessions — livrer écran `02`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-03** Portfolio & Trades — livrer écran `03`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-04** Strategy Manager — livrer écran `04`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-05** Strategy Editor — livrer écran `05`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-06** Risk Manager — livrer écran `06`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-07** Execution Settings — livrer écran `07`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-08** Agent Manager — livrer écran `08`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-09** Market Scanner — livrer écran `09`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-10** Data Sources — livrer écran `10`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-11** Backtesting & Simulation — livrer écran `11`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-12** Performance Analytics — livrer écran `12`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-13** Event Logs & Audit — livrer écran `13`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-14** Alerts & Monitoring — livrer écran `14`, endpoints, tests loading/error/permission, documentation et capture Playwright.
- **EPIC-15** System & Security — livrer écran `15`, endpoints, tests loading/error/permission, documentation et capture Playwright.

## Décisions ouvertes critiques

Liste V2.0 : [OPEN] Réseau principal (Solana pressenti), venue/exchanges, mode de détention des clés, budget data providers, plafond de latence, cadre juridique, SLA souhaité, définition exacte des spreads DEX, mode de mark-to-market, règles de liquidation d'urgence, validation par une ou deux personnes pour un usage solo, périmètre d'analyses IA, architecture du bus (Redis Streams ou NATS).

**Statut V2.1 :** tranchées par le [registre ADR](../00-guide/02-decisions.md) — réseau/venue (Kraken Spot, ADR-002), détention des clés (aucune au M1, ADR-009), budget data (API publiques Kraken gratuites), mark-to-market (vente simulée sur carnet, 08-*/03), validation solo (ADR-006), périmètre IA (aucun dans la décision, ADR-010), bus (PostgreSQL outbox, ADR-007). **Restent ouvertes :** plafond de latence contractuel, cadre juridique et SLA du LIVE, règles de liquidation d'urgence détaillées.

Ces décisions sont explicitement enregistrées dans le registre ADR ; ne pas les laisser être fixées implicitement par un développeur ou un agent de génération de code.
