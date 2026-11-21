# Catalogue des stratégies configurables

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Méthode commune

Chaque fiche : hypothèse ; univers et exclusions ; données/TTLs ; signal calculé ; règles d'entrée ; invalidation ; sizing via Risk Manager ; sorties ; coûts ; risques spécifiques ; paramètres de recherche ; métriques d'évaluation ; critères de publication. Les seuils ci-dessous sont **exemples expérimentaux**, pas recommandations de trading.

## STRAT-01 — Volume Spike Momentum

**Hypothèse testable :** un excès d’activité relatif persiste parfois quelques minutes

**Données et filtre :** volume USD, tx count, prix, liquidity, token age. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** volume_spike > seuil et spread simulé < plafond et confirmation directionnelle. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-01 — Variante Kraken Spot (ADR-005)

Première stratégie implémentée. Les seuils sont des **valeurs de départ expérimentales**, paramètres de la `StrategyVersion` (voir [`examples/strategy-volume-spike.json`](../examples/strategy-volume-spike.json)).

**Univers.** Paires Spot Kraken cotées USD, statut `online`, top-N par volume 24h USD (ADR-003), hors stablecoins et fiat/fiat.

**Données.** Flux `trade` WebSocket v2 (prix, quantité, `side` côté taker, horodatage), carnet `book` (profondeur 10–25 niveaux), `ticker` (bid/ask), bougies `ohlc` 1 min pour l'historique de démarrage. Chaque feature porte `observed_at`, `ingested_at`, `ttl_ms`.

**Features.**
- `volume_spike` = volume notionnel USD sur `[t-5min, t]` / médiane des volumes notionnels de `k = 12` fenêtres de 5 min précédentes non chevauchantes ; moins de `k_min = 6` fenêtres → `INSUFFICIENT_HISTORY` (= `UNKNOWN`).
- `buy_notional_share` = notionnel des trades dont le taker est acheteur (`side = buy`) / notionnel total sur la même fenêtre. Kraken expose le côté taker : pas d'inférence requise. Ne pas confondre avec la version comptage `buy_count_share` (05-strategies/01).
- `price_change_5m` = rendement log du dernier prix entre `t-5min` et `t` (confirmation directionnelle, doit être > 0).
- `spread_bps` = `(ask - bid) / mid × 10 000` depuis le carnet.
- `window_notional_usd` = Σ notionnel de la fenêtre courante `live` ; `window_trade_count` = Σ trades de la fenêtre (ADR-014).

**Entrée (toutes vraies, `UNKNOWN → SKIP`).** *(v0.2.0, ADR-014)* `window_notional_usd ≥ 50 000` et `window_trade_count ≥ 40` (planchers d'activité de la fenêtre courante) ; `volume_spike ≥ 4.0` ; `buy_notional_share ≥ 0.60` ; `price_change_5m > 0` ; `spread_bps ≤ 30` ; contrôle qualité de marché `PASS` (ADR-004) ; aucune position ouverte ni intention en cours sur la paire ; cooldown par paire 30 min après sortie.

**Sortie.** Stop initial 3 % sous le prix moyen d'entrée (R0 ancré au fill, 05-strategies/03) ; take-profit 2R ; sortie temporelle 60 min ; sortie si la paire passe hors `online`. Les stops/TP sont **surveillés par le moteur** en paper (pas d'ordre conditionnel côté venue en paper) ; en LIVE futur, ils seront posés côté Kraken lorsque possible (08-risque-execution/02).

**Coûts.** Frais taker Kraken (ADR-010) à l'entrée et à la sortie, glissement réel par consommation du carnet. Les seuils de stop/TP s'entendent hors frais ; le PnL et le R réalisés sont nets de frais.

**État de validation.** `RESEARCH_ONLY` pour le LIVE ; autorisée en `PAPER` après publication.

## STRAT-02 — Breakout 1h

**Hypothèse testable :** une cassure d’un niveau surveillé entraîne un flux de suivi

