# Formules du risque, PnL et métriques

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Risk capital et dimensionnement

Définir `E` = equity conservatrice, `r` = fraction de risque autorisée (exemple d'essai 0.5%, non promesse), `S` = distance au stop en fraction du prix d'entrée *incluant les coûts de sortie*, `Q` = notional maximum. Alors `risk_budget = E*r`, `notional_risk = risk_budget / max(S, s_min)`, `notional_approved = min(notional_risk, Q, cash_available, liquidity_cap, portfolio_room)`. `risk_per_trade` est une **perte budgétée**, pas la proportion de capital engagé.

Si un marché illiquide ne garantit pas l'exécution au stop, imposer une surcharge `tail_risk_buffer` ou refuser. Les limites globales s'appliquent après sizing. L'arithmétique monétaire est décimale et les tailles sont arrondies vers le bas aux contraintes de lot et tick size.

## Risque du portefeuille

Expositions par chaîne, token, venue, stratégie et clusters corrélés ; soldes libres, réservés et en transit. En cas de double signal sur un token, agréger l'intention avant calcul de risque ; ne pas répartir les limites par agent de manière à contourner le cap global. `max_daily_loss` se mesure avec convention UTC et déduction des frais. `max_drawdown` = chute relative depuis la plus haute equity marquée selon cadence et méthode documentées.

## R-multiple

`R0` = perte ex ante depuis prix moyen d'entrée jusqu'au stop initial, frais estimés inclus. `R_multiple_realized = net_PnL / R0` si R0 > 0 et documentation complète. Le changement dynamique de stop ne réécrit pas R0 ; enregistrer `current_risk` séparément. Un take-profit 2R est un objectif, jamais un gain garanti.

## PnL et equity

`realized_PnL` = produit net des fills de vente − coût de base des unités sorties − frais et gas imputables ; définir FIFO ou coût moyen et le figer. `unrealized_PnL` = valeur liquidative exécutable estimée − coût restant ; une simple dernière cotation est une approximation à marquer. `equity = cash + positions_mark_value + autres actifs − dettes`, plus convention d'inclusion des coûts de liquidation.

## Statistiques

- `win_rate = n_positive / n_closed` ; traiter breakeven explicitement.
- `profit_factor = sum(positive_net_PnL) / abs(sum(negative_net_PnL))`, inconnu si aucune perte.
- `expectancy = win_rate*avg_win − loss_rate*avg_loss` ; comparer au coût de capital.
- `max_drawdown` sur série equity *net de cash flows* ; mesurer duration et time-under-water.
- `Sharpe/Sortino` seulement si régularité et convention des retours compatible ; ne pas les annualiser naïvement sur memecoins.
- `turnover`, frais/gas, slippage vs quote, rejets, fill ratio, drawdown intra-trade.

## Circuit breakers

Mode `BLOCK_NEW_ENTRIES` si pertes seuil, données périmées, écart ledger, trop d'ordres inconnus, prix anormal, explosion de latence ou liquidation non maîtrisée. Les positions existantes conservent un service de protection indépendant. Deux approbations distinctes pour abaisser les protections en LIVE.
