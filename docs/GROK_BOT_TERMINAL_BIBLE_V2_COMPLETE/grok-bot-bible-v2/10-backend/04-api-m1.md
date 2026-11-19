# API REST et flux terminal — conception M1.4

> **0-hl — Bible V2.1 | 18 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Ce chapitre détaille le lot **M1.4** ([13-roadmap](../13-roadmap/01-plan-execution.md)) et concrétise [02-api-websocket-modeles](02-api-websocket-modeles.md) pour 0-hl. Il fournit tout ce dont les écrans M1.5 (UI-01 Live Terminal, UI-02 Bots & Sessions) ont besoin. En cas d'écart, **ce chapitre prévaut** pour l'implémentation M1.

## Principes

- **Emplacement.** Routeurs dans `app/domain/trading/api/`, exposés par `app/domain/routers.py` (variables `*_router`, montées automatiquement par le châssis). Aucun fichier châssis modifié.
- **Hôte.** API servie sur `api.0-hl.com` (Traefik du châssis), frontend sur `0-hl.com`. CORS du châssis (`frontend_url`).
- **Lecture seule sauf commandes.** M1.4 n'expose qu'**une** mutation : l'envoi de commandes de bot. Création de bots, édition de stratégies ou de politiques : hors M1 (UI-04/05/06).
- **Séparation des processus (ADR-007).** L'API ne lit **que la base** : l'état de marché en mémoire du worker ne lui est pas accessible. Tout ce que l'UI affiche est donc persisté par le worker (positions marquées, état du flux, statistiques de scan, événements).
- **Montants** sérialisés en **chaînes décimales** (jamais de nombre flottant JSON) ; horodatages ISO 8601 UTC ; valeur inconnue = `null` (affichée `—`, jamais `0`, 02-design/03).

## Authentification, rôles, erreurs

- Authentification châssis (JWT `Authorization: Bearer`) ; rôles métier vérifiés sur **chaque** route (ADR-006) : lecture = `OBSERVER` (ou tout autre rôle métier) ; commandes = `OPERATOR` ; `EMERGENCY_STOP` = `OPERATOR` **ou** `RISK_OFFICER`. Refus d'une commande ⇒ `403` + audit (QA-13).
- **Erreurs** au format RFC 9457 (`application/problem+json`) : `type`, `title`, `status`, `code` (taxonomie 10-backend/02), `detail`, `correlation_id`. Les `DomainError` sont converties par la classe de route du domaine, sans gestionnaire global châssis.
- **`X-Correlation-ID`** : repris de la requête s'il est fourni (UUID), sinon généré ; renvoyé dans la réponse et dans le corps des erreurs.

## Routes `/api/v1`

