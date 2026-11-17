# Roadmap, backlog et consignes aux agents développeurs

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Phases

| Phase | Livrable | Gate de fin |
|---|---|---|
| P0 | Réplique visuelle et tokens CSS | Overlay 550×688 documenté |
| P1 | API, Auth, events, ledger, schemas | Tests invariants + contract |
| P2 | 15 écrans admin en paper | Parcours E2E sans ordre réel |
| P3 | Connecteurs et scanner/rug, risk | Sources TTL et rejection tests |
| P4 | Exécuteur paper, backtest, replay | Réconciliation et tests stress |
| P5 | Exploitation/monitoring | SLO/alerts/runbooks éprouvés |
| P6 | Live pilot conditionnel | Double validation, garde-fous, conformité |

## Jalon M1 — Tranche verticale paper Kraken (ADR-008) — en cours

Ce jalon remplace l'enchaînement P0→P4 pour le premier livrable ; il prend une tranche fine de P0, P1, P2, P3 et P4.

| Lot | Contenu | Références |
|---|---|---|
| M1.1 Fondations | modèles (instrument, stratégie/versions, risk policy, bot, session, candidat, décision, assessment, intent, ordre, fill, position, cash movement, ledger, event, audit, rôles) ; outbox ; RBAC métier ; amorçage stratégie + policy + bot `PAPER` | 04-metier, contracts/, ADR-006/007 |
| M1.2 Connecteur Kraken | référentiel `AssetPairs`, top-N USD, WS v2 `instrument/ticker/book/trade`, checksum, fraîcheur, agrégats 1 min, amorçage OHLC | 07-data/01, ADR-002/003 |
| M1.3 Agents | SCANNER (STRAT-01 Kraken), RUG CHECK variante CEX, SIZER (05-strategies/03), EXECUTOR paper, LOGGER, EQUITY + surveillance stop/TP/temps | 06-agents/01, 08-*, ADR-004/005/010 |
| M1.4 API & WS | `/api/v1` : health, bots, commands, strategies (lecture), portfolio, trades, events ; `/ws/v1/terminal` snapshot + deltas + gap | 10-backend/02 |
| M1.5 UI | UI-01 Live Terminal fidèle à la capture 3, animé par les événements réels ; UI-02 minimal (liste bots, START / PAUSE_ENTRIES / RESUME_ENTRIES / EMERGENCY_STOP) | 02-design, 03-ecrans |
| M1.6 Tests | invariants INV-01..07 applicables, QA-01, 03, 04, 05, 06, 07, 09, 10, 13, 16–20 ; tests sans réseau (fixtures Kraken enregistrées) | 12-tests |

**Gate de fin M1.** Un bot `PAPER` tourne 24 h sur données Kraken réelles sans intervention ; chaque trade affiché est reconstructible depuis le journal (candidat → assessment → sizing → intent → fill → sortie) ; ledger réconcilié ; redéploiement en cours de session sans doublon ; suite de tests verte (`npm run typecheck && npm run test && npm run build`, `pytest`).

**Hors M1.** UI-03..15 complets, backtests/replay, analytics, alertes avancées, assistant IA, LIVE.

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
