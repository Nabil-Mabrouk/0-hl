# Bus d’événements, commandes et réconciliation

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Taxonomie

- Données : `POOL_DISCOVERED`, `TOKEN_METRICS_UPDATED`, `DATA_STALE`, `PROVIDER_DEGRADED`.
- Candidats : `CANDIDATE_DETECTED`, `SIGNAL_EVALUATED`, `RISK_STARTED`, `RISK_PASSED`, `RISK_REJECTED`.
- Ordres : `ORDER_INTENT_CREATED`, `ORDER_APPROVED`, `ORDER_SUBMITTED`, `ORDER_ACKED`, `ORDER_PARTIAL_FILL`, `ORDER_FILLED`, `ORDER_REJECTED`, `ORDER_UNKNOWN`.
- Portefeuille : `POSITION_OPENED`, `POSITION_REDUCED`, `POSITION_CLOSED`, `LEDGER_POSTED`, `EQUITY_UPDATED`, `RECONCILIATION_FAILED`.
- Système : `BOT_PAUSED`, `BOT_RESUMED`, `ALERT_OPENED`, `ALERT_ACKED`, `STRATEGY_PUBLISHED`, `POLICY_UPDATED`.

## Enveloppe normative

Chaque événement a `schema_version`, `event_id`, `correlation_id`, `causation_id`, `aggregate_type`, `aggregate_id`, `aggregate_sequence`, `occurred_at`, `received_at`, `source`, `payload`, `checksum`. La consommation ignore les duplicatas d'`event_id`, ordonne par aggregate_sequence ou reconstruit depuis snapshot. Toute migration de schéma est testée pour les anciens événements.

## Commandes

Une commande n'est pas un événement réussi : `PauseBotCommand` peut produire `BOT_PAUSED` ou `COMMAND_REJECTED`. L'API retourne un `command_id` et un état pending. L'UI ne déduit jamais succès de l'HTTP 202 seul. Chaque commande peut avoir `deadline_at`, `idempotency_key`, `actor`, `reason`.

## Patterns de fiabilité

Outbox transactionnelle DB+bus, inbox de déduplication, retry borné avec backoff, DLQ inspectable, snapshot périodique, contrôles de séquence, horloge monotone pour timeout, reconciliation périodique venue vs ledger. Pour sortie d'urgence, privilégier les ordres sécurisés côté venue lorsque disponibles et vérifier leur mise en place effective.
