# Étude STRAT-06 « Rebond après excès » hors production (ADR-024)

> **0-hl — Bible V2.1 | 21 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Étude préalable au lancement paper de STRAT-06 ([05-strategies/02 « STRAT-06 — Variante Kraken »](../05-strategies/02-catalogue-strategies.md)). Même méthode, mêmes coûts et mêmes **critères de lancement** que l'étude STRAT-02 ([09-recherche/05](05-etude-strat02.md)) ; seuls le signal, les sorties et la grille changent.

## Exécution

`python -m app.domain.trading.research.strat06_study [--days N] [--json]` dans le conteneur worker, **lecture seule**. Durée indicative : ~2 min pour 40 paires sur 4 jours, ~15 min sur 30 jours (features recalculées à chaque minute par le code du SCANNER).

## Méthode

- **Signal** : à chaque minute close `m` et pour chaque paire, features calculées par **le code du SCANNER**, features de marché comprises (`btc_change_3h`) ; aucune donnée postérieure à `m`.
- **Sortie sur condition** « retour à la moyenne » : à chaque minute de détention `m'`, `gap_to_mean_4h` est recalculée par le code du SCANNER sur les minutes antérieures à `m'` ; dès qu'elle est ≥ 0, sortie au `close` de la minute `m' − 1`. Le stop reste prioritaire.
- **Contraintes** : une position par paire, cooldown 6 h.
- **Grille (324 variantes)** :
  - chute `drop_from_high_3h ≤ −d`, `d ∈ {3 ; 4 ; 6} %` ; `volume_spike ≥ v`, `v ∈ {1,5 ; 2 ; 3}` ; filtre BTC `btc_change_3h ≥ −1,5 %` : avec ou sans ;
  - stop `x ∈ {3 ; 4 ; 6} %` ; sortie : retour à la moyenne, objectif 1,5R ou objectif 2R ; limite de temps `H ∈ {6 ; 12} h`.
  - Fixes : `window_notional_usd ≥ 25 000`, `price_change_5m > 0`, `trend_24h ≥ −10 %`.
- **Référence** : une entrée par paire et par heure pleine, mêmes sorties.

## Critères de lancement

Ceux de 09-recherche/05 (≥ 20 trades sur la partie 2, net moyen > 0, profit factor ≥ 1,2, brut ≥ référence + 0,3 point, net de la partie 1 > 0), fixés avant tout résultat. Sinon : relance hebdomadaire.

## Résultats

*À compléter après la première exécution en production.*

## Limites

Celles de 09-recherche/05 ; en plus, `trend_24h` n'est calculable qu'après 24 h d'historique et un rebond « propre à la paire » est rare : l'échantillon sera petit au début.

## Tests

| ID | Vérification |
|---|---|
| T-R21 | `drop_from_high_3h`, `gap_to_mean_4h`, `trend_24h` exacts sur une série connue ; `UNKNOWN` sous 162/180 et 216/240 minutes, sans close à `t−1440 min` (± 10 min) |
| T-R22 | sortie « retour à la moyenne » : déclenchée dès `gap_to_mean_4h ≥ 0`, calculée sans minute postérieure ; stop prioritaire |
| T-R23 | filtre BTC : une chute simultanée du BTC/USD bloque l'entrée |
