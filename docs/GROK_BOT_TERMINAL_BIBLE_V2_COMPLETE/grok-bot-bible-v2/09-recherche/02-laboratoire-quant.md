# Laboratoire quantitatif et gouvernance expérimentale

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Workflow scientifique

Hypothèse enregistrée → définition métrique et échantillon → collecte / qualité → baseline → backtest sans fuite → stress → comparaison → comité de validation → paper → monitoring de dérive. La formulation écrite de l'hypothèse précède l'observation du résultat.

## Registre d'expériences

`experiment_id`, author, strategy_version, parameters grid, dataset, seed, baseline, preregistered_metric, notes, metrics, risk findings, approval. Définir un budget de recherche pour éviter la recherche infinie du meilleur score historique.

## Optimisation

Grid/random/Bayesian search possibles en laboratoire uniquement. Chaque meilleure configuration doit subir walk-forward sur données non utilisées pour l'optimisation. Les frontières de validation temporelle sont fixes, et la recherche sur le test final est interdite. Les comparaisons sont corrigées ou au moins annotées pour multiplicité.

## Drift et monitoring

Comparer distributions des features live vs train, exécution simulée vs réelle, taux de rejet, coûts, latence, win rate avec intervalles. Une performance dégradée déclenche revue et revalidation, pas optimisation automatique en LIVE. Rollback à une version déjà approuvée via commande auditée.
