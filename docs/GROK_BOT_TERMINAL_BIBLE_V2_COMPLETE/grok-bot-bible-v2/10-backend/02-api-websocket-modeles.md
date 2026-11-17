# API REST, WebSocket, schémas et erreurs

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Conventions REST

Toutes routes sous `/api/v1`, pagination cursor, filtres validés, retour d'erreur RFC 9457 ou contrat stable, `X-Correlation-ID`, If-Match pour configuration, `Idempotency-Key` pour commandes. Sur commandes asynchrones, retourner HTTP 202 avec `command_id`, pas statut métier réussi.

| Route | Méthode | Rôle | Effet |
|---|---|---|---|
| `/bots` | GET/POST | Observer/Operator | lister/créer instance |
| `/bots/{id}/commands` | POST | Operator | pause/reprise/stop/démarrage |
| `/strategies` | GET/POST | Analyst | liste/brouillon |
| `/strategies/{id}/versions` | GET/POST | Analyst | versions |
| `/strategies/{id}/publish` | POST | Risk Officer | publication auditée |
| `/risk-policies` | GET/POST | Risk Officer | versions de politique |
| `/risk/preview` | POST | Analyst | évaluer sans ordre |
| `/trades` | GET | Observer | filtres métiers |
| `/orders` | GET | Observer | état et fills |
| `/portfolio` | GET | Observer | ledger projection |
| `/events` | GET | Observer | recherche |
| `/data-sources` | GET/PATCH | Admin | santé et réglages |
| `/backtests` | GET/POST | Analyst | jobs de simulation |
| `/alerts` | GET | Operator | incidents |
| `/alerts/{id}/ack` | POST | Operator | acquittement |
| `/admin/users` | GET/POST | Admin | RBAC |

## WebSocket

`/ws/v1/terminal?bot_id=...` avec authentification, snapshot initial et replay depuis `last_sequence`, puis événements deltas. Heartbeat serveur, protocol `hello` (capabilities, seq), `snapshot`, `event`, `gap`, `reconnect_required`. Un retard au-delà de la capacité de replay renvoie `snapshot_required`; jamais de continuité fictive.

## Contrats embarqués

Voir `../contracts/openapi-starter.yaml`, `../contracts/events.schema.json`, `../contracts/strategy.schema.json`, `../contracts/risk-policy.schema.json` et `../contracts/schema.sql`. Ce sont des **bases structurées** et non la totalité des routes/erreurs : générer OpenAPI complet et vérifier en CI. Des champs non définis doivent retourner une erreur explicite plutôt que pass-through silencieux.

## Taxonomie d'erreurs

`VALIDATION_FAILED`, `VERSION_CONFLICT`, `PERMISSION_DENIED`, `DATA_STALE`, `PROVIDER_UNAVAILABLE`, `RISK_LIMIT_EXCEEDED`, `INSUFFICIENT_FUNDS`, `ORDER_UNKNOWN`, `RECONCILIATION_REQUIRED`, `LIVE_NOT_APPROVED`, `STRATEGY_NOT_PUBLISHED`, `RATE_LIMITED`. Messages localisables + log structuré, jamais clé privée ni token d'auth.
