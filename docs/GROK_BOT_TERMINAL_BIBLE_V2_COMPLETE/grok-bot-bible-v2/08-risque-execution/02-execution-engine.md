# Execution Engine — CEX, DEX, idempotence

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Adaptateur commun

`quote(intent)`, `simulate(intent)`, `submit(order)`, `get_order(id)`, `cancel_order(id)`, `get_fills(account,since)`, `get_balances()`, `health()`. CEX : mapping orderbook/limit/market. DEX : routeur/AMM, simulation de transaction, nonce, gas, broadcast et confirmation on-chain. Ces deux modèles ne partagent pas exactement les mêmes états.

## Machine à états d'ordre

`CREATED → VERIFIED → SUBMITTING → ACKED → PARTIALLY_FILLED / FILLED`. Branches : `REJECTED`, `CANCEL_PENDING → CANCELLED`, `EXPIRED` ou `UNKNOWN`. DEX peut distinguer `BROADCAST`, `IN_BLOCK`, `CONFIRMED`, `FINALIZED`, `REORGED`. En cas de réorganisation, compensation du ledger et nouvelle réconciliation.

## Idempotence

Créer `intent_id` déterministe et clé opaque `client_order_id`. Avant retransmission après timeout, interroger l'API distante et les transactions blockchain par identifiant/nonce. Un statut incertain **bloque** une nouvelle entrée sur le même token/compte. Test obligatoire : crash juste après broadcast ; reprise puis aucune duplication.

## Prévention des sorties impossibles

Avant entrée, estimer la liquidation de la taille prévue à prix impacté ; détecter les tokens à restrictions de vente lorsque les sources le permettent, mais aucune simulation ne remplace la certitude d'une sortie future. Si profondeur insuffisante ou taxe élevée, refuser. Les prix de dernier swap ne remplacent pas un prix exécutable.

## Exécution adverse

Mesurer `implementation_shortfall = realised_execution_cost - benchmark_cost` selon convention, le spread, le coût de gas, les fees, le price impact et la latence. Bornes retry, prix maximal d'achat, `min_out` de vente, date limite et gestion du partial fill. Ne jamais envoyer un ordre aveugle pour compenser une erreur de validation.

## Adaptateurs Kraken (ADR-002, ADR-009, ADR-010)

Deux implémentations de l'adaptateur commun, interchangeables pour les agents :

**`KrakenPaperAdapter` (premier jalon).** `quote`/`simulate` consomment le carnet Kraken réel en mémoire (côté ask pour un achat, bid pour une vente) niveau par niveau jusqu'à la quantité demandée, en appliquant `lot_decimals`/`tick_size`, `ordermin`/`costmin` et les frais taker (ADR-010). `submit` exécute immédiatement contre le carnet au moment de la soumission (pas au moment de la décision : la latence simulée par défaut est de 250 ms, re-cotation incluse) ; si la profondeur disponible dans la limite de prix (`max_slippage_pct`) est insuffisante, l'ordre est **partiellement rempli** et le reliquat est annulé (comportement IOC). Carnet `STALE` ⇒ `REJECTED (DATA_STALE)`, jamais de fill sur un prix périmé. Chaque fill paper stocke l'instantané des niveaux consommés. `get_order` relit l'état persistant (les crashes sont rejouables, INV-04). Pas de stop/TP côté venue en paper : le moteur les surveille sur le meilleur bid.

**`KrakenPrivateAdapter` (LIVE futur, non implémenté).** Ordres limit IOC avec prix plafond (achat) / plancher (vente) plutôt que market, `cl_ord_id` = `client_order_id`, introspection `QueryOrders`/`OpenOrders` par `cl_ord_id` avant toute retransmission, réconciliation via `TradesHistory` et `Balance`. Stops de protection posés côté Kraken lorsque c'est possible, avec vérification de leur présence effective.

**Statuts.** Mapping Kraken (`pending`, `open`, `closed`, `canceled`, `expired`) → machine à états ci-dessus ; tout statut non mappé ⇒ `UNKNOWN`.
