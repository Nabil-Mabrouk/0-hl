# Recette du jalon M2 — tests de parcours, rapport et procédure de gate (M2.6, ADR-021)

> **0-hl — Bible V2.1 | 20 novembre 2026 | Statut : spécification de référence (source unique de vérité), validée le 2026-11-20**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

## Rappel du gate M2 (13-roadmap/01)

« Une nouvelle stratégie est décrite, publiée en version, backtestée sur au moins 4 semaines avec un résultat net reproductible jugé sur une période non utilisée pour la choisir, puis lancée sur un bot paper dédié à côté d'un autre bot, sans écrire de code ; les résultats des deux bots se comparent dans UI-12. »

Le gate porte sur une **capacité** (le laboratoire fonctionne de bout en bout), pas sur la rentabilité : une stratégie perdante bien mesurée le franchit. Un résultat positif ne serait exigé que pour un jalon ultérieur visant le LIVE (ADR-009).

## Ce qui est déjà couvert par les lots

| Exigence M2.6 (roadmap) | Couverture existante |
|---|---|
| backtest déterministe | T-M2.2-03 |
| équivalence décision bot / backtest | T-M2.2-02 |
| immutabilité des versions | T-M2.3-01, T-M1.1 (INV-06) |
| isolation entre bots | T-M2.4-04 |

M2.6 ajoute les trois éléments suivants.

## A. Test de parcours automatique (local, sans réseau)

Un seul test enchaîne tout le jalon par l'API, sur des minutes et des instantanés de carnet synthétiques :

1. dupliquer STRAT-01 en un brouillon, en modifier un seuil, le publier (motif, référence de backtest) ;
2. lancer le backtest de cette version sur les données ; relancer à l'identique ⇒ même empreinte de données et même rapport ;
3. créer un bot dédié avec cette version et un capital, le démarrer ; le bot d'amorçage tourne à côté avec l'ancienne version ;
4. faire tourner le moteur sur les mêmes données jusqu'à au moins un trade par bot ;
5. vérifier dans `/trades` et `/stats` que chaque trade porte la version de sa session, et que `by_version` compare les deux versions ;
6. vérifier les audits de chaque étape (acteur, motif) et que rien n'a demandé de code.

## B. Rapport de recette M2 (lecture seule)

`python -m app.domain.trading.recette_m2 [--json]` dans le conteneur worker. Code retour 0 si tous les critères bloquants sont OK.

| ID | Critère | Bloquant |
|---|---|---|
| R2-01 | chaque version publiée de stratégie et de policy a une empreinte égale au hash recalculé de sa config | oui |
| R2-02 | chaque session porte l'empreinte de stratégie et de policy de ses versions | oui |
| R2-03 | au plus 5 bots non `STOPPED` ; tous `PAPER` | oui |
| R2-04 | pour **chaque** bot : ledger équilibré et réconciliation OK (R-01 et R-02 de la recette M1, par bot) | oui |
| R2-05 | aucune position, intention ou écriture de ledger rattachée à la session ou au bot d'un autre bot | oui |
| R2-06 | chaque publication, retrait, rattachement et création de bot est audité avec acteur et motif | oui |
| R2-07 | historique disponible : jours couverts par les minutes et par les instantanés de carnet, couverture moyenne des instantanés sur l'univers, **date à partir de laquelle 28 jours d'instantanés sont disponibles** | non (INFO) |
| R2-08 | publications de policy en attente depuis plus de 24 h | non (INFO) |
| R2-09 | part des instantanés d'equity où une position est valorisée au-delà du carnet visible (ADR-022) sur 24 h | non (INFO) |
| R2-10 | par bot : statut, version, trades et résultat net sur 7 jours | non (INFO) |

## C. Procédure de gate (opérateur, sans code)

À dérouler lorsque R2-07 indique au moins 28 jours d'instantanés.

1. **Hypothèse écrite** (09-recherche/02) : en motif du brouillon, ce que la variante doit améliorer et la métrique jugée d'avance — résultat net moyen par trade après frais sur la **partie 2** du découpage.
2. **Brouillon** dans l'éditeur (UI-05), à partir d'une version publiée.
3. **Backtest** sur au moins 28 jours :
   `backtest --strategy FICHIER_OU_VERSION --from J-28 --to J --split 0.6 --out rapport.json`, puis le même avec `--fee-multiplier 2`.
   On consigne la partie 2 (trades, net moyen, profit factor), la comparaison à la référence sans filtre et l'empreinte des données.
4. **Reproductibilité** : relancer la même commande ⇒ même empreinte de données et même rapport.
5. **Publication** (UI-04) avec en `backtest_ref` l'empreinte de données du rapport.
6. **Bot dédié** (*New bot*, UI-02), 10 000 USD, `START`, à côté de `paper-kraken-01` ou d'un autre bot, pendant au moins 7 jours.
7. **Comparaison** dans UI-12 (Stats, tableau par version) ; le verdict est consigné dans le registre ADR :
   - le gate est franchi si les étapes 1 à 7 ont été faites sans code ;
   - on ajoute l'écart entre backtest et paper sur la même période.

## Hors M2.6

Automatisation de la procédure de gate, registre d'expériences en base et écran UI-11, critères de rentabilité.

## Tests

| ID | Vérification |
|---|---|
| T-M2.6-01 | parcours A complet : versions, backtest reproductible, deux bots, trades attribués à leur version, comparaison par version, audits |
| T-M2.6-02 | R2-01 : une config modifiée en base sans recalcul d'empreinte ⇒ KO bloquant |
| T-M2.6-03 | R2-04 et R2-05 : une écriture de ledger déséquilibrée sur un second bot ⇒ KO pour ce bot seulement ; une position dont la session appartient à un autre bot ⇒ KO |
| T-M2.6-04 | R2-06 : une publication sans motif dans l'audit ⇒ KO |
| T-M2.6-05 | R2-07 : jours d'historique et date d'éligibilité au gate calculés correctement ; base vide ⇒ valeurs `—`, jamais 0 jour inventé |
| T-M2.6-06 | le rapport est en lecture seule : aucune ligne ajoutée ni modifiée |
