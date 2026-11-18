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