**Données et filtre :** OHLCV sans lookahead, volume, profondeur. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** crosses_above(previous_high_1h) avec confirmation et cooldown. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-02 — Variante Kraken Spot « Breakout 1h » (ADR-023)

Deuxième stratégie implémentée, **candidate du gate M2** (13-roadmap « Jalon M2 »). Leçon d'ADR-020 : à frais taker (≈ 0,9 % par aller-retour avec le glissement), une stratégie doit viser des mouvements de **plusieurs heures** et **peu de trades**. Seuils de départ expérimentaux, à confirmer par l'étude [09-recherche/05](../09-recherche/05-etude-strat02.md) ; paramètres de la `StrategyVersion` [`examples/strategy-breakout-1h.json`](../examples/strategy-breakout-1h.json), publiée **sans code** (05-strategies/04).

**Hypothèse testable.** Quand le prix clôture au-dessus du plus haut de l'heure précédente, avec un volume supérieur à la normale et dans une tendance de fond haussière, la hausse se prolonge en moyenne de plus que le coût d'un aller-retour sur les heures qui suivent.

**Univers.** Celui de STRAT-01 (top 40 USD Kraken Spot, hors stablecoins).

**Features nouvelles (SCANNER, 06-agents/03).** Calculées sur les minutes closes, `UNKNOWN → SKIP` :
- `breakout_1h` (log_return) = ln(close de la dernière minute / plus haut des 60 minutes précédentes). > 0 : le prix vient de sortir par le haut du range de l'heure.
- `range_1h` (log_return) = ln(plus haut / plus bas des mêmes 60 minutes) : largeur du range cassé.
- `trend_4h` (log_return) = ln(close de la dernière minute / close d'il y a 240 minutes) : tendance de fond.

**Entrée v0.1.0 (toutes vraies).** `window_notional_usd ≥ 25 000` ; `volume_spike ≥ 2.0` ; `breakout_1h > 0` et `breakout_1h ≤ 0.01` (pas d'achat d'un mouvement déjà parti de plus de 1 %) ; `range_1h ≥ 0.008` et `range_1h ≤ 0.05` (un range assez large pour que la cassure ait un sens, pas un marché désordonné) ; `trend_4h > 0` ; `spread_bps ≤ 30` ; contrôle qualité `PASS` ; une position par paire ; cooldown par paire **4 h** après sortie.

**Sortie v0.1.0.** Stop initial 2,5 % sous le prix d'entrée ; take-profit 2R (+5 %) ; sortie temporelle **6 h** ; sortie si la paire passe hors `online`. Aucun nouveau type de sortie : le schéma de sortie de STRAT-01 suffit.

**Coûts.** Identiques à STRAT-01 (frais taker ADR-010, glissement par le carnet) ; le résultat est jugé **net de frais**.

**État de validation.** `RESEARCH_ONLY` pour le LIVE (ADR-009). Lancement en `PAPER` sur un bot dédié `paper-kraken-02` **seulement** si l'étude 09-recherche/05 remplit ses critères sur la période de contrôle (ADR-023).

## STRAT-03 — Launch Discovery

**Hypothèse testable :** des pools récents liquides offrent de courts épisodes de découverte du prix

**Données et filtre :** pool creation, LP state, réserves, sécurité contractuelle. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** âge pool dans fenêtre et filtres anti-rug forts et contrôle coût de sortie. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-04 — Buy Pressure Imbalance

**Hypothèse testable :** une dominance d’achats notionnels peut indiquer une demande directionnelle

**Données et filtre :** swaps catégorisés, volume, adresses uniques estimées. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** buy_notional_share > seuil sur plusieurs fenêtres et divergence prix filtrée. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-05 — Trend Multi-Timeframe

**Hypothèse testable :** l’alignement de tendances sur TF multiples peut réduire les faux signaux

**Données et filtre :** candles 5m/1h/4h, EMA, ATR. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** EMA rapide > lente et tendance supérieure positive et retest validé. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-06 — Mean Reversion

**Hypothèse testable :** des écarts extrêmes reviennent parfois vers un centre sur marchés liquides

**Données et filtre :** OHLCV, liquidité, spread, z-score. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** z-score sous seuil sur actif stable avec filtre de régime range. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-06 — Variante Kraken Spot « Rebond après excès » (ADR-024)

Troisième stratégie implémentée, **opposée à STRAT-02** : STRAT-02 parie qu'un mouvement se prolonge, STRAT-06 qu'un excès se résorbe. Seuils de départ expérimentaux, à confirmer par l'étude [09-recherche/06](../09-recherche/06-etude-strat06.md) ; paramètres [`examples/strategy-rebound.json`](../examples/strategy-rebound.json).

**Hypothèse testable.** Après une chute rapide et inhabituelle d'une paire liquide, **propre à cette paire** (le BTC ne chute pas en même temps) et hors marché en effondrement, le prix revient en quelques heures vers sa moyenne récente d'un écart supérieur au coût d'un aller-retour.

**Univers.** Celui de STRAT-01 (top 40 USD Kraken Spot, hors stablecoins).

**Features nouvelles (SCANNER, 06-agents/03).**
- `drop_from_high_3h` (log_return) = ln(dernier close / plus haut des 180 minutes précédant la dernière minute) ; ≤ 0.
- `gap_to_mean_4h` (log_return) = ln(dernier close / moyenne des closes des 240 minutes précédant la dernière minute).
- `trend_24h` (log_return) = ln(dernier close / close d'il y a 24 h).
- `btc_change_3h` (log_return) = variation du BTC/USD sur 3 h, identique pour toutes les paires (feature **de marché**, 06-agents/03).

**Entrée v0.1.0 (toutes vraies).** `window_notional_usd ≥ 25 000` ; `drop_from_high_3h ≤ −0.04` (chute d'au moins ~4 % depuis le plus haut des 3 h) ; `volume_spike ≥ 2.0` (chute sur volume, pas par manque d'acheteurs) ; `price_change_5m > 0` (le prix commence à se retourner : on n'attrape pas le couteau qui tombe) ; `btc_change_3h ≥ −0.015` (chute propre à la paire, pas du marché) ; `trend_24h ≥ −0.10` (pas une paire en effondrement) ; `spread_bps ≤ 30` ; contrôle qualité `PASS` ; une position par paire ; cooldown par paire **6 h**.

**Sortie v0.1.0.** Stop 4 % sous le prix d'entrée ; **sortie sur condition** (05-strategies/04 « Sorties sur condition ») `gap_to_mean_4h ≥ 0` : le prix est revenu à sa moyenne des 4 h ; pas de take-profit fixe ; sortie temporelle **12 h** ; sortie si la paire passe hors `online`.

**État de validation.** `RESEARCH_ONLY` pour le LIVE. Lancement `PAPER` sur un bot dédié seulement si l'étude remplit ses critères (ADR-024).

## STRAT-07 — Volatility Breakout

**Hypothèse testable :** une compression de volatilité peut précéder une expansion

**Données et filtre :** ATR, realized vol, niveaux de range. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** compression mesurée puis cassure validée avec liquidité. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-08 — On-chain Netflow Research

**Hypothèse testable :** des variations de flux d’adresses classées peuvent corréler avec l’activité

**Données et filtre :** cluster entity labels, transferts, venues identifiés. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** score de netflow en mode recherche, jamais achat basé sur transfert seul. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-09 — Cross-Venue Dislocation

**Hypothèse testable :** écarts de prix dépassant les coûts totaux peuvent théoriquement offrir un arbitrage

**Données et filtre :** quotes synchronisées, profondeur, frais, transfert. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** spread net exécutable positif après latence/gas/settlement. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-10 — Regime-Aware Selector

**Hypothèse testable :** la performance relative des stratégies dépend du régime

**Données et filtre :** volatilité, tendance, liquidité, volume macro. Vérifier la profondeur et les frais avant entrée. Paramétrer l'âge minimal du marché, sa provenance et la qualité de l'historique.

**Signal d'entrée :** activer seulement stratégies validées pour régime détecté. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** stop d'invalidation, take-profit en R facultatif, limite de temps, sortie sur changement de régime ou risque critique. Le Risk Manager peut réduire ou refuser l'ordre.

**Tests obligatoires :** holdout temporel, split par token/lancement, coûts agressifs, stress de liquidité, perturbation de paramètres, robustesse contre manipulation et latence, comparaison à un baseline simple ; reporter les trades non exécutables.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-11 — Relative Strength Rotation

**Hypothèse testable :** les actifs les plus forts sur un horizon de un à quelques jours le restent en moyenne sur l'horizon suivant (momentum transversal)

**Données et filtre :** rendements comparés de l'univers sur le même horizon, liquidité, spread, régime du marché. Vérifier la profondeur et les frais avant entrée.

**Signal d'entrée :** rang de la paire parmi l'univers sous un seuil, surperformance du marché de référence, filtre de régime. Chaque métrique possède une unité, une fenêtre temporelle, un seuil déclaré et un comportement `UNKNOWN → SKIP`.

**Sortie :** sortie quand la paire quitte le haut du classement (hystérésis), stop d'invalidation large, limite de temps.

**Tests obligatoires :** holdout temporel, coûts agressifs, comparaison à la détention égale de tout l'univers, sensibilité à l'horizon de classement.

**État de validation par défaut :** `RESEARCH_ONLY` ; aucune activation LIVE automatique.

## STRAT-11 — Variante Kraken Spot « Rotation des plus fortes » (ADR-024)

Quatrième stratégie implémentée : **très peu de trades**, détentions de plusieurs jours, la mieux adaptée aux frais taker. Paramètres [`examples/strategy-rotation.json`](../examples/strategy-rotation.json) avec la policy dédiée [`examples/risk-policy-rotation.json`](../examples/risk-policy-rotation.json) ; étude [09-recherche/07](../09-recherche/07-etude-strat11.md).

**Hypothèse testable.** Les 3 paires qui ont le mieux progressé sur 24 h parmi le top 40 Kraken USD, si elles battent le BTC, continuent de faire mieux que la moyenne de l'univers sur les jours suivants, d'un écart supérieur aux frais.

**Features nouvelles (SCANNER, features de marché, 06-agents/03).**
- `trend_24h` (commune avec STRAT-06).
- `rs_24h` (log_return) = `trend_24h` de la paire − `trend_24h` du BTC/USD.
- `rs_rank_24h` (rank) = rang de la paire selon `trend_24h` parmi les paires de l'univers dont `trend_24h` est connue (1 = la plus forte) ; `UNKNOWN` si moins de 20 paires sont classables.
- `btc_trend_24h` (log_return) = `trend_24h` du BTC/USD (régime du marché).

**Entrée v0.1.0.** `rs_rank_24h ≤ 3` ; `rs_24h > 0` ; `btc_trend_24h ≥ −0.03` (pas en plein repli du marché) ; `window_notional_usd ≥ 25 000` ; `spread_bps ≤ 30` ; contrôle qualité `PASS` ; une position par paire ; cooldown par paire **24 h**. Au plus 3 positions simultanées (policy).

**Sortie v0.1.0.** **Sortie sur condition** `rs_rank_24h > 8` (la paire est sortie du haut du classement ; l'écart entre 3 et 8 évite les allers-retours) ; stop **10 %** ; sortie temporelle **4 jours** ; sortie si la paire passe hors `online`.

**Policy dédiée `rotation`.** Le stop large rend la taille calculée par le SIZER à 0,5 % de risque minuscule (5 % du capital) : la policy `rotation` porte `max_trade_risk_pct` à **3** (≈ 30 % du capital par position avec un stop de 10 %), `max_token_exposure_pct` à **33**, `max_open_positions` à **3**, `max_daily_loss_pct` à **6** (portefeuille presque entièrement investi), le reste comme `paper-default`.

**État de validation.** `RESEARCH_ONLY` pour le LIVE. L'étude n'est **jugeable qu'à partir de 4 semaines de données** (détentions de plusieurs jours) ; d'ici là elle tourne à titre indicatif.

