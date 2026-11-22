# Modèle de données et fondations M1.1

> **0-hl — Bible V2.1 | 18 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Ce chapitre fixe le **modèle physique** du lot M1.1 ([13-roadmap](../13-roadmap/01-plan-execution.md)). Il concrétise [04-metier](../04-metier/01-entites-et-etats.md) et remplace, pour 0-hl, le SQL conceptuel de [`contracts/schema.sql`](../contracts/schema.sql) (qui reste une illustration). Toute modification de table passe d'abord par ce chapitre (ADR-000).

## Conventions techniques

- **Emplacement.** Code sous `app/domain/trading/` ; `app/domain/models.py` importe les modèles pour que le châssis les enregistre. Tables préfixées **`hl_`** (évite toute collision avec les tables du châssis et des modules).
- **Double cible SQL.** Le domaine fonctionne sur **PostgreSQL** (production) et **SQLite** (développement et tests, défaut du châssis). Les types portables ci-dessous sont obligatoires ; tout comportement propre à PostgreSQL (verrou consultatif) est isolé derrière un test de dialecte.
- **Identifiants.** UUID v4 (`Uuid`) pour les entités ; identifiants lisibles pour les référentiels : `hl_instrument.id = "kraken_spot:BTC/USD"`, `hl_strategy.id = "volume-spike"`, `hl_risk_policy.id = "paper-default"`.
- **Montants et quantités.** Type `Dec` : `NUMERIC(38,18)` sur PostgreSQL, `TEXT` (chaîne décimale canonique) sur SQLite ; lecture/écriture exclusivement en `decimal.Decimal`. Un `float` reçu par ce type lève une erreur.
- **Horodatages.** Type `UtcDateTime` : toujours *timezone-aware* UTC en lecture comme en écriture (SQLite stocke sans fuseau, le type rétablit UTC). Une date naïve en écriture lève une erreur.
- **JSON.** `JSON` générique, `JSONB` sur PostgreSQL.
- **Hash de configuration.** `sha256` hexadécimal du JSON canonique (`sort_keys=True`, séparateurs `(",", ":")`, UTF-8, décimaux en chaînes).
- **Validation.** Les configurations de stratégie et de politique de risque sont validées par des modèles Pydantic **stricts** (`extra="forbid"`), alignés sur [`contracts/strategy.schema.json`](../contracts/strategy.schema.json) et [`contracts/risk-policy.schema.json`](../contracts/risk-policy.schema.json) ; un test de contrat vérifie que les champs des modèles et des schémas coïncident. Le champ `status` d'une `StrategyVersion` est porté par la **colonne** `status` : il est retiré du JSON `config` stocké et **exclu du hash**, de sorte que la publication ne change pas `config_sha256`.

## Tables

### Gouvernance

| Table | Colonnes principales | Règles |
|---|---|---|
| `hl_role_assignment` | `id`, `user_id → users.id`, `role` (`OBSERVER`, `ANALYST`, `OPERATOR`, `RISK_OFFICER`, `ADMIN`), `granted_by` (nullable = amorçage), `granted_at`, `revoked_at`, `revoked_by` | une attribution active = `revoked_at IS NULL` ; au plus une attribution active par `(user_id, role)` |
| `hl_audit_event` | `id`, `actor` (`user:<id>` ou `system:<composant>`), `action`, `entity_type`, `entity_id`, `diff` (JSON), `reason`, `self_approved` (bool), `correlation_id`, `occurred_at` | append-only (INV-07) : aucune mise à jour ni suppression |

### Journal d'événements (outbox, ADR-007)

| Table | Colonnes principales | Règles |
|---|---|---|
| `hl_domain_event` | `position` (entier auto-incrémenté, **séquence globale du flux**), `event_id` (UUID unique), `schema_version`, `event_type`, `aggregate_type`, `aggregate_id`, `aggregate_sequence`, `correlation_id`, `causation_id`, `bot_id` (nullable, filtre WS), `occurred_at`, `received_at`, `source`, `payload` (JSON), `checksum` | unique `(aggregate_type, aggregate_id, aggregate_sequence)` ; append-only |
| `hl_consumer_cursor` | `consumer` (PK, ex. `agent:sizer`), `position`, `updated_at` | avance dans la même transaction que les écritures du consommateur |

