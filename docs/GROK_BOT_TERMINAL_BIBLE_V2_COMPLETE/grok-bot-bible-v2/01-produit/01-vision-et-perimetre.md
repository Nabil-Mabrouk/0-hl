# Vision produit, périmètre et non-objectifs

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Proposition de valeur

Le système est un **poste de pilotage explicable** : scanner de signaux, contrôle anti-fraude, taille des positions, exécution, journalisation, comptabilité et administration unifiée. Il sépare la collecte des données, la logique de stratégie, l'autorisation de risque et la transmission des ordres. L'interface ne doit jamais inventer une décision absente du journal événementiel.

## Faits observés dans les captures

[OBS] Présence des six rôles `SCANNER`, `RUG CHECK`, `SIZER`, `EXECUTOR`, `LOGGER`, `EQUITY`; token cards, messages de volume, LP lock, `dev wallet`, `bundled launch`, stop et take-profit en multiples de R. [OBS] Des badges `paper off · live` sont visibles, mais ne prouvent pas la réalité d'ordres. [INF] Le marché visé semble être principalement celui des tokens nouvellement listés et des mouvements de momentum. [OPEN] Blockchain, fournisseur, latence, gestion effective de clés, nature des agents IA : inconnus.

## Cadrage 0-hl (V2.1)

Le produit **0-hl** (ADR-001) cible en premier lieu les paires **Spot cotées USD de Kraken** (ADR-002, ADR-003), en long uniquement, et non les tokens DEX récemment lancés. Le marché des nouveaux tokens DEX reste une extension possible par ajout d'un adaptateur de venue. Le premier jalon est une **tranche verticale paper** sur données réelles (ADR-008) ; le LIVE Kraken est prévu par conception mais désactivé (ADR-009).

## Périmètre produit

- **V0 / Prototype graphique** : exactitude de rendu de la capture 3, jeu de données figé, animations et interaction de lecture uniquement.
- **MVP / Paper-only** : collecte réelle ou rejouée, stratégie declarative, agents algorithmiques, journal persistant, métriques d'équité simulées, administration et RBAC.
- **V1 opérationnelle** : multi-bots, surveillance fiable, relecture d'événements, reconciliations, alertes, tests de charge, évaluation scientifique et exports.
- **LIVE conditionnel** (Kraken Spot en premier, ADR-009) : activation séparée, comptes dédiés sans droit de retrait, validation de conformité, tests de kill switch et runbooks validés.

## Non-objectifs

Pas de garde de fonds pour des tiers, absence de trading sur marge au MVP, pas de gestion de fonds de clients, pas de promesse de rendement, pas de déduction d'un signal à partir d'un simple transfert on-chain, pas de fonctionnement live sans preuve d'exécution et rapprochement.

## Livrables produit

Une application UI avec 15 écrans ; un moteur d'exécution paper ; un moteur événementiel et des connecteurs ; un laboratoire de stratégie ; un back-office avec validation ; documentation de sécurité ; tests et observabilité. L'historique doit permettre de reconstruire l'état d'un bot à un instant T.
