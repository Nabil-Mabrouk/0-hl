# Backtesting événementiel, replay et validation scientifique

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Objectif

Estimer la performance *exécutable* d'une hypothèse avec des données qu'un acteur réel aurait connues, et non optimiser la courbe de capital après coup. Les memecoins DEX ont un biais de survivance particulièrement fort : les pools morts et les transactions impossibles doivent rester dans le dataset.

## Chronologie de simulation

`next_market_event → provider_lag → feature_build → strategy_decision → risk_decision → order_submission_delay → pool_state_replay → fill_or_failure → ledger → portfolio_mark`. Respecter `observed_at` plutôt que `block_time` lorsque disponibilité de données diffère. Un backfill ne réécrit pas une observation passée dans les données de validation.

## AMM constant-product simplifié

Pour réserves `x` et `y`, `x*y=k` hors frais ; la transaction modifie les réserves, donc le coût moyen dépend de la taille. Le moteur doit prendre en charge au moins le cas CPMM, la tarification par ticks pour CLMM et la différence entre route DEX et broker CEX. Faire échouer un ordre si gas, slippage ou réseau insuffisants. Prendre en compte taxes du token, retrait de liquidité et liquéfaction du carnet de swaps.

## Expérimentation

Dataset versionné et hash ; période train/validation/test, purge/embargo autour des découpages temporels si chevauchements, walk-forward, bootstrap (par jour/token cluster), stress des frais ×2–×5, délais ×2–×5, baisse de profondeur, simulations indisponibilité RPC. Toutes les recherches doivent déclarer la multiplicité des essais : éviter data snooping.

## Critères de robustesse

PnL net, max drawdown, perte tail, taille moyenne de gain/perte, execution rate, invalid trades, concentration sur quelques tokens, stabilité des paramètres, performance hors échantillon et drift après activation. Aucun `GO LIVE` basé seulement sur win rate de 30–60 trades ou capital observé sur une capture.

## Reproductibilité

Enregistrer code git sha, dataset checksum, strategy hash, policy hash, simulation seed, moteur/version, coûts appliqués, timezone et horloge. Un même run doit être reproductible bit-à-bit pour les calculs déterministes ou à tolérance bornée pour la simulation stochastique documentée.