**Ordre de visibilité.** Un consommateur lit `position > curseur` par ordre croissant. Pour qu'aucun événement ne soit « dépassé » par un autre validé plus tôt, chaque ajout prend un **verrou de transaction** global d'écriture du journal (`pg_advisory_xact_lock` sur PostgreSQL ; SQLite sérialise déjà les écritures) : l'ordre des `position` est alors l'ordre de validation. `aggregate_sequence` = dernier numéro de l'agrégat + 1, calculé sous ce même verrou. `checksum` = sha256 du JSON canonique de `(event_type, aggregate_type, aggregate_id, aggregate_sequence, payload)`. Un ajout avec un `event_id` déjà présent est ignoré (idempotence) et renvoie l'événement existant.

### Commandes

| Table | Colonnes principales | Règles |
|---|---|---|
| `hl_command` | `id`, `bot_id`, `command` (`START`, `PAUSE_ENTRIES`, `RESUME_ENTRIES`, `STOP`, `EMERGENCY_STOP`), `idempotency_key` (unique), `actor`, `reason`, `status` (`PENDING`, `COMPLETED`, `REJECTED`), `rejection_code`, `requested_at`, `deadline_at`, `processed_at`, `result` (JSON) | une même `Idempotency-Key` renvoie la commande existante, jamais une seconde exécution |

### Référentiels et configuration versionnée

| Table | Colonnes principales | Règles |
|---|---|---|
| `hl_instrument` | `id`, `venue` (`kraken_spot`), `symbol` (`BTC/USD`), `rest_name`, `altname`, `base`, `quote`, `pair_decimals`, `lot_decimals`, `tick_size`, `ordermin`, `costmin`, `status`, `in_universe`, `volume_24h_usd`, `updated_at` | rempli par M1.2 |
| `hl_strategy` | `id` (slug), `name`, `created_by`, `created_at` | |
| `hl_strategy_version` | `id`, `strategy_id`, `version`, `status` (`DRAFT`, `IN_REVIEW`, `APPROVED`, `PUBLISHED`, `DEPRECATED`), `config` (JSON validé), `config_sha256`, `created_by`, `created_at`, `published_by`, `published_at` | unique `(strategy_id, version)` ; `config` modifiable **uniquement** en `DRAFT` ; `PUBLISHED → DRAFT` interdit |
| `hl_risk_policy` | `id` (slug), `name`, `created_at` | |
| `hl_risk_policy_version` | `id`, `policy_id`, `version`, `status` (`DRAFT`, `PUBLISHED`, `DEPRECATED`), `config`, `config_sha256`, `effective_from`, `created_by`, `published_by`, `published_at` | mêmes règles d'immutabilité |

### Bots et sessions

| Table | Colonnes principales | Règles |
|---|---|---|
| `hl_bot` | `id`, `name` (unique), `environment` (`DEMO`, `PAPER`, `LIVE`), `status`, `strategy_version_id`, `risk_policy_version_id`, `row_version` (pour `If-Match`), `created_at`, `updated_at` | transitions ci-dessous ; seules des versions `PUBLISHED` peuvent être liées |
| `hl_bot_session` | `id`, `bot_id`, `environment`, `strategy_version_id`, `strategy_sha256`, `risk_policy_version_id`, `risk_policy_sha256`, `status`, `started_at`, `ended_at` | hashes figés au démarrage (INV-06) |

**Transitions de bot autorisées.** `CREATED → READY` ; `READY → RUNNING` ; `RUNNING → PAUSED` ; `PAUSED → RUNNING` ; `RUNNING|PAUSED|DEGRADED → STOPPING → STOPPED` ; `STOPPED → READY` (nouveau contrôle, jamais `STOPPED → RUNNING` direct) ; `RUNNING|PAUSED → DEGRADED` ; `DEGRADED → RUNNING|PAUSED` ; toute activité `→ FAILED` ; `FAILED → READY`. Toute autre transition lève `STATE_CONFLICT`. Démarrer (`READY → RUNNING`) un bot `LIVE` lève `LIVE_NOT_APPROVED` (ADR-009). Chaque transition incrémente `row_version`, écrit un audit et émet un événement : `BOT_PAUSED` (`RUNNING → PAUSED`), `BOT_RESUMED` (`PAUSED → RUNNING`), sinon `BOT_STATUS_CHANGED` (payload `from`, `to`, `reason`).

### Chaîne de décision et d'exécution (utilisée à partir de M1.3)

