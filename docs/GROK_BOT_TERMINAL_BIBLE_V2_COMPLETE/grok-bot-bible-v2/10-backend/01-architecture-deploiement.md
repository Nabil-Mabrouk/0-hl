# Architecture backend et modules techniques

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Topologie de référence

Frontend React/TypeScript/Vite + design tokens + store Zustand / query cache → gateway FastAPI (auth, RBAC, REST, WebSocket) → services métiers (strategy, candidate, risk, orders, ledger, analytics, alerting) → bus PostgreSQL outbox / Redis Streams ou NATS à choix d'équipe → PostgreSQL transactions + objet Parquet datasets + Redis cache → connectors DEX/CEX/RPC.

## Séparation des processus

`api`: endpoints, websocket, auth ; `ingestor`: RPC/API sous backpressure ; `feature-worker`: mesures chronologiques ; `strategy-worker`: signaux ; `risk-worker`: contrôles ; `execution-worker`: commandes et confirmations ; `ledger-worker`: écritures ; `scheduler`: rapprochement, agrégation, purge ; `research-worker`: jobs non prioritaires. Le plan de trading critique ne doit pas dépendre du rendering frontend ni de services LLM.

## Contrats et configuration

Environnements `local`, `test`, `paper`, `staging`, `live` ; secrets via secret manager ; feature flags versionnés, migrations Alembic, version OpenAPI exposée ; protocole d'événements `schema_version` et compatibilité. Réaliser un modular monolith initial avec queues et limites clairement définies ; scinder les services uniquement avec justification mesurée.

## Contraintes de latence (cibles initiales non démontrées)

Ingestion source à visibilité scanner P95 < 2s sous disponibilité provider ; décision risk locale P95 < 250ms ; livraison événement UI P95 < 1s ; erreur de réconciliation détectée < 60s après collecte des balances ; 99.9% service pour paper hors maintenances. Mesurer latence de provider séparément de la latence applicative.

## Environnement local

Docker Compose PostgreSQL, Redis, API, workers, frontend, mock-provider et seed fixtures; tests unitaires sans accès réseau, tests d'intégration avec mocks, replay de trois captures dans un mode démo, health/readiness endpoints et observability. Les secrets restent hors dépôt.

## Implémentation sur le châssis gitsky (ADR-007)

La topologie ci-dessus est la **cible logique**. Sur le châssis gitsky, elle est réalisée ainsi :

| Logique V2.0 | Réalisation 0-hl |
|---|---|
| Frontend React/TS/Vite | `frontend/` du châssis (React 19, Vite, Tailwind 4, React Router) ; écrans 0-hl sous `frontend/src/features/<écran>/` (nouveaux fichiers) ; tokens CSS de 02-design dans une feuille propre, sans toucher `theme.css` |
| Gateway FastAPI (auth, RBAC, REST, WS) | service `backend` du châssis ; auth châssis (JWT) + RBAC métier (ADR-006) ; routes `/api/v1/*` et `/ws/v1/terminal` déclarées via `app/domain/routers.py` |
| Bus Redis Streams / NATS | **outbox PostgreSQL** (`domain_event`) + curseurs par consommateur |
| ingestor, feature/strategy/risk/execution/ledger workers, scheduler | **service `worker` du châssis**, boucle continue dans `app/domain/worker_cycle.py` (06-agents « Exécution sur le châssis ») |
| research-worker | reporté (backtests au jalon P4) |
| PostgreSQL + Alembic | base du châssis ; modèles dans `app/domain/models.py` et sous-modules ; migrations Alembic du châssis |
| Parquet / object storage | reporté ; agrégats 1 min en PostgreSQL au premier jalon |
| Redis cache | aucun ; carnets en mémoire du worker |

**Organisation du code backend.** `app/domain/` contient : `models.py`, `routers.py`, `worker_cycle.py` (points d'entrée imposés par le châssis) et des sous-paquets `app/domain/trading/{events,ledger,risk,strategy,agents,venues,api,rbac}`. Aucun fichier sous `app/core/` ou `app/modules/` n'est modifié (contrat AGENTS.md).

**Contrat flotte intouchable.** Forme de `GET /health` (le health métier 0-hl est exposé sous `/api/v1/health`), labels Traefik, noms de conteneurs, structure de `.env`/`.env.local`, `MODULE_FLAGS`. Les nouveaux réglages 0-hl (venue, capital paper, frais…) sont des valeurs en base (configuration versionnée), pas de nouvelles clés `.env` sauf ADR.

**Redéploiement.** Chaque push redémarre `backend` et `worker` : le WebSocket des clients se reconnecte avec `last_sequence` ; le worker reprend depuis ses curseurs ; les ordres paper en vol sont réconciliés au démarrage (INV-04).
