# Risk Engine — règles déterministes et garde-fous

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Hiérarchie

Les limites et seuils sont portés par la `RiskPolicy` versionnée ([`contracts/risk-policy.schema.json`](../contracts/risk-policy.schema.json), défaut paper : [`examples/risk-policy-paper-default.json`](../examples/risk-policy-paper-default.json)).


System Hard Limits > Account Limits > Bot Limits > Strategy Limits > Order Intent. La valeur la plus restrictive prévaut ; un agent ne peut abaisser un cap. Chaque décision fournit une explication structurée de la chaîne de limites.

## Contrôles avant ordre

Solde et réservations, allocation, concentration, pertes intraday, nombre de positions, exposition agrégée, validité du token, volatilité, latence et qualité des sources, spread/slippage, profondeur, coût total et taille minimale. Vérifier l'existence d'une stratégie publiée et d'un stop initial applicable ; calculer risk budget via Decimal.

## Ordre des opérations

`candidate → check sources → estimate exit cost → compute risk → reserve cash → recheck quote → authorize order`; toute modification sensible de prix ou de liquidité invalide l'autorisation antérieure. L'autorisation expire vite et lie explicitement `(bot, strategy_hash, candidate, quote_id, max_notional, policy_version)`.

## Réponse risk-preview

Inclure limites relevées, valeurs courantes, notional demandé et approuvé, prix de référence, coût de sortie pessimiste, exposition et equity avant/après, sources utilisées, motif et code `RISK_...`. Le moteur ne renvoie pas `approved=true` quand les données sont `UNKNOWN`.

## Contrôles continus

Daily loss, drawdown, divergence de ledger, concentration, provider health, délai d'ordres unknown, nombre d'incidents et volatilité. Rupture → `BLOCK_NEW_ENTRIES`, alert, option `CANCEL_PENDING`, maintien d'un contrôleur d'exposition pour les positions ouvertes. Le retour à READY nécessite conditions de réarmement et audit.