| Table | Colonnes principales | Règles |
|---|---|---|
| `hl_candidate` | `id`, `bot_id`, `session_id`, `instrument_id`, `strategy_version_id`, `window_start`, `status` (`DISCOVERED`, `ENRICHING`, `REJECTED`, `APPROVED`, `EXPIRED`), `features` (JSON), `created_at` | unique `(session_id, instrument_id, strategy_version_id, window_start)` |
| `hl_risk_assessment` | `id`, `candidate_id`, `verdict` (`PASS`, `REJECT`), `criteria` (JSON : critère → `PASS/FAIL/UNKNOWN`, valeur, unité, source, `observed_at`), `risk_policy_version_id`, `created_at` | jamais modifié ; une revalidation crée un nouvel assessment |
| `hl_sizing_decision` | `id`, `candidate_id`, `approved`, `notional_requested`, `notional_approved`, `quantity`, `reference_price`, `stop_price`, `risk_budget`, `limits_applied` (JSON), `rejection_reasons` (JSON), `input_hash`, `expires_at`, `created_at` | l'autorisation d'ordre d'entrée (INV-01) |
| `hl_order_intent` | `id`, `bot_id`, `session_id`, `candidate_id`, `position_id`, `instrument_id`, `side` (`BUY`, `SELL`), `purpose` (`ENTRY`, `EXIT`), `quantity`, `limit_price`, `max_notional`, `risk_approval_id → hl_sizing_decision`, `idempotency_key` (unique), `status`, `expires_at`, `created_at` | **contrainte** : `purpose = 'EXIT' OR risk_approval_id IS NOT NULL` (INV-01) ; une sortie référence sa `position_id` |
| `hl_order` | `id`, `intent_id`, `client_order_id` (unique), `venue_order_id`, `side`, `status` (machine 08-risque-execution/02), `requested_qty`, `filled_qty`, `avg_fill_price`, `limit_price`, `submitted_at`, `acked_at`, `closed_at`, `last_error` | jamais deux ordres pour une intention sans réconciliation (INV-04) |
| `hl_fill` | `id`, `order_id`, `venue_fill_id`, `qty`, `price`, `fee`, `fee_asset`, `liquidity`, `occurred_at`, `book_snapshot` (JSON) | unique `(order_id, venue_fill_id)` ; `qty > 0`, `price > 0` |
| `hl_position` | `id`, `bot_id`, `session_id`, `instrument_id`, `status` (`PENDING`, `OPEN`, `REDUCING`, `CLOSED`, `RECONCILIATION_REQUIRED`), `quantity`, `cost_basis`, `avg_entry_price`, `initial_stop_price`, `take_profit_price`, `r0_usd`, `opened_at`, `closed_at`, `row_version`, `realized_pnl_net`, `fees_paid`, `exit_reason` (M1.3, 06-agents/03), `mark_price`, `mark_value`, `marked_at` (M1.4, 10-backend/04) | projection des fills ; `CLOSED → OPEN` interdit |
| `hl_equity_snapshot` | `id`, `bot_id`, `at`, `cash`, `cash_reserved`, `positions_mark_value`, `equity`, `realized_pnl_net`, `unrealized_pnl`, `quality` (`OK`, `STALE`, `UNKNOWN`) | projection EQUITY |

### Données de marché (M1.2)

