# 0-hl — BIBLE DE DÉVELOPPEMENT V2.1

**Date :** 17 novembre 2026 (V2.1 : décisions ADR-000 à ADR-010)  
**Format :** collection de chapitres Markdown, contrats d'exemple, maquettes PNG et références visuelles  
**Statut :** spécification de référence et **source unique de vérité** du produit 0-hl (ADR-000) ; produit nouveau, **pas** rétro-ingénierie certifiée de l'application originale (« Grok Bot Terminal », observée sur les captures).

**Règle de travail :** toute décision ou tout changement est d'abord écrit ici (ADR + chapitres), puis implémenté.

## Accès rapide

- [Guide de lecture](00-guide/00-lecture.md)
- [**Registre des décisions (ADR)**](00-guide/02-decisions.md) — tranche les points `[OPEN]` ; prime sur les autres chapitres
- [Sommaire des chapitres](00-guide/01-sommaire.md)
- [Style visuel et capture canonique](02-design/03-design-system.md)
- [Spécifications des 15 écrans](03-ecrans/)
- [Stratégies et théorie quantitative](05-strategies/)
- [Six agents](06-agents/)
- [Moteur de risque et d'exécution](08-risque-execution/)
- [API / contrats](contracts/)
- [Plan de validation](12-tests/01-plan-de-recette.md)
- [Roadmap et consignes agents dev](13-roadmap/01-plan-execution.md)

## Vues PNG

- [UI-01 — Live Terminal](assets/views/UI-01.png)
- [UI-02 — Bots & Sessions](assets/views/UI-02.png)
- [UI-03 — Portfolio & Trades](assets/views/UI-03.png)
- [UI-04 — Strategy Manager](assets/views/UI-04.png)
- [UI-05 — Strategy Editor](assets/views/UI-05.png)
- [UI-06 — Risk Manager](assets/views/UI-06.png)
- [UI-07 — Execution Settings](assets/views/UI-07.png)
- [UI-08 — Agent Manager](assets/views/UI-08.png)
- [UI-09 — Market Scanner](assets/views/UI-09.png)
- [UI-10 — Data Sources](assets/views/UI-10.png)
- [UI-11 — Backtesting & Simulation](assets/views/UI-11.png)
- [UI-12 — Performance Analytics](assets/views/UI-12.png)
- [UI-13 — Event Logs & Audit](assets/views/UI-13.png)
- [UI-14 — Alerts & Monitoring](assets/views/UI-14.png)
- [UI-15 — System & Security](assets/views/UI-15.png)

## Documents antérieurs

La V1 demeure disponible sous `original/BIBLE_V1.md` pour comparaison. La V2 réorganise ses objectifs, approfondit notamment le risk engine, le ledger, le modèle de simulation, les contrats, les tests et les états d'interface.

## Limites des livrables

Le dossier ne contient pas 15 applications fonctionnelles ni une implémentation de trading opérationnelle. Les PNG constituent des maquettes visuelles et la troisième capture un étalon visuel pour le terminal. Les fichiers OpenAPI / SQL / JSON Schema sont des **amorces de contrat**, à étendre et valider en CI. La rentabilité et le caractère réel du trading du logiciel figurant sur les captures sont inconnus.
