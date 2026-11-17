# Théorie de marché et construction des signaux

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Hypothèse vs preuve

Une stratégie n'est pas une vérité du marché. Toute stratégie exprime une **hypothèse falsifiable** : par exemple « un pic d'activité accompagnant une cassure et une profondeur suffisante peut précéder une continuation de court terme ». Les marchés DEX récents sont fortement non stationnaires, influencés par des coûts d’exécution et très susceptibles de manipulation. Le signal ne garantit aucun résultat.

## Rendements et volatilité

Rendement logarithmique : `r_t = ln(P_t / P_{t-1})`; volatilité réalisée sur `n` observations : `sqrt(sum((r_i-mean(r))^2)/(n-1)) * sqrt(annualisation)` si hypothèses raisonnables. Pour un token à observations sporadiques, privilégier temps événementiel et éviter l'annualisation irréaliste. Vérifier la validité du prix `P_t` (quote, fill, TWAP, pool spot).

## Volume spike

`V_current = volume USD sur [t-5min,t]`; `V_baseline = median(V_i sur k fenêtres précédentes non chevauchantes)`; `spike = V_current / max(V_baseline, epsilon)` ; si moins de `k_min` fenêtres, signal `INSUFFICIENT_HISTORY`. La capture mentionne 4.1x, 5.8x sans révéler son dénominateur : le calcul V2 est un choix de conception.

## Pression acheteuse

Version comptage : `buy_count / (buy_count+sell_count)` ; version notionnelle : `buy_notional/(buy_notional+sell_notional)`. Conserver **les deux** avec noms distincts ; normaliser les swaps multi-hop et leur sens du point de vue du token cible. Un wallet exécutant plusieurs buys n'équivaut pas à plusieurs investisseurs.

## Breakout

`previous_high_1h(t) = max(highs completed candles in [t-60min,t))`; un signal `crosses_above` est vrai uniquement lorsque le dernier prix **exécutable** franchit le niveau sous confirmation de volume/liquidité. Ignorer les chandeliers incomplets pour établir le seuil ; bruit de microstructure et wick manipulation à tester.

## Order flow et microstructure

`OFI` d'un carnet CEX = variation de profondeur disponible aux meilleurs niveaux acheteur/vendeur ; sur AMM sans order book, dériver des flux de swaps et de l'évolution de réserves, **pas** prétendre disposer d'un spread bid/ask de carnet. Pour AMM CPMM `x*y=k` hors frais, le prix marginal relatif dépend des réserves et la taille affecte le prix moyen. Les frais DEX, gas, MEV et slippage doivent être déduits du rendement brut.

## Données de risque des tokens

Indicateurs : top-N concentration, parts de wallets de développeurs **uniquement si attribution fondée**, capacité de transfert, honeypot, frais de vente, mécanismes mint/blacklist, verrouillage LP (montant, durée, lock address), opportunité de vente réelle. Un LP lock ne garantit pas l'absence de fraude : le développeur peut changer tokenomics ou contrôler d'autres clés. Un cluster wallet n'est pas la preuve d'une manipulation ; reporter le degré de confiance et les alternatives.

## Qualité et données manquantes

Chaque `Feature` est `(value, unit, window, source, observed_at, ingested_at, confidence, freshness, methodology_version)`. Si une métrique obligatoire a une fraîcheur excédée, `UNKNOWN` bloque la nouvelle entrée. Ne jamais convertir une valeur manquante en zéro. Les signaux générés avec data backfilled ultérieurement sont étiquetés et exclus des rapports live reproductibles.

## Sorties et invalidation d'hypothèse

Un stop fixe, un stop ATR, une invalidation de niveau et une sortie temporelle ont des effets différents : garder une politique par stratégie, une couverture du risque indépendante et une règle d'urgence serveur. L'interface affiche `take-profit 2R/3R`, mais la définition du R originel doit être ancrée au fill et au stop initial ; sans stop initial vérifié, ne pas afficher R.
