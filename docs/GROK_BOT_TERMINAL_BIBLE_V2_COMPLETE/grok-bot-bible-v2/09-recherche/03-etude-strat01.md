# Étude STRAT-01 hors production (ADR-020)

> **0-hl — Bible V2.1 | 20 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Étude ponctuelle, préalable à toute STRAT-01 v0.3.0 : sur les données de marché **déjà enregistrées** (`hl_trade_minute`), rejouer le signal d'entrée de STRAT-01 et mesurer, **frais compris**, le résultat de variantes de seuils et de sorties. C'est une première forme du laboratoire de 09-recherche/02, sans le moteur de backtest complet (UI-11).

## Constat motivant (recettes du 2026-11-19 et du 2026-11-20)

v0.2.0 : 103 trades en ~2 jours, 14 gagnants ; résultat négatif même avant frais le second jour ; 101 sorties sur 103 à la limite de 60 min (stop −3 % et objectif +6 % presque jamais atteints) ; frais ≈ 0,8 % par aller-retour.

## Exécution

`python -m app.domain.trading.research.strat01_study [--days N] [--json]` dans le conteneur worker (`docker exec 0-hl_worker …`), **lecture seule**. Le module n'est jamais importé par le worker ni par l'API.

## Méthode

- **Données** : pour chaque paire de l'univers, minutes `live` prioritaires puis `backfill` (même chargement que le SCANNER).
- **Signal** : à chaque minute close `m` et pour chaque paire, les features de la fenêtre `[m−5 min, m)` sont calculées par **le code du SCANNER** (`window_features`) ; aucune donnée postérieure à `m` n'est lue pour décider (INV-03).
- **Conditions d'entrée** : celles de STRAT-01 avec seuils variables — `volume_spike ≥ s`, `buy_notional_share ≥ b`, `window_notional_usd ≥ n`, `window_trade_count ≥ t`, `price_change_5m > 0`. Le spread n'est **pas** rejoué (aucun historique de carnet ; il ne bloquait que ~4 % des évaluations) : condition supposée vraie.
- **Prix d'entrée** : dernier `close` de la fenêtre × (1 + 0,10 % de glissement supposé), frais taker 0,40 %.
- **Sortie** (minutes suivantes uniquement) : stop à `−x %`, objectif à `k × x` (en R), sortie au temps après `H` minutes au `close`. Si stop et objectif sont touchés dans la même minute, le **stop** est retenu (hypothèse pessimiste). Sortie au stop avec 0,10 % de glissement supplémentaire ; frais taker 0,40 %. Une minute sans trade garde le dernier prix ; une **minute manquante** (trou de données) pendant la détention exclut le trade (comptabilisé comme exclu).
- **Contraintes** : une position à la fois par paire, cooldown de 30 min après sortie. Pas de sizing : résultats en **% par trade**.
- **Grille** : `s ∈ {3, 4, 6, 8}`, `b ∈ {0,60 ; 0,70}`, `(n, t) ∈ {(25 000, 25), (50 000, 40), (100 000, 60)}`, `H ∈ {5, 15, 30, 60}` min, `x ∈ {1, 2, 3} %`, `k ∈ {1, 2, aucun}`.
- **Référence** (05-strategies/02 « comparaison à un baseline simple ») : les mêmes sorties appliquées à **toutes** les fenêtres évaluables sans filtre d'entrée (dérive du marché moins les frais).
- **Contre le sur-ajustement** (09-recherche/01) : la période est coupée en deux selon le temps (60 % / 40 %). Les variantes sont classées sur la première partie (au moins 20 trades) ; le rapport donne leur résultat **sur la seconde partie**, ainsi que le nombre de variantes essayées.

## Sorties

Pour v0.2.0 (s = 4, b = 0,60, n = 50 000, t = 40, H = 60, x = 3, k = 2), la référence et les 10 meilleures variantes : nombre de trades, trades exclus, taux de gain, résultat moyen brut et net par trade, profit factor, sur chaque partie de la période.

## Limites (à lire avant toute conclusion)

Pas de carnet historique (prix au `close`, glissement forfaitaire) ; quelques jours de données seulement ; régime de marché unique ; plusieurs centaines de variantes testées (risque de faux positif) ; aucune variante n'est publiée sur la seule base de cette étude sans validation de l'opérateur (ADR-000), et le LIVE reste exclu (ADR-009).

## Tests

| ID | Vérification |
|---|---|
| T-R01 | aucune donnée postérieure à la décision : modifier une minute après `m` ne change pas le signal à `m` |
| T-R02 | sorties : stop prioritaire quand stop et objectif sont dans la même minute ; sortie au temps au `close` ; frais et glissement appliqués |
| T-R03 | trou de données pendant la détention ⇒ trade exclu ; cooldown et position unique par paire respectés |
| T-R04 | découpage temporel et classement sur la première partie, évaluation sur la seconde |