| Route | Méthode | Rôle | Contenu |
|---|---|---|---|
| `/api/v1/me` | GET | tout rôle métier | utilisateur, rôles métier actifs (déclenche l'amorçage ADR-006 au premier admin) |
| `/api/v1/health` | GET | Observer | état métier : flux (connexion, paires `LIVE`/abonnées, âge du dernier état persisté), bots (statut), dernier `SCAN_CYCLE_COMPLETED` ; `worker_alive = false` si `hl_feed_status` n'a pas été mis à jour depuis 30 s ou si le worker a consigné son arrêt (`WORKER_STOPPED`) |
| `/api/v1/bots` | GET | Observer | liste des bots |
| `/api/v1/bots/{id}` | GET | Observer | bot, session active, versions et hashes ; en-tête `ETag: "<row_version>"` |
| `/api/v1/bots/{id}/commands` | POST | Operator (E-stop : + Risk Officer) | corps `{command, reason}` ; en-tête **`Idempotency-Key` obligatoire** ; réponse **`202`** `{command_id, status: "PENDING"}` ; même clé ⇒ même commande, jamais une seconde ; l'UI attend `COMMAND_COMPLETED`/`COMMAND_REJECTED` (04-metier/02) |
| `/api/v1/commands/{id}` | GET | Observer | état d'une commande |
| `/api/v1/strategies` | GET | Observer | versions de stratégie (statut, hash, config) |
| `/api/v1/risk-policies` | GET | Observer | versions de politique |
| `/api/v1/terminal/snapshot?bot_id=` | GET | Observer | **instantané terminal** (ci-dessous), aussi envoyé par le WebSocket |
| `/api/v1/positions?bot_id=&status=` | GET | Observer | positions avec marquage |
| `/api/v1/trades?bot_id=&limit=` | GET | Observer | positions clôturées (motif, PnL net, R, frais) |
| `/api/v1/orders?bot_id=&limit=` | GET | Observer | ordres et fills |
| `/api/v1/equity?bot_id=&since=` | GET | Observer | instantanés d'equity (courbe) |
| `/api/v1/events?bot_id=&after=&types=&limit=` | GET | Observer | journal, pagination par curseur `position` (`next_after`) ; `limit ≤ 500` |
| `/api/v1/events/trace/{correlation_id}` | GET | Observer | chaîne complète candidat → sortie (03-ecrans/UI-13) |
| `/api/v1/market/feed` | GET | Observer | `hl_feed_status` + instruments de l'univers |

## Instantané terminal

Objet unique décrivant la vue UI-01 pour un bot, construit à partir de la base :
- `bot` (id, nom, environnement, statut, `row_version`), `session` (id, début, uptime en secondes, hashes) ;
- `equity` : dernier calcul — `cash`, `cash_reserved`, `positions_value` (Σ `mark_value`), `equity`, `realized_pnl_net` (Σ positions closes), `unrealized_pnl`, `quality` (`OK` si toutes les positions ouvertes ont une marque de moins de 30 s, sinon `STALE`) ; `as_of` ;
- `stats` : `trades` (positions closes), `wins` (PnL net > 0), `losses` (< 0), `breakeven` ;
- `open_positions` (avec marque), `recent_trades` (20 dernières clôtures) ;
- `agents` : pour chacun des six rôles, `last_event_at`, `last_event_type`, `status` (`ACTIVE` si événement < 2 min, `IDLE` sinon, `DOWN` si worker non vivant) d'après la table de correspondance événement → agent ci-dessous ;
- `scan` : dernier `SCAN_CYCLE_COMPLETED` (paires évaluées, `UNKNOWN`, candidats, rejets par condition) ;
- `feed` : connexion, paires `LIVE`/abonnées ;
- `recent_events` : 50 derniers événements du bot et du flux ;
- `position` : position du journal à partir de laquelle le flux WebSocket reprend ;
- *(M1.5, revue du 2026-11-19)* `entry_blocks` : liste des blocages d'entrée en vigueur (`code` parmi `BOT_NOT_RUNNING`, `KILL_SWITCH`, `MAX_OPEN_POSITIONS`, `MAX_DAILY_LOSS`, `MAX_DRAWDOWN`, avec `value`, `limit`, `until`), calculés avec les mêmes fonctions que le SIZER ; pour chaque position, `entry_qty` et `entry_notional` (quantité et montant exécutés à l'entrée, d'après les fills de l'ordre d'entrée).
- *(M1.5)* `config` (`strategy_version`, `max_trade_risk_pct`, `max_slippage_pct`), `owner_capital`, et pour chaque position `entry_features` (features clés du candidat d'entrée) — voir [03-ecrans/M1-terminal-et-bots](../03-ecrans/M1-terminal-et-bots.md#ui-01-live-terminal).

| Agent | Événements |
|---|---|
| SCANNER | `SCAN_CYCLE_COMPLETED`, `CANDIDATE_DETECTED` |
| RUG CHECK | `RISK_PASSED`, `RISK_REJECTED` (étape `RUG_CHECK`) |
| SIZER | `POSITION_SIZED`, `RISK_REJECTED` (étape `SIZER`) |
| EXECUTOR | `ORDER_*`, `POSITION_OPENED`, `POSITION_REDUCED`, `POSITION_CLOSED` |
| LOGGER | tout événement (dernier `position` du journal) |
| EQUITY | `EQUITY_UPDATED`, `RECONCILIATION_OK`, `RECONCILIATION_FAILED` |

## Persistance ajoutée côté worker (complète 06-agents/03)

- **Marquage des positions** : à chaque itération EQUITY, chaque position ouverte reçoit `mark_price` (meilleur bid), `mark_value` (valeur de liquidation conservative) et `marked_at` ; carnet non `LIVE` ⇒ `mark_value = NULL` (pas d'ancienne valeur présentée comme fraîche : `marked_at` reste celui de la dernière marque valide et la qualité devient `STALE`). Nouvelles colonnes `hl_position.mark_price`, `mark_value`, `marked_at`.
- **Equity** : l'instantané `hl_equity_snapshot` reste écrit toutes les 60 s ; l'API recalcule l'equity courante depuis le ledger et les marques (plus frais).
- **`SCAN_CYCLE_COMPLETED`** (nouvel événement, une fois par minute et par bot `RUNNING`) : `window_end`, `evaluated`, `unknown`, `candidates`, `rejected`, `fails` (nombre d'échecs **par condition** de la stratégie : `volume_spike`, `window_notional_usd`, `buy_notional_share`, `price_change_5m`, `spread_bps`…), `unknown_reasons` (par motif). Le résumé journal toutes les 5 min inclut le cumul `fails` (demande opérateur du 2026-11-18 : savoir quelle condition bloque les entrées).

## WebSocket `/ws/v1/terminal`

- **Origine** : la connexion est refusée (code `4403`) si l'en-tête `Origin` n'est pas `frontend_url` (protection contre le détournement de WebSocket inter-sites ; les navigateurs n'appliquent pas CORS aux WebSockets).
- **Authentification par premier message** (jamais de jeton dans l'URL, qui finirait dans les journaux) : le client envoie `{"type":"hello","token":"<access JWT>","bot_id":"…","last_position":n|null}` dans les 5 s ; jeton invalide ou rôle manquant ⇒ fermeture `4401`/`4403`. À l'**expiration** du jeton, le serveur ferme avec `4401` : le client rafraîchit son jeton et se reconnecte avec `last_position`.
- **Réponse** : `{"type":"hello","position":n,"replay_limit":1000,"heartbeat_ms":10000}` (entiers uniquement, aucun flottant JSON) puis :
  - `last_position` absent, ou plus de 1 000 événements de retard ⇒ `{"type":"snapshot","data":<instantané terminal>}` (jamais de continuité fictive) ;
  - sinon rejeu des événements manqués.
- **Flux** : `{"type":"event","data":<événement>}` pour les événements du bot et ceux du flux de marché (`aggregate_type = market_feed`), dans l'ordre de `position` ; le serveur interroge le journal toutes les **1 s** ; `{"type":"heartbeat","position":n}` toutes les 10 s.
- **Reprise** : le client conserve la dernière `position` reçue ; à la reconnexion, il l'envoie dans `hello`. Un événement ne peut pas être perdu (TCP + rejeu par curseur) ; un retard trop grand produit un nouvel instantané.
- Chaque connexion lit la base indépendamment (compatible avec plusieurs workers gunicorn).

## Tests M1.4 (sans réseau)

| ID | Vérification |
|---|---|
| T-M1.4-01 | chaque route : `401` sans jeton, `403` sans rôle métier, `200` Observer |
| T-M1.4-02 | erreurs RFC 9457 avec `code` et `correlation_id` ; `X-Correlation-ID` repris ou généré |
| T-M1.4-03 | commande : `Idempotency-Key` obligatoire (`422` sinon) ; `202 PENDING` ; même clé ⇒ même `command_id` ; Observer ⇒ `403` + audit ; E-stop accepté pour Risk Officer |
| T-M1.4-04 | commande traitée par le worker ⇒ `GET /commands/{id}` `COMPLETED` ; `START` en LIVE ⇒ `REJECTED` / `LIVE_NOT_APPROVED` |
| T-M1.4-05 | instantané : equity = cash + réservé + marques ; marque périmée ⇒ `quality = STALE` et valeur `null` ; wins/losses ; agents ; scan |
| T-M1.4-06 | montants en chaînes décimales, aucun flottant JSON |
| T-M1.4-07 | événements : pagination par curseur sans doublon ni trou ; trace par `correlation_id` |
| T-M1.4-08 | WebSocket : origine refusée ; jeton manquant/invalide ⇒ fermeture ; `hello` puis instantané ; événements nouveaux poussés dans l'ordre ; reprise depuis `last_position` sans doublon ; retard > 1 000 ⇒ instantané |
| T-M1.4-09 | worker : marquage des positions à chaque itération ; `SCAN_CYCLE_COMPLETED` avec `fails` par condition |
| T-M1.4-10 | migration `domain` des nouvelles colonnes (dérive PostgreSQL) |
