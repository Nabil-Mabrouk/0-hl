# Collecte, provenance et normalisation de données

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Architecture d'ingestion

Connecteurs hétérogènes → validation de schéma → normalisation d'unité → enrichissement de provenance → bus de données → feature store / cache → agents. Le fournisseur n'est jamais la source de vérité du ledger. Le choix des APIs doit être validé sur leurs conditions d'utilisation et capacités réelles par réseau.

## Sources pressenties et usages

| Source | Usage | Limite à traiter |
|---|---|---|
| **Kraken Spot (ADR-002)** | paires, statut, carnet, trades, ticker, OHLC ; plus tard balances/ordres/fills privés | rate limits, symboles multiples (REST `XXBTZUSD` / WS v2 `BTC/USD`), resynchronisation du carnet |
| DEX Screener | paires, volumes, tx counts, liquidité | cadence, prix composite, limitations d'endpoint |
| GeckoTerminal | nouveaux pools, OHLCV, trades | temps d'indexation, granularité |
| Birdeye | tokens, trading, holders | quota/pricing, couverture réseau |
| GoPlus | signaux de risque token | UNKNOWN vs safe ; qualité d'attribution |
| Bubblemaps | clusters / concentration | interprétation heuristique |
| RPC/WebSocket | logs, slots/blocks, tx confirmations | reorgs, latence, coûts |
| Exchange | balances, orders, fills | permissions, idempotence, état distant |

## Connecteur Kraken Spot (ADR-002, ADR-003)

Conception détaillée et faits vérifiés contre l'API réelle : [03-connecteur-kraken-m1](03-connecteur-kraken-m1.md) (prévaut sur cette section).

Seule source active du premier jalon. Les DEX/agrégateurs du tableau ci-dessus restent des sources candidates pour de futures venues. Les points ci-dessous sont à **revérifier contre la documentation Kraken** au moment de l'implémentation (14-references) ; tout écart est consigné ici avant d'être codé (ADR-000).

**Public REST** (`https://api.kraken.com/0/public/…`, sans clé) :
- `AssetPairs` — référentiel : `altname`, `wsname`, `base`, `quote`, `pair_decimals`, `lot_decimals`, `tick_size`, `ordermin`, `costmin`, `status`. Rafraîchi au démarrage et toutes les `refresh_seconds`.
- `Ticker` — volume 24h et dernier prix, pour le classement top-N (ADR-003).
- `OHLC` (intervalle 1 min) — amorçage de l'historique des fenêtres de volume au démarrage, partition `backfill` distincte de l'observation live (INV-03).

**Public WebSocket v2** (`wss://ws.kraken.com/v2`) : canaux `instrument` (statut et règles des paires), `ticker` (bid/ask), `book` (profondeur 25, avec vérification du **checksum** ; checksum invalide ou gap ⇒ `STALE` puis resouscription et snapshot), `trade` (prix, qty, `side` taker, horodatage, `trade_id`), `ohlc`. Heartbeat surveillé ; une déconnexion marque toutes les paires `STALE` jusqu'au prochain snapshot complet.

**Privé — LIVE futur uniquement (ADR-009), non implémenté au premier jalon** : REST `AddOrder` (avec `cl_ord_id`), `QueryOrders`, `OpenOrders`, `CancelOrder`, `TradesHistory`, `Balance`, `GetWebSocketsToken` ; WebSocket authentifié `wss://ws-auth.kraken.com/v2` (`add_order`, `cancel_order`, canaux `executions`, `balances`).

**Normalisation.** `instrument_id = "kraken_spot:" + wsname` (ex. `kraken_spot:BTC/USD`) ; le référentiel conserve les alias REST (`XXBTZUSD`, `XBTUSD`) et les décimales. Prix et quantités sont parsés en `Decimal` depuis les chaînes, jamais via `float`. Horodatages : `provider_timestamp` (champ Kraken), `ingested_at` (horloge serveur UTC).

**Fraîcheur par défaut** (précisée et corrigée par [03-connecteur-kraken-m1](03-connecteur-kraken-m1.md#flux-websocket) : un carnet calme n'est pas périmé tant que la connexion vit et que son checksum est valide). `book` et `ticker` : TTL 3 s *(V2.0, remplacé)* ; `trade` : la fenêtre est valide si le flux est connecté et le heartbeat < 5 s (absence de trade ≠ données périmées) ; référentiel `instrument` : 1 h.

**Persistance.** On ne stocke pas chaque tick en base : trades agrégés en fenêtres de 1 min (volume notionnel, notionnel acheteur/vendeur, nombre, OHLC) conservées pour les features et le replay ; carnet conservé en mémoire, avec un instantané persisté à chaque décision (entrée de l'audit de la décision et du fill paper).

## Identité d'un actif

**CEX :** `InstrumentIdentity = venue_id + venue_symbol` (ex. `kraken_spot:BTC/USD`). **DEX :** `AssetIdentity = chain_id + contract_address (+ token_program)` ; `PoolIdentity = chain_id + pool_address` ; symbole et nom ne servent qu'à l'affichage. Préserver decimals, quote currency, base token, version du contrat, réserve pool et type DEX. Pas de fusion de deux pools parce qu'ils ont même ticker.

## Fraîcheur et qualité

Pour chaque enregistrement : `event_time`, `observed_at`, `ingested_at`, `block_height`, `source`, `unit`, `source_version`, `confidence`, `ttl_ms`, `quality_flags`. `STALE` ou `UNVERIFIED` doit bloquer une décision qui requiert une vérité forte. Contrôle de monotonicité, duplicate detection, bornes et détection de retournement de chaîne.

## Cache et backfill

TTL variable par métrique ; le backfill est écrit dans une partition séparée de l'observation live, afin de ne pas introduire une information future dans un replay. Conserver raw immutable compressed (Parquet/object storage) et séries normalisées. Éviter toute réécriture silencieuse d'un fait ancien ; ajouter versions et corrections.
