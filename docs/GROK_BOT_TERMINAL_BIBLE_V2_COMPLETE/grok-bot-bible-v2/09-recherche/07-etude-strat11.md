# Étude STRAT-11 « Rotation des plus fortes » hors production (ADR-024)

> **0-hl — Bible V2.1 | 21 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Étude préalable au lancement paper de STRAT-11 ([05-strategies/02 « STRAT-11 — Variante Kraken »](../05-strategies/02-catalogue-strategies.md)). Coûts et découpage de 09-recherche/05 ; le signal est **transversal** (il compare les paires entre elles), d'où une simulation au niveau du portefeuille.

## Exécution

`python -m app.domain.trading.research.strat11_study [--days N] [--json]` dans le conteneur worker, **lecture seule** (60 jours par défaut). Le seuil de sortie v0.1.0 (rang > 8) est hors grille pour N = 3 : il est rejoué à part et affiché comme « STRAT-11 v0.1.0 ».

## Méthode

- **Signal** : à chaque minute close `m`, `trend_24h` de toutes les paires puis `rs_rank_24h`, `rs_24h`, `btc_trend_24h`, par **le code du SCANNER** ; aucune donnée postérieure à `m`.
- **Portefeuille** : au plus `N` positions simultanées (le nombre de places de la variante) ; à une minute donnée, les paires candidates sont prises **par rang croissant** tant qu'il reste une place. Une position par paire, cooldown 24 h.
- **Sortie** : sortie sur condition `rs_rank_24h > M` (au `close` de la minute précédente), stop `x`, limite de temps `H` ; stop prioritaire.
- **Grille (48 variantes)** : places et seuil d'entrée `N ∈ {2 ; 3 ; 5}` (`rs_rank_24h ≤ N`) ; sortie `M ∈ {N + 3 ; 10}` ; filtre de régime `btc_trend_24h ≥ −3 %` : avec ou sans ; stop `x ∈ {6 ; 10} %` ; `H ∈ {2 ; 4} jours`. Fixes : `rs_24h > 0`, `window_notional_usd ≥ 25 000`.
- **Référence** : **toutes** les paires classables achetées chaque jour à 00:00 UTC, mêmes stop et limite de temps, sans sortie sur condition (dérive de l'univers moins les frais).

## Critères de lancement

Ceux de 09-recherche/05, avec deux adaptations fixées dès maintenant (détentions de plusieurs jours, très peu de trades) :

1. l'étude n'est **jugeable** qu'avec **au moins 28 jours** de données ; avant, le verdict affiché est « NON JUGEABLE » quels que soient les chiffres ;
2. le seuil de nombre de trades sur la partie 2 est de **15** (au lieu de 20).

Les autres critères sont inchangés (net moyen > 0, profit factor ≥ 1,2, brut ≥ référence + 0,3 point, net de la partie 1 > 0).

## Résultats

*À compléter ; première exécution jugeable vers le 15 décembre 2026.*

## Limites

Un mois de données couvre un seul régime de marché ; le momentum transversal est connu pour ses retournements brutaux ; 40 paires seulement.

## Tests

| ID | Vérification |
|---|---|
| T-R31 | `rs_rank_24h` : rang exact sur un univers connu, ex-aequo départagés, `UNKNOWN` sous 20 paires classables ; `rs_24h` et `btc_trend_24h` exacts ; `UNKNOWN` sans BTC/USD |
| T-R32 | portefeuille : jamais plus de `N` positions ; candidates prises par rang ; sortie dès `rs_rank_24h > M` |
| T-R33 | verdict « NON JUGEABLE » sous 28 jours ; seuil de 15 trades |
