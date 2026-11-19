# Recette du jalon M1 — outillage et procédure (M1.6)

> **0-hl — Bible V2.1 | 18 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Ce chapitre outille le **gate de fin M1** ([13-roadmap](../13-roadmap/01-plan-execution.md)) : un bot `PAPER` tourne 24 h sur données Kraken réelles sans intervention ; chaque trade est reconstructible depuis le journal ; ledger réconcilié ; redéploiement en cours de session sans doublon. Il complète le [plan de recette](01-plan-de-recette.md).

## Script de recette

`python -m app.domain.trading.recette [--bot NOM] [--hours 24] [--json]`, exécuté **dans le conteneur worker** en production (`docker exec 0-hl_worker python -m app.domain.trading.recette`). Il est **en lecture seule** : toute vérification qui écrirait (réconciliation) s'exécute dans une transaction annulée. Code de sortie `0` si tous les critères bloquants sont `OK`, `1` sinon.

| ID | Critère | Bloquant |
|---|---|---|
| R-01 | ledger équilibré : chaque transaction somme à zéro par actif | oui |
| R-02 | réconciliation à l'instant : inventaire = positions, cash ≥ 0, aucune réservation résiduelle, fills = écritures | oui |
| R-03 | jamais plus d'un ordre d'entrée par intention ; jamais deux intentions pour une même autorisation | oui |
| R-04 | chaque position close est reconstructible : `CANDIDATE_DETECTED`, `RISK_PASSED`, `POSITION_SIZED`, `ORDER_FILLED`, `POSITION_OPENED`, `POSITION_CLOSED` sous un même `correlation_id` | oui |
| R-05 | journal intègre : checksum valide pour chaque événement, `aggregate_sequence` contigu par agrégat | oui |
| R-06 | aucun `RECONCILIATION_FAILED` et aucune transition vers `DEGRADED` ou `FAILED` sur la fenêtre | oui |
| R-07 | aucune commande `PENDING` depuis plus de 60 s | oui |
| R-08 | session continue : session `RUNNING` ouverte depuis au moins `--hours` | oui pour le gate |
| R-09 | continuité du worker sur la fenêtre : redémarrages comptés (`hl.worker` via `worker_runs`), connexions perdues (`PROVIDER_DEGRADED`), resyncs | information |
| R-10 | couverture des données : part des minutes `live` présentes par paire de l'univers | information |
| R-11 | activité : candidats, entrées, sorties par motif, wins/losses, PnL net, frais, R moyen | information |
| R-12 | **diagnostic STRAT-01** : cumul des `fails` par condition et des motifs `UNKNOWN` des `SCAN_CYCLE_COMPLETED` de la fenêtre | information (décision v0.3.0) |

Le rapport texte liste chaque critère avec `OK` / `KO` / `INFO` et les détails ; `--json` produit le même contenu en JSON (montants en chaînes décimales).

## Test de redémarrage (QA-20, INV-04)

Test automatisé : un moteur de décision traite une fenêtre jusqu'au sizing, puis est remplacé par une **nouvelle instance** (redémarrage : tout l'état en mémoire est perdu) sur la même base. Attendu : aucun second candidat pour la fenêtre, aucune seconde intention ni second ordre pour l'autorisation, l'autorisation non exécutée expire, la recette R-01 à R-05 reste `OK`.

## Procédure de la fenêtre de 24 h

1. Prérequis : écrans M1.5 en ligne, bot `paper-kraken-01` `RUNNING`.
2. **Gel des déploiements** pendant 24 h (tout push redémarre le worker et remet en cause « sans intervention »).
3. À H+24 : exécuter le script ; joindre le rapport au ticket de recette.
4. Gate M1 franchi si R-01 à R-08 sont `OK`. Les critères d'information alimentent la décision STRAT-01 v0.3.0 (R-12) et le suivi d'exploitation.

## Fidélité visuelle (reportée)

La comparaison Playwright de la réplique 550×688 avec la capture 3 (02-design/03, ADR-016) sera outillée dans un dossier séparé `tools/visual/` (son propre `package.json`, pour ne pas modifier celui du châssis frontend) ; non bloquante pour le gate M1.
