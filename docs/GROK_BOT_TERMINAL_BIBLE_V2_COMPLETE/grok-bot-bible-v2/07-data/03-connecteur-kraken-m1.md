# Connecteur Kraken Spot — conception M1.2

> **0-hl — Bible V2.1 | 18 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée ; `[VÉRIFIÉ]` contrôlé contre la documentation et l'API réelle le 2026-11-18.

Ce chapitre détaille le lot **M1.2** ([13-roadmap](../13-roadmap/01-plan-execution.md)) et précise [07-data/01 « Connecteur Kraken Spot »](01-connecteurs-et-normalisation.md#connecteur-kraken-spot-adr-002-adr-003). En cas d'écart, **ce chapitre prévaut** pour l'implémentation M1.

## Faits vérifiés `[VÉRIFIÉ]`

Sources : documentation officielle (14-references) et appels à l'API publique réelle, sans clé.

| Sujet | Constat | Conséquence |
|---|---|---|
| Noms de paires | REST `AssetPairs`/`Ticker`/`OHLC` avec `assetVersion=1` : clés `BTC/USD` ; le champ REST `wsname` vaut encore `XBT/USD` (héritage), mais le WebSocket v2 utilise `BTC/USD` | identité = clé `assetVersion=1` ; **ne jamais** utiliser `wsname` |
| Volume `Ticker` | `v = [aujourd'hui, 24 h]` en **devise de base** ; `p = [vwap aujourd'hui, vwap 24 h]` | `volume_24h_usd = v[1] × p[1]` (paire cotée USD) |
| Univers brut | 1 435 paires, 663 cotées `USD` ; statuts REST observés `online`, `cancel_only`, `post_only` ; nombreuses bases stablecoin (`USDT`, `USDC`, `DAI`, `PYUSD`, `RLUSD`, `USD1`, `USDQ`, `USDR`, `USDD`, `AUSD`…) | liste d'exclusion explicite (ci-dessous) |
| Statuts WS `instrument` | `online`, `cancel_only`, `post_only`, `limit_only`, `reduce_only`, `maintenance`, `delisted`, `work_in_progress` ; champs `price_precision`, `qty_precision`, `price_increment`, `qty_increment`, `qty_min`, `cost_min` | seul `online` est tradable |
| `OHLC` | 720 entrées max + la dernière **non validée** (bougie en cours) ; `[time, open, high, low, close, vwap, volume, count]`, volume en base | ignorer la dernière bougie |
| WS `book` | profondeurs 10/25/100/500/1000 ; snapshot puis updates ; niveau `qty = 0` = suppression ; tronquer à la profondeur après chaque message ; ~190 messages/s observés pour BTC+ETH en profondeur 25 | traitement en mémoire, pas d'écriture base par message |
| Checksum `book` | CRC32 non signé sur les 10 meilleurs asks (prix croissants) puis 10 meilleurs bids (prix décroissants), chaque niveau = prix puis quantité, **point décimal retiré, zéros de tête retirés** | voir règle de formatage ci-dessous |
| Formatage du checksum | sur 4 613 messages réels : formatage par **`price_precision`/`qty_precision`** de la paire = **100 %** de checksums valides ; texte JSON brut = 96 % (193 échecs) | **obligation** : formater `price` avec `price_precision` et `qty` avec `qty_precision` du canal `instrument` |
| Nombres JSON | prix/quantités envoyés comme nombres JSON | parser avec `json.loads(..., parse_float=Decimal)` ; jamais `float` |
| WS `trade` | `side` = côté **taker** (`buy`/`sell`), `trade_id` séquentiel par paire, snapshot optionnel (50 derniers trades) | pas de snapshot (évite les doubles comptes à la reconnexion) |
| Heartbeat | `{"channel":"heartbeat"}` ≈ 1/s **seulement en l'absence d'autre message** ; canal `status` (`system: online`…) | chien de garde : aucun message depuis 5 s ⇒ connexion morte |
| Limites REST publiques | non documentées sur les pages consultées `[OPEN]` | client prudent : **au plus 1 requête/s**, retry borné sur `EAPI:Rate limit` |
| Dépendance | `websockets` 17.4 déjà résolue par `uvicorn[standard]==0.51.0` du châssis | épinglée explicitement `websockets==17.4` (ADR-012) |

## Composants

Sous `app/domain/trading/market/` :

| Module | Rôle |
|---|---|
| `kraken_rest.py` | client REST public (`AssetPairs`, `Ticker`, `OHLC`) au-dessus de `httpx.AsyncClient`, `assetVersion=1`, ≤ 1 req/s, erreurs Kraken (`error[]`) levées en `PROVIDER_UNAVAILABLE` |
| `kraken_ws.py` | client WebSocket v2 : transport injectable (réel = `websockets`, test = rejeu de fixtures), abonnements, routage des messages, chien de garde, reconnexion avec backoff |
| `book.py` | carnet d'ordres en `Decimal` : snapshot/update, troncature, checksum, meilleur bid/ask, `spread_bps` |
| `universe.py` | sélection de l'univers (règles ci-dessous), pure et testable |
| `aggregates.py` | agrégation des trades en minutes UTC, couverture de connexion, backfill OHLC |
| `state.py` | `MarketState` en mémoire : instruments, carnets, état du flux par paire ; API de lecture pour les agents M1.3 |
| `service.py` | `MarketDataService` : orchestre REST, WS, univers, flush des minutes, persistance de l'état du flux, événements |

## Univers (ADR-003)

1. `AssetPairs` + `Ticker` (sans filtre de paire, `assetVersion=1`).
2. Candidates : `quote = "USD"`, `aclass_base = "currency"`, statut REST `online`, base **hors** liste d'exclusion.
3. **Exclusions** (stablecoins et fiat) : `USDT`, `USDC`, `DAI`, `PYUSD`, `RLUSD`, `USD1`, `USDQ`, `USDR`, `USDD`, `USDG`, `USDE`, `TUSD`, `FDUSD`, `AUSD`, `EURC`, `EURCV`, `EUROP`, **`PAXG`, `XAUT`** (jetons adossés à l'or, ajoutés le 2026-11-18 : leur volume suit l'or, pas le momentum crypto), `EUR`, `GBP`, `CAD`, `AUD`, `CHF`, `JPY`, `AED`. La liste est un paramètre du code, revue à chaque ajout constaté chez Kraken.
4. `volume_24h_usd = v[1] × p[1]` ; filtre `≥ universe.min_volume_24h_usd` de la stratégie.
5. Tri décroissant par `volume_24h_usd`, puis par symbole (déterminisme) ; on garde `top_n_by_volume`.
6. Persistance : upsert `hl_instrument` pour toutes les candidates évaluées (`in_universe` vrai/faux, règles d'ordre, `volume_24h_usd`). Correspondance des règles : `pair_decimals → price_precision`, `lot_decimals → qty_precision`, `tick_size → price_increment`, `ordermin → qty_min`, `costmin → cost_min` ; le canal `instrument` met ensuite à jour le statut en continu.
7. Événement `UNIVERSE_UPDATED` (`added`, `removed`, `size`) uniquement si la composition change.
8. **Abonnements** = univers ∪ paires ayant une position ouverte (une paire sortie de l'univers reste supervisée, ADR-003).
9. Rafraîchissement toutes les `universe.refresh_seconds` (3 600 s).

## Flux WebSocket

- **Une connexion** `wss://ws.kraken.com/v2` ; abonnements : `instrument` (snapshot), `book` profondeur **10** (25 jusqu'au 2026-11-19, ADR-017), `trade` sans snapshot ; ajouts/retraits d'abonnements lors des changements d'univers.
- **Fraîcheur du carnet** (précise 07-data/01) : un carnet est **frais** si (a) la connexion est vivante (dernier message, heartbeat compris, < 5 s), (b) un snapshot a été reçu depuis la dernière (re)connexion et (c) le dernier checksum est valide. L'absence de mise à jour d'un carnet calme n'est **pas** une péremption. L'âge de la dernière modification reste exposé à titre informatif.
- **Retard du flux (ADR-017)** : `retard = réception − horodatage Kraken` de chaque message `book`/`trade` ; paire `STALE` (motif `FEED_LAG`) au-delà de 2 s, retour `LIVE` au premier carnet valide sous 1 s ; reconnexion volontaire (motif `FEED_LAG`) si le retard dépasse 15 s. Un carnet en retard n'est jamais présenté comme frais.
- **Checksum invalide** ⇒ carnet `STALE` (motif `CHECKSUM_MISMATCH`), désabonnement puis réabonnement du `book` pour cette paire (nouveau snapshot), compteur `resync_count`.
- **Connexion perdue / chien de garde** ⇒ toutes les paires `STALE` (motif `CONNECTION_LOST`), événement `PROVIDER_DEGRADED`, reconnexion avec backoff exponentiel 1, 2, 4… 60 s (+ gigue) ; retour `LIVE` paire par paire au premier snapshot valide.
- **Canal `status`** avec `system ≠ online` ⇒ toutes les paires `STALE` (motif `VENUE_MAINTENANCE`).
- **Statut de paire** via `instrument` : tout changement met à jour `hl_instrument.status` et émet `INSTRUMENT_STATUS_CHANGED` ; une paire non `online` est `NOT_TRADABLE` pour le RUG CHECK (variante CEX), indépendamment de la fraîcheur.
- **États d'une paire** : `UNKNOWN` (abonnée, premier snapshot attendu, motif `AWAITING_SNAPSHOT`), `LIVE`, `STALE`. Seul `LIVE` autorise une décision.
- **Événements** émis **uniquement sur transition** : `DATA_STALE` (paire, motif ; **sortie de `LIVE`** uniquement — une paire jamais `LIVE` ne génère pas de bruit au démarrage), `DATA_FRESH` (retour `STALE → LIVE`), `PROVIDER_DEGRADED`, `PROVIDER_RECOVERED`, `INSTRUMENT_STATUS_CHANGED`, `UNIVERSE_UPDATED`. Jamais un événement par tick.

## Agrégats de trades

Nouvelle table **`hl_trade_minute`** (complète [10-backend/03](../10-backend/03-modele-donnees-m1.md)) :

| Colonne | Contenu |
|---|---|
| `instrument_id`, `minute_start` (UTC, seconde 0), `source` (`live` / `backfill`) | unique ensemble |
| `notional_usd` | Σ prix × quantité (paire cotée USD) ; backfill : `volume × vwap` |
| `buy_notional_usd`, `sell_notional_usd`, `buy_count`, `sell_count` | côté taker ; **NULL** en backfill (inconnu, jamais 0) |
| `trade_count`, `volume_base` | |
| `open`, `high`, `low`, `close` | NULL si aucun trade dans la minute |

Règles :
- **Couverture.** Une minute `live` n'est écrite que si la connexion a été vivante **pendant toute la minute** ; dans ce cas, une minute sans trade est écrite avec des zéros (volume nul observé). Une minute avec coupure n'est **pas** écrite : c'est un trou, que les features traitent comme `UNKNOWN`, jamais comme zéro.
- **Clôture.** Une minute est écrite après sa fin (horloge UTC + 2 s de marge pour les trades tardifs) ; la minute en cours n'est jamais écrite. À l'arrêt du worker, la minute partielle est abandonnée.
- **Déduplication.** `trade_id` par paire strictement croissant ; un `trade_id` déjà vu est ignoré.
- **Backfill** (INV-03) : à l'entrée d'une paire dans l'univers et au démarrage, `OHLC` 1 min ; la dernière bougie (non validée) est ignorée ; écriture `source = backfill` uniquement pour les minutes **antérieures** à la première minute `live` couverte. Le backfill n'écrase jamais une minute `live`. Les features M1.3 lisent `live` en priorité, puis `backfill` pour l'historique de la ligne de base uniquement, et marquent `uses_backfill = true`.
- **Rétention** : 7 jours (purge par le worker, quotidienne).

## État du flux persisté

Nouvelle table **`hl_feed_status`**, une ligne par instrument abonné plus une ligne `kraken_spot:__connection__` : `state` (`LIVE`, `STALE`, `UNKNOWN`), `reason`, `last_message_at`, `last_book_change_at`, `last_trade_at`, `last_checksum_ok_at`, `resync_count`, `updated_at`. Écrite par le worker **toutes les 5 s** (upsert), lue par l'API (`/api/v1/health`, UI-08/UI-10). Les agents du worker lisent l'état **en mémoire** (`MarketState`), plus frais.

## Intégration au worker (ADR-007, ADR-012)

- `run_cycle` : amorçage (10-backend/03), puis démarrage du `MarketDataService`, puis attente de `stop_requested` ; à l'arrêt : fermeture propre du WebSocket, annulation des tâches, dernière écriture de `hl_feed_status`.
- Le service utilise **ses propres sessions** de base (une par unité de travail), jamais une session ouverte pendant toute la vie du worker.
- Dans la persistance périodique, les événements en attente sont écrits **en dernier**, juste avant la validation, pour tenir le verrou du journal le moins longtemps possible (M1.3).
- **Robustesse** : Kraken injoignable, erreur réseau ou message invalide ne font **jamais** sortir `run_cycle` ; ils produisent état `STALE`, événements et reconnexion. Seule une erreur de programmation inattendue remonte au runner du châssis (journalisée `failed`).

## Journalisation du worker (constat production 2026-11-18)

`docker logs 0-hl_worker` était vide : les loggers `hl.*` n'avaient aucun handler. Règles :
- Au démarrage de `run_cycle`, le logger racine du domaine **`hl`** reçoit un handler sur **stdout**, niveau **INFO**, format `horodatage UTC ISO | niveau | logger | message` ; `propagate = False` (pas de doublon) ; configuration idempotente. Le handler vide son tampon à chaque ligne (pas de dépendance à `PYTHONUNBUFFERED`, aucun fichier châssis modifié).
- Messages **INFO** obligatoires : démarrage du worker et fin d'amorçage ; sélection de l'univers (taille, ajouts, retraits) ; fin du backfill (paires, lignes écrites, durée) ; connexion WebSocket établie (numéro de connexion) ; **résumé périodique toutes les 5 min** (paires `LIVE`/abonnées, minutes `live` écrites depuis le dernier résumé, resyncs cumulés, et depuis ADR-017 : débit en messages/s, coût moyen de traitement en µs, retard maximal en ms) ; arrêt propre.
- Messages **WARNING** obligatoires : chaque déconnexion (motif `WATCHDOG_TIMEOUT`, `CONNECTION_LOST`, `CONNECT_FAILED`) ; chaque resync de carnet (paire, motif) ; chaque changement de statut d'instrument ; échec REST (univers, backfill) ; canal `status` hors `online`. **ERROR** (avec trace) : échec de la persistance périodique, exception d'un gestionnaire de message.
- Les messages ne contiennent jamais de secret (aucune clé n'existe au M1).
- Diagnostic serveur : les commandes `psql` passent par `docker exec 0-hl_db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "…"'` pour que les variables soient résolues dans le conteneur.

## Tests M1.2 (sans réseau)

| ID | Vérification |
|---|---|
| T-M1.2-01 | rejeu de la session réelle enregistrée : 100 % des checksums valides avec le formatage par précision |
| T-M1.2-02 | checksum altéré ⇒ paire `STALE` (`CHECKSUM_MISMATCH`), demande de réabonnement, `DATA_STALE` émis une seule fois |
| T-M1.2-03 | parsing : aucun `float` ; prix et quantités en `Decimal` |
| T-M1.2-04 | univers sur fixtures REST : exclut stablecoins, fiat, non-USD, `cancel_only`, volume insuffisant ; tri et top-N déterministes |
| T-M1.2-05 | agrégation : notionnels acheteur/vendeur, bornes de minute, déduplication `trade_id` |
| T-M1.2-06 | couverture : minute sans trade et connexion vivante ⇒ ligne à zéro ; minute avec coupure ⇒ aucune ligne |
| T-M1.2-07 | backfill : dernière bougie ignorée, `source=backfill`, côté acheteur NULL, aucune minute `live` écrasée |
| T-M1.2-08 | chien de garde : silence > 5 s ⇒ toutes paires `STALE`, `PROVIDER_DEGRADED`, reconnexion, retour `LIVE` au snapshot |
| T-M1.2-09 | `instrument` : passage `online → cancel_only` ⇒ statut persisté et `INSTRUMENT_STATUS_CHANGED` |
| T-M1.2-10 | `run_cycle` avec transport qui échoue toujours : aucune exception, arrêt propre sur `stop_requested` |
| T-M1.2-11 | `hl_feed_status` persisté et cohérent avec `MarketState` |
| T-M1.2-12 | migration `domain` des nouvelles tables (test de dérive PostgreSQL) |
| T-M1.2-14 | retard : message en retard > 2 s ⇒ paire `STALE` `FEED_LAG` ; retour `LIVE` sous 1 s ; retard > 15 s ⇒ reconnexion `FEED_LAG` ; arrêt propre de la boucle de réception sans tâche par message (ADR-017) |
| T-M1.2-13 | journalisation : handler stdout idempotent sur `hl` ; messages INFO/WARNING obligatoires émis (univers, backfill, connexion, déconnexion, resync, résumé) |
