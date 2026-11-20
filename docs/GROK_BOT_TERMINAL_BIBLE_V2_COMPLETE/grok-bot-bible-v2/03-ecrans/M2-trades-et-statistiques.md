# Écrans M2.5 — Trades (UI-03) et Statistiques (UI-12) (ADR-021)

> **0-hl — Bible V2.1 | 20 novembre 2026 | Statut : spécification de référence (source unique de vérité), validée le 2026-11-20**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Objectif : répondre sans requête SQL à « qu'a fait ce bot, avec quelle version de stratégie, et combien cela a-t-il rapporté **après frais** ? ». Aujourd'hui (M1), le Terminal ne montre que les 20 dernières clôtures et quatre compteurs ; la recette et l'étude passent par le serveur.

## Données et définitions

Aucune nouvelle table : tout se déduit des positions, des fills, des sessions et des instantanés d'equity existants.

- **Trade** = une position close. Sa **version de stratégie** est celle de sa session (`hl_bot_session.strategy_version_id` et empreinte), pas celle du bot aujourd'hui : un rattachement (M2.3) ne réécrit jamais l'historique.
- Par trade : ouverture, clôture, durée, paire, version, quantité, prix moyen d'entrée et de sortie (fills), **notionnel d'entrée** (Σ quantité × prix des fills d'entrée), frais, résultat net (`realized_pnl_net`), **résultat net en %** (net / notionnel d'entrée), multiple de R, motif de sortie, **glissement d'entrée** en bps (prix moyen d'entrée vs prix de référence du SIZER), identifiant de corrélation (trace).
- **Statistiques** sur un ensemble de trades (formules de 05-strategies/03) : nombre, gagnants, perdants, taux de gain ; gain moyen, perte moyenne ; **espérance** par trade (USD et %) ; **profit factor** (inconnu s'il n'y a aucune perte) ; résultat net, résultat brut (net + frais), frais, **part des frais dans le résultat brut** ; R moyen ; durée moyenne ; glissement d'entrée moyen ; sorties par motif ; meilleure et pire paire.
- **Courbe et drawdown** (par bot) : instantanés `OK` uniquement ; la courbe affichée est la **performance** `P = equity − capital apporté` (dépôts et retraits exclus, invariant UI-12), le drawdown maximal suit la définition d'EQUITY (06-agents/03).
- **Par version** : les mêmes statistiques regroupées par version de stratégie, pour comparer deux versions côte à côte ; une version avec moins de 20 trades est signalée « échantillon trop petit ».
- Les positions **ouvertes** sont listées à part avec leur marque ; leur résultat latent n'entre jamais dans les statistiques réalisées.

## API (`/api/v1`, Observer)

| Route | Effet |
|---|---|
| `GET /trades?bot_id=&since=&until=&instrument_id=&exit_reason=&strategy_version_id=&limit=&offset=` | trades filtrés, plus récents d'abord, avec les champs ci-dessus ; `bot_id` devient facultatif (tous les bots) ; réponse compatible avec M1 (champs ajoutés seulement) ; `total` pour la pagination |
| `GET /trades.csv?…` | mêmes filtres, CSV (séparateur `;`, décimales `.`, en-tête), au plus 10 000 lignes |
| `GET /stats?bot_id=&since=&until=&strategy_version_id=&instrument_id=` | statistiques de l'ensemble filtré + `by_version` + `by_pair` + `by_exit_reason` |
| `GET /stats/curve?bot_id=&since=&until=` | points de performance `P` (au plus 2 000, sous-échantillonnés régulièrement) et drawdown maximal avec sa date |

Valeurs monétaires en chaînes décimales ; statistique incalculable ⇒ `null`, jamais 0.

## Écrans

Navigation : Terminal · Bots · **Trades** · **Stats** · Strategies · Risk. Mêmes états communs que M1.

- **Trades (UI-03)** :
  - filtres : bot, période (aujourd'hui, 7 jours, 30 jours, tout, dates libres), paire, motif de sortie, version de stratégie — conservés dans l'URL ;
  - bandeau : résultat **réalisé** net, frais, nombre de trades, et à part le résultat **latent** des positions ouvertes ;
  - positions ouvertes (paire, quantité, entrée, marque, latent, âge) ;
  - table des trades (colonnes ci-dessus, triables, 50 par page) ; clic sur une ligne ⇒ **trace** du trade : features du candidat, critères du RUG CHECK, sizing (limite retenue), fills d'entrée et de sortie, motif — à partir de `/events/trace/{correlation_id}` ;
  - bouton *Export CSV* (mêmes filtres).
- **Stats (UI-12)** :
  - mêmes filtres de bot et de période ;
  - cartes : résultat net, résultat brut, frais (et part dans le brut), taux de gain, espérance, profit factor, drawdown maximal, durée moyenne ;
  - courbe de performance (SVG, sans bibliothèque de graphiques) avec le point de drawdown maximal ;
  - tableau **par version** (une colonne par version, les mêmes métriques ; « échantillon trop petit » sous 20 trades) ;
  - tableau par paire (net, trades, taux de gain) et répartition des sorties par motif.

## Hors M2.5

Comparaison paper/live, attribution fine du glissement de sortie, export de la courbe, statistiques des backtests dans l'écran (leur rapport reste celui de M2.2), alertes de dérive.

## Tests

| ID | Vérification |
|---|---|
| T-M2.5-01 | un trade porte la version de **sa session** même après un rattachement du bot à une autre version |
| T-M2.5-02 | notionnel d'entrée, prix moyens, net %, glissement d'entrée calculés depuis les fills ; filtres et pagination ; `bot_id` absent ⇒ tous les bots |
| T-M2.5-03 | statistiques : formules sur un jeu connu (gagnants/perdants, espérance, profit factor `null` sans perte, part des frais) ; regroupements par version, paire, motif |
| T-M2.5-04 | courbe : dépôts exclus (un dépôt ne crée ni gain ni drawdown), instantanés `STALE` ignorés, sous-échantillonnage borné |
| T-M2.5-05 | CSV : mêmes lignes que l'API pour les mêmes filtres, décimales exactes |
| T-M2.5-06 | positions ouvertes et latent exclus des statistiques réalisées ; aucune valeur inconnue affichée comme 0 |
| T-M2.5-07 (UI) | filtres dans l'URL, tri, pagination, trace au clic, export ; cartes et tableau par version (« échantillon trop petit »), courbe avec point de drawdown ; états `LOADING`/`EMPTY`/`ERROR` |
