# Étude STRAT-02 « Breakout 1h » hors production (ADR-023)

> **0-hl — Bible V2.1 | 21 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Étude préalable au lancement paper de STRAT-02 ([05-strategies/02 « STRAT-02 »](../05-strategies/02-catalogue-strategies.md)). Même méthode que l'étude STRAT-01 ([09-recherche/03](03-etude-strat01.md)), qu'elle réutilise : seuls le signal et la grille changent.

## Exécution

`python -m app.domain.trading.research.strat02_study [--days N] [--json]` dans le conteneur worker, **lecture seule**, jamais importé par le worker ni par l'API.

## Méthode

- **Données, prix, frais, glissement, sorties, trous de données, découpage 60 % / 40 %** : identiques à 09-recherche/03 (entrée au `close` + 0,10 %, frais taker 0,40 % par côté, stop prioritaire dans une même minute, trade exclu si une minute manque pendant la détention).
- **Signal** : à chaque minute close `m` et pour chaque paire, les features de STRAT-02 sont calculées par **le code du SCANNER** (aucune donnée postérieure à `m`, INV-03). Spread non rejoué (comme STRAT-01) ; le backtest M2.2 le rejoue ensuite sur les instantanés de carnet.
- **Contraintes** : une position par paire ; cooldown de 4 h après sortie.
- **Grille (324 variantes)** :
  - `volume_spike ≥ v`, `v ∈ {1,5 ; 2 ; 3}` ;
  - filtre de tendance `trend_4h > 0` : avec ou sans ;
  - plafond de poursuite `breakout_1h ≤ c`, `c ∈ {0,5 % ; 1 %}` ;
  - stop `x ∈ {1,5 ; 2,5 ; 4} %` ; objectif `k ∈ {1,5 ; 2 ; 3}` R ; limite de temps `H ∈ {2 ; 6 ; 12} h`.
  - Fixes : `window_notional_usd ≥ 25 000`, `0,8 % ≤ range_1h ≤ 5 %`.
- **Référence** : les mêmes sorties appliquées à une entrée **par paire et par heure pleine**, sans filtre (dérive du marché moins les frais).

## Critères de lancement paper (ADR-023)

La variante retenue est la meilleure de la **partie 1** (au moins 10 trades) ; elle est jugée **sur la partie 2 seulement**. Lancement de `paper-kraken-02` si **toutes** ces conditions sont vraies sur la partie 2 :

1. au moins **20 trades** ;
2. résultat **net** moyen par trade **> 0** ;
3. profit factor net **≥ 1,2** ;
4. résultat brut moyen supérieur à celui de la référence d'au moins **0,3 point** ;
5. résultat net de la partie 1 également > 0.

Sinon : aucune publication, l'étude est relancée **chaque semaine** à mesure que l'historique s'allonge (180 jours de rétention, M2.1), et le résultat de chaque exécution est consigné ci-dessous. Le nombre de variantes essayées figure toujours dans le rapport (risque de faux positif).

## Sorties

Pour la v0.1.0 (v = 2, tendance oui, c = 1 %, x = 2,5 %, k = 2, H = 6 h), la référence et les 10 meilleures variantes de la partie 1 : trades, exclus, taux de gain, brut et net moyens, profit factor, durée moyenne de détention, part du meilleur actif dans le résultat, sur chaque partie ; puis le verdict des 5 critères.

## Résultats

### Exécution du 2026-11-21 `[OBS]`

Période 2026-11-17 22:44 → 2026-11-21 00:17 UTC (≈ 3,1 jours, coupure 2026-11-19 18:51), 40 paires, 48 747 minutes évaluables, 324 variantes.

| Ligne | Partie | Trades | Gagnants | Brut moyen | Net moyen | PF |
|---|---|---|---|---|---|---|
| v0.1.0 | 1 | 13 | 23,1 % | −1,154 % | −1,942 % | 0,09 |
| v0.1.0 | 2 | 10 | 0,0 % | −1,755 % | −2,538 % | 0,00 |
| Référence (sorties v0.1.0) | 1 | 60 | 13,3 % | −0,849 % | −1,639 % | 0,15 |
| Référence (sorties v0.1.0) | 2 | 73 | 15,1 % | −1,128 % | −1,916 % | 0,12 |
| Variante #1 (v ≥ 3, tendance, c ≤ 0,5 %, H = 6 h, stop 1,5 %, 1,5R) | 1 | 13 | 46,2 % | +0,186 % | −0,613 % | 0,45 |
| idem | 2 | 8 | 0,0 % | −1,513 % | −2,298 % | 0,00 |

**Verdict : PAS DE LANCEMENT** (0 critère sur 5).

**Lecture.**
1. **Période fortement baissière** : la référence (une entrée au hasard par paire et par heure) perd 0,85 à 1,13 % **brut** par trade de 6 h. Une stratégie à l'achat seulement ne peut presque rien y gagner.
2. Dans ce régime, la cassure fait **pire que le hasard** sur la partie 2 (−1,76 % contre −1,13 % brut) : les cassures sont surtout de fausses cassures, arrêtées au stop.
3. Les dix meilleures variantes de la partie 1 sont toutes négatives sur la partie 2 ; le plafond de poursuite (0,5 % ou 1 %) ne change rien (variantes identiques deux à deux).
4. Échantillon minuscule (8 à 21 trades) : aucune conclusion sur l'hypothèse elle-même, seulement sur ce régime. Relance hebdomadaire.


## Limites

Prix au `close`, glissement forfaitaire, pas de spread ; quelques jours de données au départ (≈ 4 j le 21/11), un seul régime de marché ; une cassure d'1 h est fréquente sur 40 paires, mais avec un cooldown de 4 h l'échantillon reste petit. Un passage des critères n'est qu'un feu vert pour le **paper**, jamais pour le LIVE (ADR-009).

## Tests

| ID | Vérification |
|---|---|
| T-R11 | features : `breakout_1h`, `range_1h`, `trend_4h` exacts sur une série connue ; `UNKNOWN` sous 54 minutes sur 60, sans minute `t−240` (± 10 min), ou si la dernière minute n'est pas `live` |
| T-R12 | aucune donnée postérieure à la décision (modifier une minute après `m` ne change pas le signal) |
| T-R13 | cooldown de 4 h et position unique par paire ; référence à une entrée par paire et par heure |
| T-R14 | verdict : les 5 critères évalués sur la partie 2 avec la variante choisie sur la partie 1 |