`hl_trade_minute` (agrégats de trades par minute, `live`/`backfill`) et `hl_feed_status` (état du flux par instrument) : spécifiées dans [07-data/03](../07-data/03-connecteur-kraken-m1.md#agrégats-de-trades). `hl_risk_policy_version.publish_requested_at / publish_requested_by / publish_request_reason` (publication en attente d'une policy assouplie, M2.3, migration `d2a7f4c81b90`) : voir [05-strategies/04](../05-strategies/04-versions-sans-code-m2.md#règles). `hl_book_snapshot` (carnet par minute, M2.1) : spécifiée dans [07-data/03 « Instantanés de carnet »](../07-data/03-connecteur-kraken-m1.md#instantanés-de-carnet-m21-adr-021).

### Ledger (08-risque-execution/03)

| Table | Colonnes principales | Règles |
|---|---|---|
| `hl_ledger_transaction` | `id`, `bot_id`, `kind` (`DEPOSIT`, `WITHDRAWAL`, `RESERVE`, `RELEASE`, `BUY_FILL`, `SELL_FILL`, `COMPENSATION`), `occurred_at`, `source_event_id`, `description` | append-only |
| `hl_ledger_entry` | `id`, `transaction_id`, `bot_id`, `account_code`, `asset`, `amount` (signé) | pour chaque `(transaction, asset)` : **somme = 0**, sinon la transaction est refusée |
| `hl_cash_movement` | `id`, `bot_id`, `kind` (`DEPOSIT`, `WITHDRAWAL`), `asset`, `amount` (> 0), `actor`, `reason`, `ledger_transaction_id`, `occurred_at` | flux de capital, jamais du PnL (QA-07) |

**Signe.** Débit positif, crédit négatif. **Comptes** (par bot) : `cash`, `cash_reserved`, `inventory:<instrument_id>` (au coût), `fees`, `realized_pnl` (gain = crédit), `owner_capital`. Le ledger est tenu en **USD** ; les quantités d'actif vivent dans les fills et positions.

| Opération | Écritures |
|---|---|
| Dépôt de 10 000 | `cash +10000` ; `owner_capital −10000` |
| Réservation de X | `cash −X` ; `cash_reserved +X` (libération : inverse) |
| Achat 100 @ 1, frais 1 | `inventory +100` ; `fees +1` ; `cash −101` (exemple 08-*/03) |
| Vente 100 @ 1,2, frais 1,2, coût FIFO 100 | `cash +118.8` ; `fees +1.2` ; `inventory −100` ; `realized_pnl −20` |

`equity = cash + cash_reserved + valeur de marché des positions`. `realized_pnl_net = −solde(realized_pnl) − frais imputés aux positions clôturées`. Le capital déposé n'apparaît que dans `owner_capital`.

## RBAC (ADR-006)

- Dépendance FastAPI `require_trading_roles(*roles)` : authentification châssis (`get_current_user`), puis au moins un des rôles **actifs** demandés, sinon `403 PERMISSION_DENIED` et `hl_audit_event` de la tentative pour les actions sensibles.
- **Amorçage.** Tant qu'**aucune** attribution n'existe dans `hl_role_assignment`, le premier utilisateur châssis `admin` qui appelle l'API trading reçoit les cinq rôles (`granted_by = NULL`, audit `ROLES_BOOTSTRAPPED`). Ensuite, seul un détenteur du rôle `ADMIN` attribue ou révoque.

## Amorçage des données (seed, idempotent)

Exécuté au démarrage du worker (et réexécutable sans effet) :
1. Politique `paper-default` version `1` depuis [`examples/risk-policy-paper-default.json`](../examples/risk-policy-paper-default.json), publiée par `system:seed`.
2. Stratégie `volume-spike` à la version de l'exemple courant (0.1.0 puis **0.2.0**, ADR-014) depuis [`examples/strategy-volume-spike.json`](../examples/strategy-volume-spike.json), publiée par `system:seed` **pour l'environnement PAPER uniquement** (audit `self_approved=true`, motif « amorçage M1 »). Toute version ultérieure suit le parcours de publication (01-produit/02).
3. Bot `paper-kraken-01`, `PAPER`, statut `READY`, lié aux deux versions publiées ; s'il n'a **jamais eu de session**, il est relié à la version d'exemple courante (audit `SEED_REBIND`, ADR-014).
4. Dépôt paper initial de 10 000 USD (ADR-010) : `hl_cash_movement` + transaction `DEPOSIT`.

Chaque étape vérifie l'existence avant de créer ; deux exécutions successives produisent le même état.

**Bots d'observation (ADR-027).** Après les étapes 1 à 4, le seed crée, s'ils n'existent pas :
5. Politique `rotation` version `1` ([`examples/risk-policy-rotation.json`](../examples/risk-policy-rotation.json)), publiée par `system:seed`.
6. Stratégies `breakout-1h`, `rebound`, `rotation` et `pullback` version `0.1.0` (fichiers `examples/strategy-*.json`), publiées par `system:seed` pour PAPER uniquement (audit `self_approved=true`, motif « ADR-027 observation paper »).
7. Bots `paper-kraken-02` (`breakout-1h`), `-03` (`rebound`), `-04` (`rotation`, policy `rotation`), `-05` (`pullback`), créés par la même fonction que « New bot » (06-agents/04 : mêmes validations, limite de 5 bots non arrêtés, dépôt paper de 10 000 USD), à l'état `READY` : **le démarrage reste une commande de l'opérateur**.

Règles propres à ces étapes : une version ou un bot qui existe déjà (quel que soit son statut : brouillon, retiré, bot en pause ou arrêté) n'est **jamais** modifié, republié ni recréé ; un bot n'est créé que si sa stratégie et sa policy sont publiées. Chaque élément est créé dans sa propre sous-transaction : un échec (par exemple la limite de bots atteinte) est journalisé (`seed: observation … skipped`) et n'empêche ni les autres éléments ni le démarrage du worker. Les copies se trouvent dans `app/domain/trading/seed_data/` et un test vérifie qu'elles restent identiques aux exemples.

## Environnement de développement (correction d'ADR-012)

Le `docker-compose.dev.yml` du châssis lance **backend (SQLite) + frontend**, sans PostgreSQL ni worker. En développement : backend via ce compose (ou `uvicorn` local), worker lancé localement (`python -m app.modules.worker.runner`) sur la même base. Les tests `pytest` utilisent SQLite en mémoire ; des tests marqués `postgres` s'exécutent contre une base PostgreSQL jetable lorsque `TEST_POSTGRES_URL` est défini (verrou consultatif, `NUMERIC`), sinon ils sont ignorés.

## Migrations (ADR-011)

- La chaîne Alembic **`domain`** (`alembic/domain/`, fournie par le template gitsky) crée et fait évoluer toutes les tables `hl_`. En production, le service `migrate` l'applique **après** les chaînes core et modules ; `create_all` reste réservé au développement.
- Toute modification du modèle physique suit l'ordre : (1) ce chapitre ; (2) les modèles ; (3) `alembic -n domain revision --autogenerate -m "<description>"` ; (4) **relecture du script** : types portables `Dec` (NUMERIC(38,18) en PostgreSQL) et `UtcDateTime` (`TIMESTAMP WITH TIME ZONE`), contraintes et index nommés comme dans les modèles, clés étrangères vers `users.id` (table châssis, non créée par la chaîne `domain`), aucune opération sur les tables du châssis ; (5) test `upgrade` sur une base PostgreSQL vide puis tests `postgres` ; (6) commit unique modèle + révision.
- **Règles de relecture constatées (révision `6f8c2b042d4b`).** L'autogénération rend `Dec`/`UtcDateTime` comme `app.domain.trading.types.*` sans import : on les **fige** dans le script en `sa.Numeric(precision=38, scale=18)` et `sa.DateTime(timezone=True)`, pour qu'aucune révision n'importe le code applicatif. `app/domain/trading/models.py` importe `app.core.models` afin que la table châssis `users` (cible de clés étrangères) soit connue de l'autogénération, sans jamais être modifiée par la chaîne `domain`. La commande s'exécute depuis la racine du dépôt avec `PYTHONPATH=.` et `DATABASE_URL` pointant sur une base PostgreSQL à jour (toutes chaînes appliquées).
- Un test vérifie que les métadonnées des modèles et le schéma obtenu après `upgrade head` ne divergent pas (absence de migration oubliée).

## Tests du lot M1.1

| ID | Vérification |
|---|---|
| T-M1.1-01 | transaction de ledger déséquilibrée refusée ; exemple d'achat 08-*/03 équilibré |
| T-M1.1-02 | dépôt : equity augmente, `realized_pnl_net` inchangé (QA-07) |
| T-M1.1-03 | vente avec coût FIFO : `realized_pnl` et frais conformes au tableau |
| T-M1.1-04 | `Dec` refuse `float`, conserve 18 décimales ; `UtcDateTime` refuse une date naïve |
| T-M1.1-05 | journal : `aggregate_sequence` contigu, `position` croissante, `event_id` dupliqué ignoré, checksum stable |
| T-M1.1-06 | curseur : lecture après position, avance, reprise après « crash » sans perte ni doublon |
| T-M1.1-07 | stratégie : exemple valide ; champ inconnu refusé ; version publiée non modifiable ; hash stable (INV-06) |
| T-M1.1-08 | politique de risque : exemple valide ; immutabilité après publication |
| T-M1.1-09 | `hl_order_intent` d'entrée sans `risk_approval_id` refusé (INV-01) |
| T-M1.1-10 | transitions de bot : autorisées OK, interdites `STATE_CONFLICT`, `LIVE` → `LIVE_NOT_APPROVED` |
| T-M1.1-11 | RBAC : amorçage du premier admin, `403` sans rôle, audit de la tentative |
| T-M1.1-12 | seed idempotent (deux exécutions = même état) |
| T-M1.1-13 | contrat : champs Pydantic = propriétés des schémas JSON (stratégie, politique) |
