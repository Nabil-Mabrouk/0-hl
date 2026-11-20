# Agents de décision — conception M1.3

> **0-hl — Bible V2.1 | 18 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Ce chapitre détaille le lot **M1.3** ([13-roadmap](../13-roadmap/01-plan-execution.md)) : la boucle de décision paper qui relie le flux Kraken (07-data/03) au ledger (08-risque-execution/03). Il concrétise [01-architecture-agents](01-architecture-agents.md), [STRAT-01 Kraken](../05-strategies/02-catalogue-strategies.md#strat-01--variante-kraken-spot-adr-005), [05-strategies/03](../05-strategies/03-risque-mathematique.md) et [08-risque-execution](../08-risque-execution/01-risk-engine.md). En cas d'écart, **ce chapitre prévaut** pour l'implémentation M1.

## Boucle de décision

Le moteur (`DecisionEngine`) tourne dans le worker, à côté du `MarketDataService`, et lit l'état de marché **en mémoire**. Une itération toutes les **5 s**, dans cet ordre, chaque étape dans sa propre transaction :

1. **Commandes** : traitement des `hl_command` `PENDING` (ci-dessous) et démarrage automatique (ADR-013).
2. **Sorties** : supervision de toutes les positions ouvertes de tous les bots, **quel que soit** le statut du bot (une pause ou un arrêt ne coupe jamais la protection des positions).
3. **Entrées** : pour chaque bot `RUNNING` dont les entrées ne sont pas bloquées : SCANNER → RUG CHECK → SIZER → EXECUTOR.
4. **EQUITY** : projection, instantané, contrôles continus et réconciliation.

`stop_requested` est vérifié entre chaque étape. Une erreur inattendue dans une étape est journalisée (ERROR) et n'arrête pas les étapes suivantes ni la protection des positions.

## Commandes

| Commande | Transition | Effet |
|---|---|---|
| `START` | `READY → RUNNING` | crée une `hl_bot_session` (`RUNNING`) avec les hashes de la stratégie et de la politique (INV-06) ; `LIVE` ⇒ `LIVE_NOT_APPROVED` |
| `PAUSE_ENTRIES` | `RUNNING → PAUSED` | plus aucune entrée ; sorties supervisées |
| `RESUME_ENTRIES` | `PAUSED → RUNNING` | reprise des entrées |
| `STOP` | `RUNNING/PAUSED/DEGRADED → STOPPING` | plus aucune entrée ; les positions suivent leurs règles de sortie ; passage automatique `STOPPED` (session close) quand il ne reste ni position ni ordre |
| `EMERGENCY_STOP` | `→ STOPPING` | plus aucune entrée **et** politique de liquidation M1 `LIQUIDATE_ALL` : sortie immédiate de toutes les positions (même mécanique que les sorties), puis `STOPPED` |

Une commande traitée passe `COMPLETED` (avec `result`) ou `REJECTED` (avec `rejection_code` : `STATE_CONFLICT`, `LIVE_NOT_APPROVED`). Une commande dont `deadline_at` est dépassée est `REJECTED` (`DEADLINE_EXCEEDED`). Chaque traitement est audité.

## SCANNER (STRAT-01 Kraken)

- Évaluation **une fois par minute et par paire**, sur la dernière fenêtre de 5 minutes **closes** : `window = [m − 5 min, m)` avec `m` = minute UTC courante. Une paire est évaluée si son flux est `LIVE` ; sinon elle est comptée `UNKNOWN`.
- **Features** (05-strategies/02) calculées à partir de `hl_trade_minute` et du carnet :
  - `volume_spike` = Σ notionnel de la fenêtre / max(médiane des Σ notionnels des 12 fenêtres de 5 min précédentes, 1 USD). La fenêtre courante doit être **entièrement `live`** (5 minutes) ; une fenêtre de référence est utilisable si ses 5 minutes existent (`live` prioritaire, sinon `backfill`) ; moins de 6 fenêtres de référence ⇒ `UNKNOWN` (`INSUFFICIENT_HISTORY`). `uses_backfill` est consigné.
  - `buy_notional_share` = Σ notionnel acheteur / Σ notionnel, fenêtre courante `live` ; notionnel nul ⇒ `UNKNOWN`.
  - `price_change_5m` = ln(dernier `close` de la fenêtre / dernier `close` avant la fenêtre) ; prix manquant ⇒ `UNKNOWN`.
  - `spread_bps` = carnet courant.
  - `window_notional_usd`, `window_trade_count` (ADR-014) = sommes de la fenêtre courante, uniquement si elle est entièrement `live`, sinon `UNKNOWN`.
- **TTL** : pour les features de fenêtre, la fraîcheur est garantie par (a) le flux `LIVE` et (b) une fenêtre égale à la plus récente fenêtre close ; `ttl_ms` de la stratégie s'applique à l'âge du dernier message du flux (07-data/03).
- Conditions évaluées selon la stratégie (`gt/gte/lt/lte`, `Decimal`). Toute condition `UNKNOWN` ⇒ `SKIP` ; toutes vraies ⇒ **candidat**. Exclusions préalables : position ouverte ou intention en cours sur la paire pour ce bot, cooldown (`cooldown_seconds` de la stratégie, à défaut celui de la politique) depuis la dernière sortie.
- Seuls les candidats sont persistés (`hl_candidate`, unique par fenêtre) avec leurs features ; les évaluations négatives et `UNKNOWN` sont **comptées** (journal et résumé), pas stockées.
- Événement `CANDIDATE_DETECTED` ; le `correlation_id` de toute la chaîne (assessment, sizing, ordre, position, sortie) est l'identifiant du candidat.

## RUG CHECK — contrôle de qualité de marché (ADR-004)

Critères de la [variante CEX](01-architecture-agents.md#rug-check--variante-cex-adr-004), évalués au moment du candidat, chacun `PASS/FAIL/UNKNOWN` avec valeur, seuil, source et `observed_at` :

| Critère | Règle M1 |
|---|---|
| `pair_status` | statut `online` |
| `data_freshness` | flux `LIVE` (connexion vivante, snapshot, checksum valide) |
| `spread` | `spread_bps ≤ max_spread_bps` |
| `min_order_liquidity` | une vente **et** un achat de la taille minimale `max(qty_min × prix, cost_min)` passent dans le carnet avec un glissement ≤ `max_slippage_pct` (la profondeur pour la taille réelle est imposée par le SIZER via `liquidity_cap`) |
| `abnormal_move` | `|price_change_5m| ≤ ln(1 + max_abnormal_move_pct/100)` |
| `volume_floor` | `volume_24h_usd ≥ min_volume_24h_usd` (référentiel `hl_instrument`) |
| `cooldown` | pas de sortie sur la paire depuis moins de `cooldown_seconds` |

Verdict : un `FAIL` ou un `UNKNOWN` ⇒ `REJECT` ; sinon `PASS`. `hl_risk_assessment` est écrit dans les deux cas ; événements `RISK_PASSED` / `RISK_REJECTED` (critères en échec). Candidat `REJECTED` ou poursuite.

## SIZER

Formule de [05-strategies/03](../05-strategies/03-risque-mathematique.md#risk-capital-et-dimensionnement), en `Decimal` :

- `E` = equity conservatrice (EQUITY ci-dessous) ; `UNKNOWN` ⇒ refus `EQUITY_UNKNOWN`.
- `r = max_trade_risk_pct / 100` ; `S = initial_stop_pct/100 + 2 × taker_fee` (stop + frais d'entrée et de sortie), plancher `s_min = 0,5 %`.
- `risk_budget = E × r` ; `notional_risk = risk_budget / S`.
- `notional_approved = min(notional_risk, Q, cash_available, liquidity_cap, portfolio_room)` avec `Q = E × max_token_exposure_pct/100`, `cash_available` = cash libre − frais estimés, `liquidity_cap` = plus grand notionnel dont l'achat (asks) **et** la revente (bids) restent dans `max_slippage_pct`, `portfolio_room = E × max_portfolio_exposure_pct/100 − exposition au coût des positions ouvertes`.
- Blocages préalables (refus, sans calcul) : `kill_switch`, positions ouvertes ≥ `max_open_positions`, perte du jour UTC ≥ `max_daily_loss_pct`, drawdown ≥ `max_drawdown_pct` (définitions EQUITY).
- Quantité = `notional_approved / meilleur ask`, **arrondie vers le bas** à `qty_precision` ; refus si `qty < qty_min` ou `qty × prix < cost_min`.
- `hl_sizing_decision` toujours écrit (approuvé ou non) avec `limits_applied` (chaque borne et sa valeur), `rejection_reasons`, `input_hash` (hash des entrées) et `expires_at = now + authorization_ttl_seconds`. Événement `POSITION_SIZED` ou `RISK_REJECTED`.

## EXECUTOR paper (08-risque-execution/02, ADR-010)

- **Paramètres de venue paper** (constantes de code, documentées ici) : frais taker **0,40 %**, latence simulée **250 ms**, liquidité `taker`, frais en USD.
- **Entrée** — une seule transaction : vérifier l'autorisation (approuvée, non expirée, carnet toujours `LIVE`) ; créer `hl_order_intent` (`id` = UUID5 déterministe de la décision de sizing ; `idempotency_key` unique : une seconde exécution de la même autorisation est impossible, INV-04) ; `RESERVE` au ledger (`notional + frais`) ; après la latence simulée, balayage des **asks du carnet courant** jusqu'à la quantité, prix limite `ask_ref × (1 + max_slippage_pct/100)` (comportement IOC) ; `hl_order` + `hl_fill` (un fill par niveau consommé, `book_snapshot` des niveaux) ; `BUY_FILL` depuis le cash réservé ; `RELEASE` du reliquat ; position `OPEN`.
- **Fill partiel** : la position est ouverte pour la quantité exécutée, le reliquat n'est jamais compté (QA-06). Aucun fill ⇒ ordre `EXPIRED`, intention `EXPIRED`, réservation libérée, candidat `EXPIRED`.
- **Atomicité** : la transaction unique rend un crash sans effet partiel ; au redémarrage, une intention `CREATED` sans ordre (impossible par construction) serait passée `EXPIRED` par la réconciliation.
- **Position** : `avg_entry_price` (moyenne pondérée des fills), `initial_stop_price = avg × (1 − initial_stop_pct/100)`, `take_profit_price = avg + take_profit_r × (avg − stop)` (seuils hors frais, STRAT-01), `r0_usd = (avg − stop) × qty + frais d'entrée + frais de sortie estimés au stop`, `cost_basis` = Σ notionnels **hors frais** (les frais vont au compte `fees`).
- Événements `ORDER_INTENT_CREATED`, `ORDER_SUBMITTED`, `ORDER_FILLED` ou `ORDER_PARTIAL_FILL`, `POSITION_OPENED`, `LEDGER_POSTED`.
- **Chaque événement de candidat (`RISK_PASSED`, `RISK_REJECTED`, `POSITION_SIZED`), d'ordre, de position et de ledger porte `instrument_id`** dans son payload (correctif du 2026-11-20 : une clôture survenant plus d'une heure après le candidat s'affichait sans symbole, le terminal ne pouvant plus remonter au `CANDIDATE_DETECTED`).
- **Latence mesurée** : `decision_to_fill_ms` (de la décision de sizing au fill) est inclus dans `ORDER_FILLED` et dans le journal INFO de l'entrée ; au-delà de `authorization_ttl_seconds`, l'autorisation est expirée et l'entrée refusée (`AUTHORIZATION_EXPIRED`). Constat de l'essai réel : une entrée à ~10 s non reproduite lors d'un diagnostic de 4 min (aucun retard de boucle > 0,3 s, aucune attente de verrou > 0,2 s) — la mesure reste en production.

## Sorties

Pour chaque position `OPEN`/`REDUCING`, à chaque itération, à partir du **meilleur bid** d'un carnet `LIVE` :

| Motif | Condition |
|---|---|
| `STOP` | `best_bid ≤ initial_stop_price` |
| `TAKE_PROFIT` | `best_bid ≥ take_profit_price` |
| `TIME` | âge de la position ≥ `max_hold_seconds` |
| `STATUS` | statut de paire ≠ `online` et `exit_on_status_change` |
| `EMERGENCY` | `EMERGENCY_STOP` du bot |

- Ordre de sortie `SELL` (intention `EXIT` liée à la position), balayage des **bids**, prix plancher `best_bid × (1 − 5 %)` (glissement de sortie plus large que l'entrée : la protection prime) ; frais taker.
- La vente est possible si le statut de paire est `online`, `reduce_only` ou `limit_only` ; sinon (`cancel_only`, `post_only`, `maintenance`…) la sortie est **différée** et journalisée (WARNING) à chaque nouvelle tentative, sans alerte en double.
- Carnet non `LIVE` ⇒ sortie différée (pas de prix fiable) ; journalisée.
- Coût FIFO : un seul lot par position, coût libéré au prorata de la quantité vendue. `SELL_FILL` au ledger (08-*/03 : cash, frais, inventaire, PnL réalisé). Vente partielle ⇒ `REDUCING`, reliquat retenté à l'itération suivante.
- Clôture : `CLOSED`, `closed_at`, `exit_reason`, `realized_pnl_net` (produit net des ventes − coût − frais d'entrée et de sortie), `R` réalisé = `realized_pnl_net / r0_usd` ; événements `POSITION_REDUCED` / `POSITION_CLOSED`.

## EQUITY, contrôles continus et réconciliation

- **Valorisation conservative** : valeur d'une position = produit de la **vente simulée** de toute sa quantité sur les bids actuels, moins les frais taker ; un carnet non `LIVE` ⇒ valeur `UNKNOWN`. `equity = cash + cash_reserved + Σ valeurs` ; `UNKNOWN` si une valeur l'est (jamais remplacée par le coût).
- **Instantané** `hl_equity_snapshot` toutes les **60 s** et après chaque fill (`quality` `OK`/`STALE`), événement `EQUITY_UPDATED` (bot).
- **Perte du jour** = (equity courante − equity du premier instantané `OK` du jour UTC − apports nets du jour) / equity de début de jour ; bloque les entrées si ≤ −`max_daily_loss_pct` jusqu'au jour UTC suivant.
- **Drawdown** sur la courbe de performance `P = equity − owner_capital` : `(max P − P) / (owner_capital + max P)` ; bloque les entrées au-delà de `max_drawdown_pct` (réarmement manuel ultérieur, M1.4+).
- **Réconciliation** (au démarrage, après chaque fill, toutes les 5 min) : pour chaque bot, (a) inventaire au coût du ledger par instrument = Σ `cost_basis` des positions ouvertes, (b) `cash ≥ 0` et `cash_reserved = 0` hors exécution en cours, (c) fills = écritures `BUY_FILL`/`SELL_FILL` correspondantes, (d) intentions `CREATED` orphelines passées `EXPIRED`. Résultat `RECONCILIATION_OK` (journal) ou `RECONCILIATION_FAILED` (événement + bot `DEGRADED` : entrées bloquées, sorties maintenues).

## Modèle de données (complète 10-backend/03)

`hl_position` reçoit `realized_pnl_net` (Dec), `fees_paid` (Dec, entrée + sortie), `exit_reason` (texte). Aucune autre table nouvelle.

## Compléments M1.4

Marquage des positions à chaque itération EQUITY et événement `SCAN_CYCLE_COMPLETED` (échecs par condition) : voir [10-backend/04](../10-backend/04-api-m1.md#persistance-ajoutée-côté-worker-complète-06-agents03).

## Journalisation (complète 07-data/03)

Formats : prix et quantités journalisés avec 10 chiffres significatifs au plus (les valeurs stockées restent exactes). INFO : commande traitée, candidat détecté (paire, features clés), assessment rejeté (critères), sizing (notionnel, motif de limite), ordre exécuté (quantité, prix moyen, frais), position ouverte/fermée (motif, PnL net, R) ; **résumé toutes les 5 min** par bot (statut, paires évaluées, `UNKNOWN`, candidats, rejets, positions ouvertes, equity). WARNING : sortie différée, entrée bloquée par une limite, réconciliation en échec.

## Tests M1.3 (sans réseau)

| ID | Vérification |
|---|---|
| T-M1.3-01 | features : spike/buy share/variation sur minutes synthétiques ; fenêtre incomplète ou historique insuffisant ⇒ `UNKNOWN` ; backfill signalé |
| T-M1.3-02 | évaluation des conditions ; `UNKNOWN ⇒ SKIP` ; cooldown et position existante excluent |
| T-M1.3-03 | RUG CHECK : chaque critère en échec ⇒ `REJECT` ; flux non `LIVE` ⇒ `REJECT` (QA-01) ; statut `cancel_only` ⇒ `REJECT` (QA-16) |
| T-M1.3-04 | SIZER : formule numérique, chaque borne gagnante, arrondi vers le bas, stop plus proche ⇒ budget respecté (QA-03), liquidité insuffisante ⇒ refus (QA-04) |
| T-M1.3-05 | balayage du carnet : prix moyen pondéré, frais, limite de glissement, fill partiel (QA-18, QA-06) |
| T-M1.3-06 | EXECUTOR : ledger équilibré, réservation libérée, position correcte ; même autorisation rejouée ⇒ un seul ordre (QA-05) ; crash dans la transaction ⇒ aucun effet |
| T-M1.3-07 | sorties STOP, TAKE_PROFIT, TIME, STATUS ; vente partielle ; PnL et R nets ; sortie différée si carnet périmé ou paire non vendable |
| T-M1.3-08 | commandes : START crée la session (hashes), PAUSE bloque les entrées mais pas les sorties, STOP → STOPPED quand plus de position, EMERGENCY_STOP liquide (QA-09), LIVE refusé (QA-19) |
| T-M1.3-09 | démarrage automatique : une seule fois, jamais après pause/arrêt, jamais en LIVE (ADR-013) |
| T-M1.3-10 | EQUITY : valeur conservative, `UNKNOWN` si carnet périmé, perte du jour et drawdown bloquent les entrées, dépôt hors PnL |
| T-M1.3-11 | réconciliation : OK nominal ; écart provoqué ⇒ `RECONCILIATION_FAILED` + bot `DEGRADED` |
| T-M1.3-12 | bout en bout : minutes + carnet synthétiques ⇒ candidat → assessment → sizing → ordre → position → sortie TP, chaîne entière sous un même `correlation_id`, ledger équilibré |
| T-M1.3-13 | migration `domain` des nouvelles colonnes (dérive PostgreSQL) |
